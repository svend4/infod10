"""Opt-in local bearer governance, using an existing Store transaction.

These credentials identify holders of locally issued capabilities. They do not
authenticate human identities, proposal authors, or a remote service. A trusted
owner of the SQLite file can bypass this Python API.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import secrets
import sqlite3
import time
from typing import Any

from .store import CLAIM_ID, HASH, WorkbenchError, canonical, digest, reason, safe_file_id


RULES_VERSION = "governance-1"
MAX_GRANT_SECONDS = 365 * 24 * 60 * 60
IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
TOKEN = re.compile(r"awg_[A-Za-z0-9_-]{43}\Z")

SCHEMA = """
CREATE TABLE IF NOT EXISTS governance_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
    administrator_id TEXT NOT NULL, enabled_at REAL NOT NULL,
    activation_revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_policies (
    generation INTEGER PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL,
    created_at REAL NOT NULL, administrator_grant_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_grants (
    grant_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL, role TEXT NOT NULL,
    artifact_ids TEXT NOT NULL, expires_at REAL, created_at REAL NOT NULL,
    issuer_grant_id TEXT, credential_hash TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS governance_revocations (
    grant_id TEXT PRIMARY KEY REFERENCES governance_grants(grant_id),
    revoked_at REAL NOT NULL, administrator_grant_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_decisions (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL UNIQUE,
    proposal_id TEXT NOT NULL REFERENCES proposals(id), artifact_id TEXT NOT NULL,
    principal_id TEXT NOT NULL, grant_id TEXT NOT NULL REFERENCES governance_grants(grant_id),
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS governance_heads (
    proposal_id TEXT NOT NULL REFERENCES proposals(id), artifact_id TEXT NOT NULL,
    principal_id TEXT NOT NULL, decision_id TEXT NOT NULL REFERENCES governance_decisions(decision_id),
    PRIMARY KEY(proposal_id,artifact_id,principal_id)
);
CREATE TABLE IF NOT EXISTS governance_operations (
    operation_id TEXT PRIMARY KEY, action TEXT NOT NULL,
    request_digest TEXT NOT NULL, result_id TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS governance_operations_result
    ON governance_operations(action,result_id);
CREATE TABLE IF NOT EXISTS governance_receipts (
    proposal_id TEXT PRIMARY KEY REFERENCES proposals(id), receipt_id TEXT NOT NULL UNIQUE,
    body TEXT NOT NULL
);
"""


def new_credential() -> str:
    """Return a secret; callers must store it privately before registration."""
    return "awg_" + secrets.token_urlsafe(32)


def _credential_hash(value: Any) -> str:
    if not isinstance(value, str) or not TOKEN.fullmatch(value):
        raise WorkbenchError("governance_unauthorized", "A valid local bearer credential is required")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _identifier(value: Any, label: str) -> str:
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise WorkbenchError("invalid_governance_input", f"Invalid {label}")
    return value


def _policy(value: Any) -> dict[str, Any]:
    if (not isinstance(value, dict)
            or set(value) != {"schema_version", "minimum_reviewers", "separate_coordinator"}
            or type(value["schema_version"]) is not int or value["schema_version"] != 1
            or type(value["minimum_reviewers"]) is not int
            or not 1 <= value["minimum_reviewers"] <= 16
            or type(value["separate_coordinator"]) is not bool):
        raise WorkbenchError("invalid_governance_policy", "Expected schema 1, 1..16 reviewers and a coordinator separation boolean")
    return dict(value)


def _scope(value: Any) -> list[str]:
    if (not isinstance(value, list) or not 1 <= len(value) <= 100
            or any(not isinstance(key, str) or not (
                CLAIM_ID.fullmatch(key) or safe_file_id(key, "document") or safe_file_id(key, "code")
            ) for key in value)
            or len(set(value)) != len(value)):
        raise WorkbenchError("invalid_governance_input", "An exact allowlist of 1..100 distinct safe artifact IDs is required")
    return sorted(value)


def _event(connection: sqlite3.Connection, event: str, body: Any) -> None:
    connection.execute("INSERT INTO events(type,data) VALUES (?,?)", (event, canonical(body)))


def enabled(connection: sqlite3.Connection) -> bool:
    return connection.execute("SELECT 1 FROM governance_settings WHERE singleton=1").fetchone() is not None


def current_policy(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM governance_policies ORDER BY generation DESC LIMIT 1").fetchone()
    if row is None:
        raise WorkbenchError("governance_disabled", "Enable governance on a bootstrapped store first")
    return {"generation": row["generation"], "policy": json.loads(row["body"]),
            "policy_digest": row["digest"], "created_at": row["created_at"],
            "administrator_grant_id": row["administrator_grant_id"]}


def _grant_public(connection: sqlite3.Connection, row: sqlite3.Row, now: float) -> dict[str, Any]:
    revoked = connection.execute("SELECT * FROM governance_revocations WHERE grant_id=?", (row["grant_id"],)).fetchone()
    operation = connection.execute("SELECT operation_id FROM governance_operations WHERE action='grant' AND result_id=?",
                                   (row["grant_id"],)).fetchone()
    return {"grant_id": row["grant_id"], "principal_id": row["principal_id"],
            "role": row["role"], "artifact_ids": json.loads(row["artifact_ids"]),
            "expires_at": row["expires_at"], "created_at": row["created_at"],
            "issuer_grant_id": row["issuer_grant_id"], "operation_id": operation[0] if operation else None,
            "revoked": bool(revoked),
            "revoked_at": revoked["revoked_at"] if revoked else None,
            "expired": row["expires_at"] is not None and row["expires_at"] <= now}


def authorize(connection: sqlite3.Connection, credential: Any, role: str,
              artifact_ids: list[str] | None = None, *, now: float | None = None) -> dict[str, Any]:
    """Authenticate after acquiring the transaction lock; never return a hash."""
    if not enabled(connection):
        raise WorkbenchError("governance_disabled", "Governance is not enabled")
    sampled = time.time() if now is None else now
    credential_hash = _credential_hash(credential)
    row = connection.execute("SELECT * FROM governance_grants WHERE credential_hash=?", (credential_hash,)).fetchone()
    if row is None:
        raise WorkbenchError("governance_unauthorized", "Credential is not authorized for this action")
    public = _grant_public(connection, row, sampled)
    if (public["role"] != role or public["revoked"] or public["expired"]
            or not set(artifact_ids or []).issubset(public["artifact_ids"])):
        raise WorkbenchError("governance_unauthorized", "Credential is not authorized for this action")
    return public


def enable(connection: sqlite3.Connection, policy: Any, administrator_id: Any,
           credential: Any) -> dict[str, Any]:
    if enabled(connection):
        raise WorkbenchError("governance_already_enabled", "Governance activation is one-way")
    revision = connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
    if revision is None or int(revision[0]) < 1 or connection.execute("SELECT 1 FROM artifacts").fetchone() is None:
        raise WorkbenchError("governance_requires_bootstrap", "Bootstrap artifacts before activating governance")
    validated = _policy(policy)
    principal = _identifier(administrator_id, "administrator ID")
    credential_hash = _credential_hash(credential)
    now, grant_id = time.time(), "gvg_" + secrets.token_hex(16)
    connection.execute("INSERT INTO governance_settings VALUES (1,?,?,?)", (principal, now, int(revision[0])))
    connection.execute("INSERT INTO governance_grants VALUES (?,?,?,?,?,?,?,?)",
                       (grant_id, principal, "administrator", "[]", None, now, None, credential_hash))
    connection.execute("INSERT INTO governance_policies VALUES (?,?,?,?,?)",
                       (1, canonical(validated), digest(validated), now, grant_id))
    result = {"enabled": True, "rules_version": RULES_VERSION,
              "policy_generation": 1, "policy_digest": digest(validated), "policy": validated,
              "activation_revision": int(revision[0]), "enabled_at": now,
              "administrator": _grant_public(connection, connection.execute(
                  "SELECT * FROM governance_grants WHERE grant_id=?", (grant_id,)).fetchone(), now)}
    _event(connection, "governance_enabled", result)
    return result


def _operation(connection: sqlite3.Connection, operation_id: Any, action: str,
               request: Any) -> sqlite3.Row | None:
    _identifier(operation_id, "operation ID")
    row = connection.execute("SELECT * FROM governance_operations WHERE operation_id=?", (operation_id,)).fetchone()
    if row and (row["action"] != action or row["request_digest"] != digest(request)):
        raise WorkbenchError("idempotency_conflict", "Operation ID already names different governance input")
    return row


def grant(connection: sqlite3.Connection, principal_id: Any, role: Any, artifact_ids: Any,
          expires_at: Any, *, credential: Any, new_credential: Any,
          operation_id: Any) -> dict[str, Any]:
    now = time.time()
    administrator = authorize(connection, credential, "administrator", now=now)
    principal, scope = _identifier(principal_id, "principal ID"), _scope(artifact_ids)
    if not isinstance(role, str) or role not in {"reviewer", "coordinator", "analyst", "doc_author", "code_author", "integrator"}:
        raise WorkbenchError("invalid_governance_input", "Unknown reviewer, coordinator or author role")
    if type(expires_at) not in {int, float} or (type(expires_at) is float and not math.isfinite(expires_at)):
        raise WorkbenchError("invalid_governance_input", "Grant expiry must be finite epoch seconds")
    token_hash = _credential_hash(new_credential)
    from . import transfer_origin
    transfer_origin.enforce_credential_fresh(connection, token_hash)
    request = {"principal_id": principal, "role": role, "artifact_ids": scope,
               "expires_at": expires_at, "credential_hash": token_hash,
               "administrator_grant_id": administrator["grant_id"]}
    old = _operation(connection, operation_id, "grant", request)
    if old:
        row = connection.execute("SELECT * FROM governance_grants WHERE grant_id=?", (old["result_id"],)).fetchone()
        return {**_grant_public(connection, row, now), "replayed": True}
    if not now < expires_at <= now + MAX_GRANT_SECONDS:
        raise WorkbenchError("invalid_governance_input", "Grant expiry must be in the future and at most 365 days away")
    if connection.execute("SELECT 1 FROM governance_grants WHERE credential_hash=?", (token_hash,)).fetchone():
        raise WorkbenchError("governance_credential_conflict", "Credential has already been registered")
    grant_id = "gvg_" + secrets.token_hex(16)
    connection.execute("INSERT INTO governance_grants VALUES (?,?,?,?,?,?,?,?)",
                       (grant_id, principal, role, canonical(scope), expires_at, now, administrator["grant_id"], token_hash))
    connection.execute("INSERT INTO governance_operations VALUES (?,?,?,?)",
                       (operation_id, "grant", digest(request), grant_id))
    result = {**_grant_public(connection, connection.execute("SELECT * FROM governance_grants WHERE grant_id=?", (grant_id,)).fetchone(), now), "replayed": False}
    _event(connection, "governance_grant_issued", result)
    return result


def revoke(connection: sqlite3.Connection, grant_id: Any, *, credential: Any) -> dict[str, Any]:
    now = time.time()
    administrator = authorize(connection, credential, "administrator", now=now)
    if not isinstance(grant_id, str) or not re.fullmatch(r"gvg_[0-9a-f]{32}", grant_id):
        raise WorkbenchError("invalid_governance_input", "Invalid grant ID")
    row = connection.execute("SELECT * FROM governance_grants WHERE grant_id=?", (grant_id,)).fetchone()
    if row is None:
        raise WorkbenchError("unknown_governance_grant", "Grant does not exist")
    if row["role"] == "administrator":
        raise WorkbenchError("governance_administrator_protected", "The initial administrator cannot be revoked through this API")
    existing = connection.execute("SELECT 1 FROM governance_revocations WHERE grant_id=?", (grant_id,)).fetchone()
    if not existing:
        connection.execute("INSERT INTO governance_revocations VALUES (?,?,?)", (grant_id, now, administrator["grant_id"]))
    result = {**_grant_public(connection, row, now), "replayed": bool(existing)}
    if not existing:
        _event(connection, "governance_grant_revoked", result)
    return result


def set_policy(connection: sqlite3.Connection, policy: Any, *, credential: Any,
               expected_generation: Any) -> dict[str, Any]:
    administrator = authorize(connection, credential, "administrator")
    current = current_policy(connection)
    validated = _policy(policy)
    if type(expected_generation) is not int or expected_generation != current["generation"]:
        raise WorkbenchError("governance_policy_changed", "Expected policy generation does not match current policy")
    generation = current["generation"] + 1
    connection.execute("INSERT INTO governance_policies VALUES (?,?,?,?,?)",
                       (generation, canonical(validated), digest(validated), time.time(), administrator["grant_id"]))
    result = current_policy(connection)
    result["policy_generation"] = result["generation"]
    _event(connection, "governance_policy_changed", result)
    return result


def write_ids(proposal: sqlite3.Row) -> list[str]:
    return sorted(body["id"] for body in json.loads(proposal["payload"])["writes"])


def _content_pin(packet: dict[str, Any]) -> dict[str, Any]:
    """The immutable content references carried by a vote or receipt."""
    return {key: packet[key] for key in (
        "packet_id", "packet_digest", "content_policy_generation", "content_policy_digest")}


def _content_decision_matches(body: dict[str, Any], packet: dict[str, Any]) -> bool:
    return (body.get("content_packet_digest") == packet["packet_digest"]
            and body.get("content_packet") == _content_pin(packet))


def decide(connection: sqlite3.Connection, proposal: sqlite3.Row, artifact_id: Any,
           verdict: Any, rationale: Any, *, credential: Any,
           expected_proposal_digest: Any, expected_policy_generation: Any,
           expected_previous_decision: Any, operation_id: Any,
           expected_content_packet_digest: Any = None) -> dict[str, Any]:
    if not isinstance(artifact_id, str) or artifact_id not in write_ids(proposal):
        raise WorkbenchError("invalid_governance_input", "Decision must identify one exact proposal write")
    reviewer = authorize(connection, credential, "reviewer", [artifact_id])
    from . import authorship
    if authorship.enabled(connection):
        authorship.separate(connection, proposal["id"], reviewer["principal_id"])
    if not isinstance(verdict, str) or verdict not in {"approve", "request_changes", "abstain"}:
        raise WorkbenchError("invalid_governance_input", "Invalid governance verdict")
    if (not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 4000
            or any(0xD800 <= ord(char) <= 0xDFFF for char in rationale)):
        raise WorkbenchError("invalid_governance_input", "Rationale requires 1..4000 Unicode characters")
    if not isinstance(expected_proposal_digest, str) or not HASH.fullmatch(expected_proposal_digest):
        raise WorkbenchError("invalid_governance_input", "Expected proposal digest is required")
    if type(expected_policy_generation) is not int or expected_policy_generation < 1:
        raise WorkbenchError("invalid_governance_input", "Expected policy generation is required")
    if expected_previous_decision is not None and (not isinstance(expected_previous_decision, str)
            or not re.fullmatch(r"gvd_[0-9a-f]{64}", expected_previous_decision)):
        raise WorkbenchError("invalid_governance_input", "Previous decision must be null or a decision ID")
    request = {"proposal_id": proposal["id"], "proposal_digest": expected_proposal_digest,
               "policy_generation": expected_policy_generation, "artifact_id": artifact_id,
               "verdict": verdict, "rationale": rationale,
               "previous_decision_id": expected_previous_decision,
               "principal_id": reviewer["principal_id"], "grant_id": reviewer["grant_id"]}
    from . import content_gate
    content_enabled = content_gate.enabled(connection)
    if content_enabled and expected_content_packet_digest is not None:
        if (not isinstance(expected_content_packet_digest, str)
                or not HASH.fullmatch(expected_content_packet_digest)):
            raise WorkbenchError("content_packet_required", "The exact content packet digest is required")
        request["content_packet_digest"] = expected_content_packet_digest
    # Authenticate first, but resolve a completed operation before rebuilding
    # content. A historical replay returns its old pin, even after inputs or
    # policy changed; it does not register a new vote for the current packet.
    old = _operation(connection, operation_id, "decide", request)
    if old:
        body = json.loads(connection.execute("SELECT body FROM governance_decisions WHERE decision_id=?", (old["result_id"],)).fetchone()[0])
        if (content_enabled and expected_content_packet_digest is not None
                and body.get("content_packet", {}).get("packet_digest") != expected_content_packet_digest):
            raise WorkbenchError("idempotency_conflict", "Operation ID already names a different content packet")
        return {**body, "replayed": True}
    if content_enabled and expected_content_packet_digest is None:
        raise WorkbenchError("content_packet_required", "The exact content packet digest is required")
    policy = current_policy(connection)
    if proposal["status"] != "proposed":
        raise WorkbenchError("governance_closed_proposal", "New decisions require a proposed candidate")
    if expected_proposal_digest != proposal["digest"]:
        raise WorkbenchError("governance_proposal_changed", "Expected digest differs from the exact candidate")
    if expected_policy_generation != policy["generation"]:
        raise WorkbenchError("governance_policy_changed", "Expected policy generation does not match current policy")
    packet = content_gate.build_packet(connection, proposal) if content_enabled else None
    if packet is not None:
        if packet["packet_digest"] != expected_content_packet_digest:
            raise WorkbenchError("content_packet_changed", "Expected digest differs from the current content packet")
        if verdict == "approve" and not packet["eligible"]:
            raise WorkbenchError("content_packet_blocked", "A blocked content packet cannot receive an approval")
    head = connection.execute("SELECT decision_id FROM governance_heads WHERE proposal_id=? AND artifact_id=? AND principal_id=?",
                              (proposal["id"], artifact_id, reviewer["principal_id"])).fetchone()
    if (head[0] if head else None) != expected_previous_decision:
        raise WorkbenchError("governance_decision_conflict", "Previous decision does not match the current principal head")
    from . import review_continuity
    continuity_stamp = review_continuity.stamp(connection, packet) if review_continuity.enabled(connection) else None
    now = time.time()
    authorize(connection, credential, "reviewer", [artifact_id], now=now)
    body = {"schema_version": 1, "rules_version": RULES_VERSION, **request,
            "policy_digest": policy["policy_digest"], "operation_id": operation_id, "created_at": now}
    if packet is not None:
        content_gate.save_packet(connection, packet)
        body["schema_version"] = 2
        body["content_packet"] = _content_pin(packet)
    if continuity_stamp is not None:
        body["schema_version"] = 3
        body["review_continuity"] = continuity_stamp
    body["decision_id"] = "gvd_" + digest(body)
    connection.execute("INSERT INTO governance_decisions(decision_id,proposal_id,artifact_id,principal_id,grant_id,body) VALUES (?,?,?,?,?,?)",
                       (body["decision_id"], proposal["id"], artifact_id, reviewer["principal_id"], reviewer["grant_id"], canonical(body)))
    connection.execute("INSERT INTO governance_heads VALUES (?,?,?,?) ON CONFLICT(proposal_id,artifact_id,principal_id) DO UPDATE SET decision_id=excluded.decision_id",
                       (proposal["id"], artifact_id, reviewer["principal_id"], body["decision_id"]))
    connection.execute("INSERT INTO governance_operations VALUES (?,?,?,?)",
                       (operation_id, "decide", digest(request), body["decision_id"]))
    _event(connection, "governance_decision_recorded", body)
    return {**body, "replayed": False}


def observe(connection: sqlite3.Connection, proposal: sqlite3.Row, coordinator: dict[str, Any],
            expected_policy_generation: Any, *, now: float | None = None) -> dict[str, Any]:
    """Inspect governance and its enabled content gate; runtime stays separate."""
    sampled = time.time() if now is None else now
    policy = current_policy(connection)
    reasons = []
    from . import authorship, content_gate, review_continuity
    continuous = review_continuity.enabled(connection)
    attribution = None
    contributors: set[str] = set()
    if authorship.enabled(connection):
        attribution = authorship.validate(connection, proposal["id"])
        reasons.extend(attribution["reasons"])
        contributors = set(attribution["contributor_principals"])
        if coordinator["principal_id"] in contributors:
            reasons.append(reason("authorship_coordinator_contributor", principal_id=coordinator["principal_id"]))
    if type(expected_policy_generation) is not int or expected_policy_generation != policy["generation"]:
        reasons.append(reason("governance_policy_generation_mismatch", expected=expected_policy_generation,
                              actual=policy["generation"]))
    packet = content_gate.build_packet(connection, proposal) if content_gate.enabled(connection) else None
    if packet is not None:
        if not packet["eligible"]:
            reasons.append(reason("content_packet_blocked", packet_id=packet["packet_id"]))
        reasons.extend(packet["reasons"])
    heads = {row[0] for row in connection.execute("SELECT decision_id FROM governance_heads WHERE proposal_id=?", (proposal["id"],))}
    observations = []
    for row in connection.execute("SELECT body FROM governance_decisions WHERE proposal_id=? ORDER BY seq", (proposal["id"],)):
        body = json.loads(row[0])
        invalid = []
        if attribution is not None and not attribution["integrity_valid"]:
            invalid.append("authorship_provenance_invalid")
        if body["principal_id"] in contributors:
            invalid.append("authorship_contributor_separated")
        if body["decision_id"] not in heads:
            invalid.append("superseded")
        if body["proposal_digest"] != proposal["digest"]:
            invalid.append("proposal_digest_changed")
        continuity_match = None
        if continuous:
            continuity_match = review_continuity.match_decision(connection, body, packet)
            if not continuity_match["valid"]:
                invalid.extend(continuity_match["reasons"] or ["continuity_match_invalid"])
        elif body["policy_generation"] != policy["generation"] or body["policy_digest"] != policy["policy_digest"]:
            invalid.append("policy_generation_changed")
        if packet is not None:
            if not continuous and not _content_decision_matches(body, packet):
                invalid.append("content_packet_changed")
            if body["verdict"] == "approve" and not packet["eligible"]:
                invalid.append("content_packet_blocked")
        grant_row = connection.execute("SELECT * FROM governance_grants WHERE grant_id=?", (body["grant_id"],)).fetchone()
        grant = _grant_public(connection, grant_row, sampled) if grant_row else None
        if grant is None or grant["role"] != "reviewer" or grant["principal_id"] != body["principal_id"] or body["artifact_id"] not in grant["artifact_ids"]:
            invalid.append("grant_not_authorized")
        elif grant["revoked"]:
            invalid.append("grant_revoked")
        elif grant["expired"]:
            invalid.append("grant_expired")
        if (policy["policy"]["separate_coordinator"] and body["verdict"] == "approve"
                and body["principal_id"] == coordinator["principal_id"]):
            invalid.append("coordinator_separated")
        if (continuous and continuity_match is not None
                and len(continuity_match["reasons"]) == 1
                and continuity_match["reasons"][0] in {"continuity_history_limit", "continuity_history_invalid"}
                and invalid == continuity_match["reasons"]):
            # An unresolved old veto must not silently disappear just because
            # its history is malformed or exceeds the bounded reader. A fresh
            # head or revocation is needed before this proposal can be accepted.
            reasons.append(reason("continuity_reconsideration_required", decision_id=body["decision_id"]))
        item = {**body, "current_head": body["decision_id"] in heads,
                "grant_expires_at": grant["expires_at"] if grant else None,
                "valid": not invalid, "invalid_reasons": invalid}
        if continuous:
            item["continuity_match"] = continuity_match
        observations.append(item)
    writes = []
    for identifier in write_ids(proposal):
        valid = [item for item in observations if item["artifact_id"] == identifier and item["valid"]]
        approvals = sorted((item for item in valid if item["verdict"] == "approve"), key=lambda item: item["principal_id"])
        vetoes = [item for item in valid if item["verdict"] == "request_changes"]
        minimum = policy["policy"]["minimum_reviewers"]
        if len(approvals) < minimum:
            reasons.append(reason("governance_quorum_missing", artifact_id=identifier, required=minimum, actual=len(approvals)))
        if vetoes:
            reasons.append(reason("governance_changes_requested", artifact_id=identifier,
                                  decision_ids=[item["decision_id"] for item in vetoes]))
        writes.append({"artifact_id": identifier, "required_reviewers": minimum,
                       "approval_principals": [item["principal_id"] for item in approvals],
                       "approval_decision_ids": [item["decision_id"] for item in approvals],
                       "used_decision_ids": [item["decision_id"] for item in approvals[:minimum]],
                       "veto_decision_ids": [item["decision_id"] for item in vetoes],
                       "eligible": len(approvals) >= minimum and not vetoes})
    deadlines = [coordinator["expires_at"]] + [item["grant_expires_at"] for item in observations if item["valid"]]
    finite_deadlines = [deadline for deadline in deadlines if deadline is not None]
    result = {"schema_version": 1, "rules_version": RULES_VERSION, "enabled": True,
            "proposal_id": proposal["id"], "proposal_digest": proposal["digest"],
            "proposal_status": proposal["status"], "policy_generation": policy["generation"],
            "policy_digest": policy["policy_digest"], "policy": policy["policy"],
            "coordinator": {"principal_id": coordinator["principal_id"], "grant_id": coordinator["grant_id"]},
            "observed_at": sampled, "valid_until": min(finite_deadlines) if finite_deadlines else None,
            "writes": writes, "decisions": observations,
            "eligible": not reasons, "reasons": reasons, "advisory_only": True,
            "author_identity_authenticated": False, "human_identity_authenticated": False}
    if attribution is not None:
        result["authorship"] = {"rules_version": authorship.RULES_VERSION,
                                "integrity_valid": attribution["integrity_valid"],
                                "reasons": attribution["reasons"],
                                "proof": authorship.metadata(attribution["record"]) if attribution["record"] else None,
                                "contributor_principals": attribution["contributor_principals"]}
    if packet is not None:
        result["content"] = packet
    if continuous:
        matches = {item["decision_id"]: item["continuity_match"] for item in observations}
        result["review_continuity"] = {
            **review_continuity.stamp(connection, packet),
            "used_decisions": {decision_id: matches[decision_id]["proof"]
                               for write in writes for decision_id in write["used_decision_ids"]},
        }
    return result


def finalize_observation(connection: sqlite3.Connection, proposal: sqlite3.Row,
                         credential: Any, expected_policy_generation: Any,
                         observation: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Revalidate the bounded selected quorum at a fresh authorization time.

    This deliberately refuses if a selected approval expired during the full
    scan, even if another approval could substitute. The write lock prevents
    policy, heads and revocations from changing. No promise is made about
    authority remaining valid after this recorded point in the transaction.
    """
    identifiers = write_ids(proposal)
    failures = []
    from . import authorship, content_gate, review_continuity
    continuous = review_continuity.enabled(connection)
    contributors: set[str] = set()
    if authorship.enabled(connection):
        attribution = authorship.validate(connection, proposal["id"])
        failures.extend(attribution["reasons"])
        contributors = set(attribution["contributor_principals"])
        previous_proof = observation.get("authorship", {}).get("proof")
        current_proof = authorship.metadata(attribution["record"]) if attribution["record"] else None
        if previous_proof != current_proof:
            failures.append(reason("authorship_provenance_changed"))
    packet = content_gate.build_packet(connection, proposal) if content_gate.enabled(connection) else None
    if packet is not None:
        if not packet["eligible"]:
            failures.append(reason("content_packet_blocked", packet_id=packet["packet_id"]))
        failures.extend(packet["reasons"])
        previous_packet = observation.get("content")
        if previous_packet is None or _content_pin(previous_packet) != _content_pin(packet):
            failures.append(reason("content_packet_changed"))
    prepared_matches = {}
    selected_bodies = {}
    continuity_summary = None
    if continuous:
        # Full historical packet and transition validation can be expensive.
        # Resolve every selected proof before sampling current grant authority.
        for write in observation["writes"]:
            for decision_id in write["used_decision_ids"]:
                row = connection.execute("SELECT body FROM governance_decisions WHERE decision_id=?", (decision_id,)).fetchone()
                body = json.loads(row[0]) if row else None
                selected_bodies[decision_id] = body
                if body is None:
                    failures.append(reason("governance_selected_approval_invalid", artifact_id=write["artifact_id"], decision_id=decision_id))
                    continue
                matched = review_continuity.match_decision(connection, body, packet)
                prepared_matches[decision_id] = matched
                if not matched["valid"]:
                    failures.append(reason("continuity_selected_approval_invalid", artifact_id=write["artifact_id"],
                                           decision_id=decision_id, continuity_reasons=matched["reasons"]))
        continuity_summary = {
            **review_continuity.stamp(connection, packet),
            "used_decisions": {identifier: matched["proof"] for identifier, matched in prepared_matches.items()},
        }
    # Sample authority after provenance, content and continuity scans, so a
    # grant expiring during any of those scans cannot pass with an old time.
    now = time.time()
    coordinator = authorize(connection, credential, "coordinator", identifiers, now=now)
    policy = current_policy(connection)
    if coordinator["principal_id"] in contributors:
        failures.append(reason("authorship_coordinator_contributor", principal_id=coordinator["principal_id"]))
    if (type(expected_policy_generation) is not int
            or expected_policy_generation != policy["generation"]
            or observation["policy_generation"] != policy["generation"]
            or observation["policy_digest"] != policy["policy_digest"]):
        failures.append(reason("governance_policy_generation_mismatch", expected=expected_policy_generation,
                               actual=policy["generation"]))
    for write in observation["writes"]:
        for decision_id in write["used_decision_ids"]:
            if continuous:
                body = selected_bodies[decision_id]
            else:
                row = connection.execute("SELECT body FROM governance_decisions WHERE decision_id=?", (decision_id,)).fetchone()
                body = json.loads(row[0]) if row else None
            if body is None:
                failures.append(reason("governance_selected_approval_invalid", artifact_id=write["artifact_id"], decision_id=decision_id))
                continue
            grant_row = connection.execute("SELECT * FROM governance_grants WHERE grant_id=?", (body["grant_id"],)).fetchone()
            grant = _grant_public(connection, grant_row, now) if grant_row else None
            head = connection.execute("SELECT decision_id FROM governance_heads WHERE proposal_id=? AND artifact_id=? AND principal_id=?",
                                      (proposal["id"], write["artifact_id"], body["principal_id"])).fetchone()
            policy_and_content_valid = (
                prepared_matches[decision_id]["valid"] if continuous else (
                    body["policy_generation"] == policy["generation"] and body["policy_digest"] == policy["policy_digest"]
                    and (packet is None or (packet["eligible"] and _content_decision_matches(body, packet)))
                )
            )
            valid = (grant is not None and grant["role"] == "reviewer" and not grant["revoked"]
                     and grant["principal_id"] == body["principal_id"]
                     and write["artifact_id"] in grant["artifact_ids"]
                     and body["artifact_id"] == write["artifact_id"]
                     and body["proposal_id"] == proposal["id"] and body["proposal_digest"] == proposal["digest"]
                     and policy_and_content_valid
                     and body["verdict"] == "approve" and head is not None and head[0] == decision_id
                     and body["principal_id"] not in contributors
                     and (not continuous or packet["eligible"])
                     and (not policy["policy"]["separate_coordinator"] or body["principal_id"] != coordinator["principal_id"]))
            if not valid:
                failures.append(reason("governance_selected_approval_invalid", artifact_id=write["artifact_id"], decision_id=decision_id))
            elif grant["expired"]:
                failures.append(reason("governance_approval_expired", artifact_id=write["artifact_id"], decision_id=decision_id))
    result = {**observation, "authorization_time": now,
              "eligible": observation["eligible"] and not failures,
              "reasons": [*observation["reasons"], *failures],
              "authorization_reasons": failures}
    if continuous:
        result["review_continuity"] = continuity_summary
    return result, coordinator


def receipt(connection: sqlite3.Connection, proposal: sqlite3.Row, revision: int,
            observation: dict[str, Any]) -> dict[str, Any]:
    if not observation["eligible"]:
        raise WorkbenchError("governance_ineligible", "An ineligible observation cannot become an acceptance receipt")
    body = {"schema_version": 1, "rules_version": RULES_VERSION,
            "proposal_id": proposal["id"], "proposal_digest": proposal["digest"], "revision": revision,
            "policy_generation": observation["policy_generation"], "policy_digest": observation["policy_digest"],
            "coordinator": observation["coordinator"], "accepted_at": observation["authorization_time"],
            "authorization_time": observation["authorization_time"],
            "decisions": {item["artifact_id"]: item["used_decision_ids"] for item in observation["writes"]}}
    if "authorship" in observation:
        attribution = observation["authorship"]
        if not attribution["integrity_valid"] or attribution["proof"] is None:
            raise WorkbenchError("authorship_provenance_invalid", "Acceptance requires authenticated provenance")
        body["schema_version"] = 2
        body["authorship"] = attribution["proof"]
    if "content" in observation:
        from . import content_gate
        packet = observation["content"]
        if not packet["eligible"]:
            raise WorkbenchError("content_packet_blocked", "Acceptance requires an eligible content packet")
        content_gate.save_packet(connection, packet)
        body["schema_version"] = 3
        body["content_packet"] = _content_pin(packet)
    if "review_continuity" in observation:
        body["schema_version"] = 4
        body["review_continuity"] = observation["review_continuity"]
    body["receipt_id"] = "gvr_" + digest(body)
    if body["schema_version"] == 4 and len(canonical(body).encode("utf-8")) > 4_000_000:
        raise WorkbenchError("continuity_receipt_too_large", "Continuity acceptance receipt exceeds 4 MB")
    connection.execute("INSERT INTO governance_receipts VALUES (?,?,?)", (proposal["id"], body["receipt_id"], canonical(body)))
    return body


def existing_receipt(connection: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
    row = connection.execute("SELECT body FROM governance_receipts WHERE proposal_id=?", (proposal_id,)).fetchone()
    return json.loads(row[0]) if row else None


def status(connection: sqlite3.Connection, proposal_id: str | None = None) -> dict[str, Any]:
    if not enabled(connection):
        return {"enabled": False, "rules_version": RULES_VERSION}
    now = time.time()
    settings = connection.execute("SELECT * FROM governance_settings WHERE singleton=1").fetchone()
    policies = [{"generation": row["generation"], "policy": json.loads(row["body"]),
                 "policy_digest": row["digest"], "created_at": row["created_at"],
                 "administrator_grant_id": row["administrator_grant_id"]}
                for row in connection.execute("SELECT * FROM governance_policies ORDER BY generation")]
    decision_query = "SELECT body FROM governance_decisions" + (" WHERE proposal_id=?" if proposal_id is not None else "") + " ORDER BY seq"
    receipt_query = "SELECT body FROM governance_receipts" + (" WHERE proposal_id=?" if proposal_id is not None else "") + " ORDER BY rowid"
    parameters = (proposal_id,) if proposal_id is not None else ()
    return {"enabled": True, "rules_version": RULES_VERSION, "observed_at": now,
            "enabled_at": settings["enabled_at"], "activation_revision": settings["activation_revision"],
            "administrator_id": settings["administrator_id"],
            "policy_generation": policies[-1]["generation"], "policy_digest": policies[-1]["policy_digest"],
            "policy": policies[-1]["policy"], "policy_history": policies,
            "grants": [_grant_public(connection, row, now) for row in connection.execute("SELECT * FROM governance_grants ORDER BY created_at,grant_id")],
            "revocations": [dict(row) for row in connection.execute("SELECT * FROM governance_revocations ORDER BY revoked_at,grant_id")],
            "decisions": [json.loads(row[0]) for row in connection.execute(decision_query, parameters)],
            "receipts": [json.loads(row[0]) for row in connection.execute(receipt_query, parameters)],
            "credential_mode": "local_bearer", "author_identity_authenticated": False,
            "human_identity_authenticated": False, "trusted_database_owner_can_bypass": True}
