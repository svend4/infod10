"""Opt-in local attribution, pinned attempts and transitive separation.

The SQLite owner and local clock remain trusted. Records authenticate locally
issued bearer authority at creation, not human identity or expert independence.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time
from typing import Any

from . import governance
from .store import ROLE_KINDS, WorkbenchError, canonical, digest, reason


RULES_VERSION = "authorship-1"
MAX_PROPOSALS = 256
MAX_DEPTH = 16
MAX_CONTRIBUTORS = 256
MAX_BODY_BYTES = 1000000

SCHEMA = """
CREATE TABLE IF NOT EXISTS authorship_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), rules_version TEXT NOT NULL,
    epoch INTEGER NOT NULL, enabled_at REAL NOT NULL,
    activation_revision INTEGER NOT NULL, policy_generation INTEGER NOT NULL,
    administrator_grant_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS authorship_leases (
    task_id TEXT NOT NULL REFERENCES tasks(id), token INTEGER NOT NULL,
    principal_id TEXT NOT NULL, grant_id TEXT NOT NULL REFERENCES governance_grants(grant_id),
    role TEXT NOT NULL, claimed_at REAL NOT NULL,
    PRIMARY KEY(task_id,token)
);
CREATE TABLE IF NOT EXISTS proposal_authorship (
    proposal_id TEXT PRIMARY KEY REFERENCES proposals(id),
    authorship_id TEXT NOT NULL UNIQUE, digest TEXT NOT NULL, body TEXT NOT NULL
);
"""


def enabled(connection: sqlite3.Connection) -> bool:
    return connection.execute("SELECT 1 FROM authorship_settings WHERE singleton=1").fetchone() is not None


def activation(connection: sqlite3.Connection) -> dict[str, Any] | None:
    row = connection.execute("SELECT * FROM authorship_settings WHERE singleton=1").fetchone()
    return {key: row[key] for key in row.keys() if key != "singleton"} if row else None


def enable(connection: sqlite3.Connection, *, credential: Any,
           expected_policy_generation: Any) -> dict[str, Any]:
    if enabled(connection):
        raise WorkbenchError("authorship_already_enabled", "Authorship activation is one-way")
    administrator = governance.authorize(connection, credential, "administrator")
    current = governance.current_policy(connection)
    changed = governance.set_policy(connection, current["policy"], credential=credential,
                                    expected_generation=expected_policy_generation)
    now = time.time()
    revision = int(connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
    connection.execute("INSERT INTO authorship_settings VALUES (1,?,?,?,?,?,?)",
                       (RULES_VERSION, 1, now, revision, changed["generation"], administrator["grant_id"]))
    result = {"enabled": True, "rules_version": RULES_VERSION, "activation": activation(connection),
              "policy_generation": changed["generation"], "policy_digest": changed["policy_digest"],
              "credential_mode": "local_bearer", "human_identity_authenticated": False}
    governance._event(connection, "authorship_enabled", result)
    return result


def authorize_worker(connection: sqlite3.Connection, credential: Any, worker: str,
                     role: str | None = None, write_ids: list[str] | None = None,
                     *, now: float | None = None) -> dict[str, Any]:
    settings = activation(connection)
    if not settings or settings["rules_version"] != RULES_VERSION or settings["epoch"] != 1:
        raise WorkbenchError("authorship_provenance_invalid", "Unknown authorship activation rules")
    # A role-less claim derives the credential role without exposing its hash.
    if role is None:
        hashed = governance._credential_hash(credential)
        row = connection.execute("SELECT role FROM governance_grants WHERE credential_hash=?", (hashed,)).fetchone()
        if row is None or row[0] not in ROLE_KINDS:
            raise WorkbenchError("governance_unauthorized", "An author role credential is required")
        role = row[0]
    grant = governance.authorize(connection, credential, role, write_ids, now=now)
    if grant["principal_id"] != worker:
        raise WorkbenchError("authorship_principal_mismatch", "Worker must equal the authenticated principal")
    return grant


def pin_lease(connection: sqlite3.Connection, task_id: str, token: int,
              grant: dict[str, Any], now: float) -> None:
    connection.execute("INSERT INTO authorship_leases VALUES (?,?,?,?,?,?)",
                       (task_id, token, grant["principal_id"], grant["grant_id"], grant["role"], now))


def authorize_attempt(connection: sqlite3.Connection, task: sqlite3.Row | None,
                      worker: str, token: int, credential: Any,
                      write_ids: list[str] | None = None) -> dict[str, Any]:
    if task is None:
        raise WorkbenchError("lease_lost", "Task does not exist")
    grant = authorize_worker(connection, credential, worker, task["role"], write_ids)
    pinned = connection.execute("SELECT * FROM authorship_leases WHERE task_id=? AND token=?",
                                (task["id"], token)).fetchone()
    if pinned is None:
        raise WorkbenchError("authorship_lease_unproven", "This attempt has no authenticated lease; expire and reclaim it")
    if (pinned["grant_id"] != grant["grant_id"] or pinned["principal_id"] != worker
            or pinned["role"] != task["role"]):
        raise WorkbenchError("authorship_grant_mismatch", "The original lease grant must authorize this attempt")
    return grant


def _historical_grant(connection: sqlite3.Connection, grant_id: str, principal: str,
                      role: str, writes: list[str], sampled: float) -> bool:
    row = connection.execute("SELECT * FROM governance_grants WHERE grant_id=?", (grant_id,)).fetchone()
    revoked = connection.execute("SELECT revoked_at FROM governance_revocations WHERE grant_id=?", (grant_id,)).fetchone()
    return bool(row and row["principal_id"] == principal and row["role"] == role
                and row["created_at"] <= sampled
                and (row["expires_at"] is None or sampled < row["expires_at"])
                and (revoked is None or sampled < revoked[0])
                and set(writes).issubset(json.loads(row["artifact_ids"])))


def _load_node(connection: sqlite3.Connection, proposal_id: str) -> tuple[dict[str, Any], list[str]]:
    row = connection.execute("SELECT * FROM proposal_authorship WHERE proposal_id=?", (proposal_id,)).fetchone()
    proposal = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None or proposal is None:
        raise WorkbenchError("authorship_provenance_invalid", "A candidate or source has no authorship proof")
    if len(row["body"].encode("utf-8")) > MAX_BODY_BYTES:
        raise WorkbenchError("authorship_provenance_invalid", "Authorship proof exceeds the size limit")
    body = json.loads(row["body"])
    required = {"schema_version", "rules_version", "epoch", "proposal_id", "proposal_digest", "task_id",
                "fencing_token", "principal_id", "grant_id", "role", "write_ids", "authorization_time",
                "lease_claimed_at", "components", "contributor_principals"}
    if (not isinstance(body, dict) or set(body) != required or type(body["schema_version"]) is not int
            or body["schema_version"] != 1 or type(body["epoch"]) is not int
            or type(body["fencing_token"]) is not int or body["fencing_token"] < 1
            or any(type(body[key]) not in {int, float} or not math.isfinite(body[key])
                   for key in ("authorization_time", "lease_claimed_at"))
            or body["rules_version"] != RULES_VERSION or canonical(body) != row["body"]
            or digest(body) != row["digest"] or row["authorship_id"] != "awp_" + row["digest"]):
        raise WorkbenchError("authorship_provenance_invalid", "Authorship body or digest is invalid")
    payload = json.loads(proposal["payload"])
    writes = sorted(item["id"] for item in payload["writes"])
    task = connection.execute("SELECT * FROM tasks WHERE id=?", (proposal["task_id"],)).fetchone()
    pinned = connection.execute("SELECT * FROM authorship_leases WHERE task_id=? AND token=?",
                                (proposal["task_id"], proposal["token"])).fetchone()
    settings = activation(connection)
    if (not settings or settings["rules_version"] != RULES_VERSION or settings["epoch"] != 1
            or body["epoch"] != settings["epoch"]
            or body["proposal_id"] != proposal_id or body["proposal_digest"] != proposal["digest"]
            or digest(payload) != proposal["digest"]
            or proposal_id != "p_" + digest({"task": proposal["task_id"], "token": proposal["token"], "payload": proposal["digest"]})[:24]
            or body["task_id"] != proposal["task_id"] or body["fencing_token"] != proposal["token"]
            or body["principal_id"] != proposal["author"] or task is None or body["role"] != task["role"]
            or body["role"] not in ROLE_KINDS or body["write_ids"] != writes
            or any(item["kind"] not in ROLE_KINDS[body["role"]] for item in payload["writes"])
            or pinned is None or pinned["principal_id"] != body["principal_id"]
            or pinned["grant_id"] != body["grant_id"] or pinned["role"] != body["role"]
            or body["lease_claimed_at"] != pinned["claimed_at"]
            or not settings["enabled_at"] <= pinned["claimed_at"] <= body["authorization_time"]
            or not _historical_grant(connection, body["grant_id"], body["principal_id"], body["role"], writes, body["authorization_time"])
            or not _historical_grant(connection, body["grant_id"], body["principal_id"], body["role"], [], pinned["claimed_at"])):
        raise WorkbenchError("authorship_provenance_invalid", "Authorship does not match the original proposal, grant or lease")
    components = list(connection.execute("SELECT * FROM proposal_components WHERE composite_id=? ORDER BY position", (proposal_id,)))
    if (len(components) > 32 or not isinstance(body["components"], list)
            or len(components) != len(body["components"])
            or bool(components) != (body["role"] == "integrator")):
        raise WorkbenchError("authorship_provenance_invalid", "Authorship component lineage differs")
    expected = []
    children = []
    for position, component in enumerate(components, 1):
        source = connection.execute("SELECT * FROM proposals WHERE id=?", (component["source_id"],)).fetchone()
        proof = connection.execute("SELECT authorship_id,digest FROM proposal_authorship WHERE proposal_id=?", (component["source_id"],)).fetchone()
        if (source is None or proof is None or component["position"] != position
                or source["digest"] != component["source_digest"]
                or canonical(json.loads(source["payload"])["writes"]) != component["writes"]):
            raise WorkbenchError("authorship_provenance_invalid", "A source component or its proof differs")
        expected.append({"position": position, "proposal_id": source["id"], "proposal_digest": source["digest"],
                         "authorship_id": proof["authorship_id"], "authorship_digest": proof["digest"],
                         "write_ids": sorted(item["id"] for item in json.loads(component["writes"]))})
        children.append(source["id"])
    if body["components"] != expected:
        raise WorkbenchError("authorship_provenance_invalid", "Pinned source proofs differ from actual lineage")
    if components:
        embedded = json.loads(payload["rationale"])
        expected_refs = [{"proposal_id": item["proposal_id"], "digest": item["proposal_digest"], "write_ids": item["write_ids"]} for item in expected]
        combined = [item for component in components for item in json.loads(component["writes"])]
        merged_reads: dict[str, Any] = {}
        merged_queries: dict[str, str] = {}
        for component in components:
            source_payload = json.loads(connection.execute("SELECT payload FROM proposals WHERE id=?", (component["source_id"],)).fetchone()[0])
            for selected, merged in ((source_payload["readset"], merged_reads), (source_payload["queries"], merged_queries)):
                if not isinstance(selected, dict):
                    raise WorkbenchError("authorship_provenance_invalid", "A source component has malformed bases")
                for key, value in selected.items():
                    if key in merged and merged[key] != value:
                        raise WorkbenchError("authorship_provenance_invalid", "Component bases conflict")
                    merged[key] = value
        if (not isinstance(embedded, dict) or embedded.get("components") != expected_refs
                or sorted(combined, key=lambda item: item["id"]) != payload["writes"]
                or payload["readset"] != merged_reads or payload["queries"] != merged_queries):
            raise WorkbenchError("authorship_provenance_invalid", "Composite writes, bases or embedded references differ")
    return {**body, "authorship_id": row["authorship_id"], "authorship_digest": row["digest"]}, children


def validate(connection: sqlite3.Connection, proposal_id: str) -> dict[str, Any]:
    """Validate a bounded DAG iteratively; author expiry/revocation is historical."""
    try:
        states: dict[str, int] = {}
        records: dict[str, dict[str, Any]] = {}
        heights: dict[str, int] = {}
        stack = [(proposal_id, 0, False)]
        while stack:
            identifier, depth, finish = stack.pop()
            if depth > MAX_DEPTH:
                raise WorkbenchError("authorship_provenance_invalid", "Authorship ancestry exceeds depth 16")
            if finish:
                body = records[identifier]
                contributors = {body["principal_id"]}
                for component in body["components"]:
                    child = records[component["proposal_id"]]
                    if child["authorization_time"] > body["authorization_time"]:
                        raise WorkbenchError("authorship_provenance_invalid", "A source proof was created after its composition")
                    contributors.update(child["contributor_principals"])
                heights[identifier] = max((1 + heights[item["proposal_id"]] for item in body["components"]), default=0)
                if depth + heights[identifier] > MAX_DEPTH:
                    raise WorkbenchError("authorship_provenance_invalid", "Authorship ancestry exceeds depth 16")
                if len(contributors) > MAX_CONTRIBUTORS or body["contributor_principals"] != sorted(contributors):
                    raise WorkbenchError("authorship_provenance_invalid", "The transitive contributor set differs or exceeds its bound")
                states[identifier] = 2
                continue
            if states.get(identifier) == 1:
                raise WorkbenchError("authorship_provenance_invalid", "Authorship lineage contains a cycle")
            if states.get(identifier) == 2:
                if depth + heights[identifier] > MAX_DEPTH:
                    raise WorkbenchError("authorship_provenance_invalid", "Authorship ancestry exceeds depth 16")
                continue
            if len(states) >= MAX_PROPOSALS:
                raise WorkbenchError("authorship_provenance_invalid", "Authorship ancestry exceeds 256 proposals")
            record, children = _load_node(connection, identifier)
            states[identifier], records[identifier] = 1, record
            stack.append((identifier, depth, True))
            stack.extend((child, depth + 1, False) for child in reversed(children))
        root = records[proposal_id]
        return {"integrity_valid": True, "reasons": [], "record": root,
                "contributor_principals": root["contributor_principals"], "validated_proposals": len(records)}
    except (WorkbenchError, ValueError, TypeError, KeyError, IndexError, RecursionError, OverflowError, sqlite3.Error) as error:
        detail = str(error) if isinstance(error, WorkbenchError) else "Malformed authorship evidence"
        return {"integrity_valid": False, "reasons": [reason("authorship_provenance_invalid", detail=detail)],
                "record": None, "contributor_principals": [], "validated_proposals": 0}


def require(connection: sqlite3.Connection, proposal_id: str) -> dict[str, Any]:
    result = validate(connection, proposal_id)
    if not result["integrity_valid"]:
        raise WorkbenchError("authorship_provenance_invalid", result["reasons"][0]["detail"])
    return result


def separate(connection: sqlite3.Connection, proposal_id: str, principal: str) -> dict[str, Any]:
    result = require(connection, proposal_id)
    if principal in result["contributor_principals"]:
        raise WorkbenchError("authorship_contributor_separated", "A transitive contributor cannot review, decide or coordinate this candidate")
    return result


def metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("authorship_id", "authorship_digest", "principal_id", "contributor_principals", "write_ids")}


def record(connection: sqlite3.Connection, proposal_id: str, task: sqlite3.Row,
           worker: str, token: int, credential: Any) -> dict[str, Any]:
    proposal = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    writes = governance.write_ids(proposal)
    children = list(connection.execute("SELECT * FROM proposal_components WHERE composite_id=? ORDER BY position", (proposal_id,)))
    components, contributors = [], {worker}
    for item in children:
        source = require(connection, item["source_id"])["record"]
        contributors.update(source["contributor_principals"])
        components.append({"position": item["position"], "proposal_id": item["source_id"],
                           "proposal_digest": item["source_digest"], "authorship_id": source["authorship_id"],
                           "authorship_digest": source["authorship_digest"],
                           "write_ids": sorted(body["id"] for body in json.loads(item["writes"]))})
    if len(contributors) > MAX_CONTRIBUTORS:
        raise WorkbenchError("authorship_provenance_invalid", "Too many transitive contributors")
    # Fresh current authority check after source traversal and payload validation.
    grant = authorize_attempt(connection, task, worker, token, credential, writes)
    now = time.time()
    grant = authorize_worker(connection, credential, worker, task["role"], writes, now=now)
    pinned = connection.execute("SELECT * FROM authorship_leases WHERE task_id=? AND token=?", (task["id"], token)).fetchone()
    if not (task["state"] == "leased" and task["worker"] == worker and task["token"] == token and task["lease_until"] > now):
        raise WorkbenchError("lease_lost", "Lease expired during authorship capture")
    body = {"schema_version": 1, "rules_version": RULES_VERSION, "epoch": activation(connection)["epoch"],
            "proposal_id": proposal_id, "proposal_digest": proposal["digest"], "task_id": task["id"],
            "fencing_token": token, "principal_id": worker, "grant_id": grant["grant_id"], "role": task["role"],
            "write_ids": writes, "authorization_time": now, "lease_claimed_at": pinned["claimed_at"],
            "components": components, "contributor_principals": sorted(contributors)}
    body_text = canonical(body)
    if len(body_text.encode("utf-8")) > MAX_BODY_BYTES:
        raise WorkbenchError("authorship_provenance_invalid", "Authorship proof exceeds 1 MB")
    proof_digest = digest(body)
    connection.execute("INSERT INTO proposal_authorship VALUES (?,?,?,?)",
                       (proposal_id, "awp_" + proof_digest, proof_digest, body_text))
    # Apply the same bounded validator to the newly formed ancestry before commit.
    validated = require(connection, proposal_id)["record"]
    governance._event(connection, "proposal_authorship_recorded", {"proposal_id": proposal_id, **metadata(validated)})
    return metadata(validated)


def status(connection: sqlite3.Connection, proposal_id: str | None = None) -> dict[str, Any]:
    result = {"enabled": enabled(connection), "rules_version": RULES_VERSION,
              "activation": activation(connection), "credential_mode": "local_bearer",
              "human_identity_authenticated": False, "trusted_database_owner_can_bypass": True}
    if proposal_id is not None:
        result.update({"proposal_id": proposal_id, **validate(connection, proposal_id)})
        return result
    result["leases"] = [dict(row) for row in connection.execute("SELECT * FROM authorship_leases ORDER BY claimed_at,task_id,token")]
    result["records"] = [{"proposal_id": row[0], **validate(connection, row[0])}
                         for row in connection.execute("SELECT proposal_id FROM proposal_authorship ORDER BY rowid")]
    return result
