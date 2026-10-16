"""Mandatory comparisons of bounded, administrator-declared content.

All functions use the caller's existing Store transaction.  The packet is a
deterministic observation of declared fields, not an external review report.
The frozen obligations-1 and coverage-1 extractors parse proposed code as data.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from typing import Any

from . import authorship, coverage, governance, obligations
from .store import Store, WorkbenchError, canonical, digest, reason, safe_file_id


RULES_VERSION = "content-gate-1"
MAX_ARTIFACTS = 16
MAX_OBLIGATIONS = 16
MAX_PROMISES = 16
MAX_POLICY_BYTES = 1_000_000
MAX_PACKET_BYTES = 4_000_000
MAX_STATUS_PACKETS = 100
LIMITS = [
    "Only the administrator-declared inventory and its exact artifact bundle are covered; completeness over other text or project requirements is not established.",
    "Exact unique quotations and resolved fields pin declared locations; correspondence between prose and those fields is not inferred or verified.",
    "Literal Python declarations do not establish program behavior; proposed code is parsed with bounded AST rules and is never executed.",
    "Matches do not establish prose truth, external evidence, human identity, independent people or independent models.",
    "A standalone packet does not authorize acceptance; formal review, current runtime bases, authorship and governance remain separate requirements.",
    "The SQLite owner and application implementation remain trusted; hashes provide internal consistency rather than external certification.",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS content_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), rules_version TEXT NOT NULL,
    enabled_at REAL NOT NULL, activation_revision INTEGER NOT NULL,
    governance_policy_generation INTEGER NOT NULL,
    administrator_grant_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_policies (
    generation INTEGER PRIMARY KEY, body TEXT NOT NULL, digest TEXT NOT NULL,
    created_at REAL NOT NULL, governance_policy_generation INTEGER NOT NULL,
    administrator_grant_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS content_packets (
    packet_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
    proposal_id TEXT NOT NULL REFERENCES proposals(id),
    content_policy_generation INTEGER NOT NULL REFERENCES content_policies(generation),
    body TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS content_packets_proposal ON content_packets(proposal_id);
"""

_PACKET_FIELDS = {
    "schema_version", "content_rules_version", "obligation_rules_version",
    "coverage_rules_version", "content_policy_generation", "content_policy_digest",
    "governance_policy_generation", "governance_policy_digest", "proposal_id",
    "proposal_digest", "read_set", "queries", "write_ids", "required_write_ids",
    "relations", "promises", "dependencies", "eligible", "reasons", "limits",
    "packet_id", "packet_digest",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_content_policy", message)


def _rules() -> None:
    if (obligations.OBLIGATION_RULES_VERSION != "obligations-1"
            or coverage.COVERAGE_RULES_VERSION != "coverage-1"):
        raise WorkbenchError("content_rules_unsupported", "Content gate requires frozen obligations-1 and coverage-1 rules")


def _policy(value: Any) -> dict[str, Any]:
    """Validate a complete, bounded mapping rather than accepting loose IDs."""
    _rules()
    try:
        obligations._shape(value)
        encoded = canonical(value).encode("utf-8")
        _require(len(encoded) <= MAX_POLICY_BYTES, "Content policy exceeds 1 MB")
        captured = json.loads(encoded)
        _require(isinstance(captured, dict) and set(captured) == {
            "schema_version", "artifact_ids", "spec", "inventory",
        }, "Expected schema_version, artifact_ids, spec and inventory")
        _require(type(captured["schema_version"]) is int and captured["schema_version"] == 1,
                 "Unsupported content policy schema")
        identifiers = captured["artifact_ids"]
        _require(isinstance(identifiers, list) and 1 <= len(identifiers) <= MAX_ARTIFACTS,
                 "Expected 1..16 distinct artifact IDs")
        _require(all(isinstance(identifier, str) and (
            safe_file_id(identifier, "document") or safe_file_id(identifier, "code")
        ) for identifier in identifiers), "Expected safe document or code artifact IDs")
        _require(len(identifiers) == len(set(identifiers)), "Duplicate policy artifact ID")
        obligations._spec(captured["spec"])
        coverage._inventory(captured["inventory"])
        items = captured["spec"]["obligations"]
        promises = captured["inventory"]["promises"]
        _require(1 <= len(items) <= MAX_OBLIGATIONS, "Expected 1..16 obligations")
        _require(1 <= len(promises) <= MAX_PROMISES, "Expected 1..16 promises")
        by_id = {item["id"]: item for item in items}
        endpoints = set()
        for item in items:
            for kind in ("document", "code"):
                identifier = item[kind]["artifact_id"]
                _require(safe_file_id(identifier, kind), "Obligation endpoint ID has the wrong artifact kind")
                endpoints.add(identifier)
        _require(set(identifiers) == endpoints,
                 "Artifact allowlist must equal the complete obligation endpoint union")
        referenced = set()
        for promise in promises:
            mapped = promise["obligation_ids"]
            _require(1 <= len(mapped) <= MAX_OBLIGATIONS,
                     "Every promise needs 1..16 obligation references")
            for identifier in mapped:
                _require(identifier in by_id, "Promise references an unknown obligation")
                _require(canonical(coverage._requirement(by_id[identifier])) == canonical(promise["requirement"]),
                         "Promise requirement differs from its referenced obligation")
                referenced.add(identifier)
        _require(referenced == set(by_id), "Every obligation must be referenced by a promise")
        captured["artifact_ids"] = sorted(identifiers)
        return captured
    except WorkbenchError as error:
        if error.code == "invalid_content_policy":
            raise
        raise WorkbenchError("invalid_content_policy", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_content_policy", "Expected a bounded, complete content policy") from error


def enabled(connection: sqlite3.Connection) -> bool:
    return connection.execute("SELECT 1 FROM content_settings WHERE singleton=1").fetchone() is not None


def current_policy(connection: sqlite3.Connection) -> dict[str, Any]:
    settings = connection.execute("SELECT * FROM content_settings WHERE singleton=1").fetchone()
    if settings is None:
        raise WorkbenchError("content_disabled", "Content gate is not enabled")
    if settings["rules_version"] != RULES_VERSION:
        raise WorkbenchError("content_policy_invalid", "Unsupported content activation rules")
    row = connection.execute("SELECT * FROM content_policies ORDER BY generation DESC LIMIT 1").fetchone()
    if row is None:
        raise WorkbenchError("content_policy_invalid", "Enabled content gate has no policy")
    policy = _policy(obligations._strict_json(row["body"]))
    if canonical(policy) != row["body"] or digest(policy) != row["digest"]:
        raise WorkbenchError("content_policy_invalid", "Stored content policy address differs from its body")
    return {"generation": row["generation"], "policy": policy,
            "policy_digest": row["digest"], "created_at": row["created_at"],
            "governance_policy_generation": row["governance_policy_generation"],
            "administrator_grant_id": row["administrator_grant_id"]}


def _append_policy(connection: sqlite3.Connection, policy: dict[str, Any], *,
                   credential: Any, expected_policy_generation: Any,
                   generation: int) -> dict[str, Any]:
    administrator = governance.authorize(connection, credential, "administrator")
    current = governance.current_policy(connection)
    changed = governance.set_policy(connection, current["policy"], credential=credential,
                                    expected_generation=expected_policy_generation)
    connection.execute("INSERT INTO content_policies VALUES (?,?,?,?,?,?)",
                       (generation, canonical(policy), digest(policy), time.time(),
                        changed["generation"], administrator["grant_id"]))
    return {"generation": generation, "policy": policy, "policy_digest": digest(policy),
            "governance_policy_generation": changed["generation"],
            "administrator_grant_id": administrator["grant_id"]}


def enable(connection: sqlite3.Connection, policy: Any, *, credential: Any,
           expected_policy_generation: Any) -> dict[str, Any]:
    if enabled(connection):
        raise WorkbenchError("content_already_enabled", "Content gate activation is one-way")
    if not governance.enabled(connection) or not authorship.enabled(connection):
        raise WorkbenchError("content_prerequisites_required", "Enable governance and authorship before content gate")
    validated = _policy(policy)
    changed = _append_policy(connection, validated, credential=credential,
                             expected_policy_generation=expected_policy_generation, generation=1)
    now = time.time()
    revision = Store._revision(connection)
    connection.execute("INSERT INTO content_settings VALUES (1,?,?,?,?,?)",
                       (RULES_VERSION, now, revision, changed["governance_policy_generation"],
                        changed["administrator_grant_id"]))
    result = {"enabled": True, "rules_version": RULES_VERSION,
              "current_policy": current_policy(connection),
              "policy_generation": changed["governance_policy_generation"],
              "content_policy_generation": changed["generation"],
              "content_policy_digest": changed["policy_digest"],
              "activation_revision": revision, "enabled_at": now}
    governance._event(connection, "content_enabled", result)
    return result


def set_policy(connection: sqlite3.Connection, policy: Any, *, credential: Any,
               expected_policy_generation: Any) -> dict[str, Any]:
    current = current_policy(connection)
    validated = _policy(policy)
    changed = _append_policy(connection, validated, credential=credential,
                             expected_policy_generation=expected_policy_generation,
                             generation=current["generation"] + 1)
    result = {"enabled": True, "rules_version": RULES_VERSION,
              "current_policy": current_policy(connection),
              "policy_generation": changed["governance_policy_generation"],
              "content_policy_generation": changed["generation"],
              "content_policy_digest": changed["policy_digest"]}
    governance._event(connection, "content_policy_changed", result)
    return result


def _relation(item: dict[str, Any], candidate: dict[str, Any],
              writes: dict[str, Any]) -> dict[str, Any]:
    document = obligations._endpoint(item["document"], "document", item["value_type"], candidate, writes)
    code = obligations._endpoint(item["code"], "code", item["value_type"], candidate, writes)
    reasons = []
    if not document["resolved"] or not code["resolved"]:
        status = "unresolved"
        for label, endpoint in (("document", document), ("code", code)):
            reasons.extend({"endpoint": label, **value} for value in endpoint["reasons"])
    else:
        left, right = document["normalized_value"], code["normalized_value"]
        matches = left == right if item["relation"] == "equals" else set(right) <= set(left)
        status = "match" if matches else "mismatch"
        if not matches:
            reasons.append(reason("declared_values_differ" if item["relation"] == "equals"
                                  else "code_members_not_documented"))
    return {"id": item["id"], "scope": item["scope"], "relation": item["relation"],
            "value_type": item["value_type"], "status": status, "reasons": reasons,
            "document": document, "code": code}


def build_packet(connection: sqlite3.Connection, proposal: sqlite3.Row) -> dict[str, Any]:
    """Capture current declared content without clocks or review decisions.

    Call this in the same transaction as the decision or acceptance that uses
    it. Standalone callers receive an observation, never a reserved permission.
    """
    _rules()
    try:
        policy = current_policy(connection)
        governance_policy = governance.current_policy(connection)
        payload = obligations._strict_json(proposal["payload"])
        if digest(payload) != proposal["digest"]:
            raise WorkbenchError("content_proposal_invalid", "Proposal address differs from its payload")
        current = Store._artifacts(connection)
        writes = {body["id"]: body for body in payload["writes"]}
        if len(writes) != len(payload["writes"]):
            raise WorkbenchError("content_proposal_invalid", "Duplicate proposal write IDs")
        candidate = {**current, **writes}
        descriptor = policy["policy"]
        reasons = []
        write_ids = sorted(writes)
        if write_ids != descriptor["artifact_ids"]:
            reasons.append(reason("content_proposal_write_set", expected=descriptor["artifact_ids"], actual=write_ids))
        read_set = {
            identifier: {"expected": expected,
                         "actual": digest(current[identifier]) if identifier in current else None}
            for identifier, expected in sorted(payload["readset"].items())
        }
        queries = {
            scope: {"expected": expected, "actual": Store._query_digest(current, scope)}
            for scope, expected in sorted(payload["queries"].items())
        }
        for identifier, values in read_set.items():
            if values["expected"] != values["actual"]:
                reasons.append(reason("content_stale_read", artifact_id=identifier, **values))
        for scope, values in queries.items():
            if values["expected"] != values["actual"]:
                reasons.append(reason("content_query_changed", scope=scope, **values))
        relations = [_relation(item, candidate, writes) for item in descriptor["spec"]["obligations"]]
        by_id = {item["id"]: item for item in relations}
        specifications = {item["id"]: item for item in descriptor["spec"]["obligations"]}
        for result in relations:
            if result["status"] != "match":
                reasons.append(reason("content_relation_" + result["status"], obligation_id=result["id"]))
        promises = []
        dependencies = {}
        for item in descriptor["inventory"]["promises"]:
            requirement = item["requirement"]
            anchor = coverage._anchor_v1(item["anchor"], candidate, writes)
            document = obligations._endpoint(requirement["document"], "document",
                                             requirement["value_type"], candidate, writes)
            mappings = [{
                "obligation_id": identifier,
                "requirement_digest": digest(coverage._requirement(specifications[identifier])),
                "valid": canonical(coverage._requirement(specifications[identifier])) == canonical(requirement),
                "comparison_status": by_id[identifier]["status"],
            } for identifier in item["obligation_ids"]]
            resolved = anchor["resolved"] and document["resolved"]
            matches = bool(mappings) and all(mapping["valid"] and mapping["comparison_status"] == "match"
                                            for mapping in mappings)
            promise_reasons = [{"endpoint": "quote_anchor", **value} for value in anchor["reasons"]]
            promise_reasons.extend({"endpoint": "document_field", **value} for value in document["reasons"])
            if not resolved:
                reasons.append(reason("content_promise_unresolved", promise_id=item["id"]))
            if not matches:
                reasons.append(reason("content_promise_mismatch", promise_id=item["id"]))
            promises.append({"id": item["id"], "requirement_digest": digest(requirement),
                             "anchor": anchor, "document": document, "mappings": mappings,
                             "anchor_verified": resolved, "eligible": resolved and matches,
                             "reasons": promise_reasons})
        endpoints = [endpoint for result in relations for endpoint in (result["document"], result["code"])]
        endpoints.extend(endpoint for promise in promises for endpoint in (promise["anchor"], promise["document"]))
        for endpoint in endpoints:
            for identifier, address in endpoint["dependencies"].items():
                if identifier in dependencies and dependencies[identifier] != address:
                    raise WorkbenchError("content_proposal_invalid", "Conflicting candidate dependency addresses")
                dependencies[identifier] = address
                if identifier not in payload["readset"]:
                    missing = reason("content_dependency_untracked", artifact_id=identifier)
                    if missing not in reasons:
                        reasons.append(missing)
        body = {
            "schema_version": 1, "content_rules_version": RULES_VERSION,
            "obligation_rules_version": "obligations-1", "coverage_rules_version": "coverage-1",
            "content_policy_generation": policy["generation"], "content_policy_digest": policy["policy_digest"],
            "governance_policy_generation": governance_policy["generation"],
            "governance_policy_digest": governance_policy["policy_digest"],
            "proposal_id": proposal["id"], "proposal_digest": proposal["digest"],
            "read_set": read_set, "queries": queries,
            "write_ids": write_ids, "required_write_ids": descriptor["artifact_ids"],
            "relations": relations, "promises": promises, "dependencies": dict(sorted(dependencies.items())),
            "eligible": not reasons, "reasons": reasons, "limits": list(LIMITS),
        }
        obligations._shape(body)
        if len(canonical(body).encode("utf-8")) > MAX_PACKET_BYTES:
            raise WorkbenchError("content_packet_too_large", "Content packet exceeds 4 MB")
        address = digest(body)
        packet = {**body, "packet_id": "cgp_" + address, "packet_digest": address}
        if len(canonical(packet).encode("utf-8")) > MAX_PACKET_BYTES:
            raise WorkbenchError("content_packet_too_large", "Content packet exceeds 4 MB")
        return packet
    except WorkbenchError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("content_proposal_invalid", "Cannot build a bounded content packet") from error


def save_packet(connection: sqlite3.Connection, packet: dict[str, Any]) -> dict[str, Any]:
    """Append one internally addressed packet, or return its identical record."""
    try:
        obligations._shape(packet)
        if not isinstance(packet, dict) or set(packet) != _PACKET_FIELDS:
            raise WorkbenchError("content_packet_invalid", "Unexpected packet fields")
        encoded = canonical(packet)
        if len(encoded.encode("utf-8")) > MAX_PACKET_BYTES:
            raise WorkbenchError("content_packet_too_large", "Content packet exceeds 4 MB")
        body = {key: value for key, value in packet.items() if key not in {"packet_id", "packet_digest"}}
        address = digest(body)
        if packet["packet_id"] != "cgp_" + address or packet["packet_digest"] != address:
            raise WorkbenchError("content_packet_invalid", "Packet address differs from its body")
        existing = connection.execute("SELECT * FROM content_packets WHERE packet_id=? OR digest=?",
                                      (packet["packet_id"], address)).fetchone()
        if existing is not None:
            if (existing["body"] != encoded or existing["digest"] != address
                    or existing["proposal_id"] != packet["proposal_id"]
                    or existing["content_policy_generation"] != packet["content_policy_generation"]):
                raise WorkbenchError("content_packet_invalid", "Existing packet address names different data")
            return {"packet_id": packet["packet_id"], "packet_digest": address, "replayed": True}
        proposal = Store._find_proposal(connection, packet["proposal_id"])
        if proposal["digest"] != packet["proposal_digest"]:
            raise WorkbenchError("content_packet_invalid", "Packet pins a different proposal")
        policy = connection.execute("SELECT digest FROM content_policies WHERE generation=?",
                                    (packet["content_policy_generation"],)).fetchone()
        if policy is None or policy[0] != packet["content_policy_digest"]:
            raise WorkbenchError("content_packet_invalid", "Packet pins an unknown content policy")
        governance_policy = connection.execute("SELECT digest FROM governance_policies WHERE generation=?",
                                               (packet["governance_policy_generation"],)).fetchone()
        if governance_policy is None or governance_policy[0] != packet["governance_policy_digest"]:
            raise WorkbenchError("content_packet_invalid", "Packet pins an unknown governance policy")
        connection.execute("INSERT INTO content_packets VALUES (?,?,?,?,?)",
                           (packet["packet_id"], address, packet["proposal_id"],
                            packet["content_policy_generation"], encoded))
        return {"packet_id": packet["packet_id"], "packet_digest": address, "replayed": False}
    except WorkbenchError:
        raise
    except (KeyError, TypeError, ValueError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("content_packet_invalid", "Expected a bounded, internally addressed content packet") from error


def load_packet(connection: sqlite3.Connection, packet_id: Any) -> dict[str, Any]:
    """Read an exact historical packet without recomputing live eligibility.

    Address, shape and historical references are checked. This establishes
    internal consistency, not a signature or renewed permission to accept.
    """
    if not isinstance(packet_id, str) or not re.fullmatch(r"cgp_[0-9a-f]{64}", packet_id):
        raise WorkbenchError("content_packet_invalid", "Expected a cgp_ address with 64 lowercase hexadecimal digits")
    size = connection.execute("SELECT length(CAST(body AS BLOB)),typeof(body) FROM content_packets WHERE packet_id=?",
                              (packet_id,)).fetchone()
    if size is None:
        raise WorkbenchError("content_packet_not_found", "No recorded content packet has this address")
    if size[1] != "text" or size[0] > MAX_PACKET_BYTES:
        raise WorkbenchError("content_packet_invalid", "Recorded packet is not bounded UTF-8 JSON text")
    try:
        row = connection.execute("SELECT * FROM content_packets WHERE packet_id=?", (packet_id,)).fetchone()
        packet = obligations._strict_json(row["body"])
        if not isinstance(packet, dict) or set(packet) != _PACKET_FIELDS:
            raise WorkbenchError("content_packet_invalid", "Unexpected recorded packet fields")
        body = {key: value for key, value in packet.items() if key not in {"packet_id", "packet_digest"}}
        address = digest(body)
        if (canonical(packet) != row["body"] or packet["packet_id"] != packet_id
                or packet_id != "cgp_" + address or packet["packet_digest"] != address
                or row["digest"] != address or row["proposal_id"] != packet["proposal_id"]
                or row["content_policy_generation"] != packet["content_policy_generation"]):
            raise WorkbenchError("content_packet_invalid", "Recorded packet address or row pins differ from its body")
        if (type(packet["schema_version"]) is not int or packet["schema_version"] != 1
                or packet["content_rules_version"] != "content-gate-1"
                or packet["obligation_rules_version"] != "obligations-1"
                or packet["coverage_rules_version"] != "coverage-1"
                or type(packet["content_policy_generation"]) is not int or packet["content_policy_generation"] < 1
                or type(packet["governance_policy_generation"]) is not int or packet["governance_policy_generation"] < 1
                or type(packet["eligible"]) is not bool or not isinstance(packet["reasons"], list)
                or packet["eligible"] != (not packet["reasons"])
                or not isinstance(packet["relations"], list) or not 1 <= len(packet["relations"]) <= MAX_OBLIGATIONS
                or not isinstance(packet["promises"], list) or not 1 <= len(packet["promises"]) <= MAX_PROMISES
                or not isinstance(packet["read_set"], dict) or not isinstance(packet["queries"], dict)
                or not isinstance(packet["dependencies"], dict)
                or not isinstance(packet["limits"], list) or packet["limits"] != LIMITS):
            raise WorkbenchError("content_packet_invalid", "Unsupported recorded packet schema or authority flags")
        for field in ("content_policy_digest", "governance_policy_digest", "proposal_digest"):
            if not isinstance(packet[field], str) or not obligations.HASH.fullmatch(packet[field]):
                raise WorkbenchError("content_packet_invalid", "Invalid recorded reference digest")
        for field in ("write_ids", "required_write_ids"):
            identifiers = packet[field]
            if (not isinstance(identifiers, list) or not all(isinstance(item, str) for item in identifiers)
                    or identifiers != sorted(set(identifiers))):
                raise WorkbenchError("content_packet_invalid", "Invalid recorded artifact allowlist")
        obligations._reasons(packet["reasons"])
        proposal = connection.execute("SELECT digest,payload FROM proposals WHERE id=?",
                                      (packet["proposal_id"],)).fetchone()
        if (proposal is None or proposal["digest"] != packet["proposal_digest"]
                or len(proposal["payload"].encode("utf-8")) > MAX_POLICY_BYTES
                or digest(obligations._strict_json(proposal["payload"])) != proposal["digest"]):
            raise WorkbenchError("content_packet_invalid", "Historical proposal reference differs from its recorded body")
        for table, generation_field, digest_field in (
            ("content_policies", "content_policy_generation", "content_policy_digest"),
            ("governance_policies", "governance_policy_generation", "governance_policy_digest"),
        ):
            # table comes only from the two fixed schema names above.
            policy = connection.execute("SELECT body,digest FROM " + table + " WHERE generation=?",
                                        (packet[generation_field],)).fetchone()
            if (policy is None or policy["digest"] != packet[digest_field]
                    or len(policy["body"].encode("utf-8")) > MAX_POLICY_BYTES):
                raise WorkbenchError("content_packet_invalid", "Historical policy reference is missing or too large")
            descriptor = obligations._strict_json(policy["body"])
            if canonical(descriptor) != policy["body"] or digest(descriptor) != policy["digest"]:
                raise WorkbenchError("content_packet_invalid", "Historical policy address differs from its recorded body")
        return packet
    except WorkbenchError as error:
        if error.code == "content_packet_invalid":
            raise
        raise WorkbenchError("content_packet_invalid", "Recorded packet or historical reference is malformed") from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("content_packet_invalid", "Recorded packet or historical reference is malformed") from error


def status(connection: sqlite3.Connection, proposal_id: str | None = None) -> dict[str, Any]:
    result = {"enabled": enabled(connection), "rules_version": RULES_VERSION,
              "trusted_database_owner_can_bypass": True, "limits": list(LIMITS)}
    if not result["enabled"]:
        return result
    settings = connection.execute("SELECT * FROM content_settings WHERE singleton=1").fetchone()
    result.update({"current_policy": current_policy(connection),
                   "activation": {key: settings[key] for key in settings.keys() if key != "singleton"}})
    parameters = (proposal_id,) if proposal_id is not None else ()
    where = " WHERE proposal_id=?" if proposal_id is not None else ""
    count = connection.execute("SELECT COUNT(*) FROM content_packets" + where, parameters).fetchone()[0]
    rows = connection.execute("SELECT packet_id,digest,proposal_id,content_policy_generation FROM content_packets"
                              + where + " ORDER BY rowid DESC LIMIT ?", (*parameters, MAX_STATUS_PACKETS))
    result["stored_packets"] = [{"packet_id": row["packet_id"], "packet_digest": row["digest"],
                                  "proposal_id": row["proposal_id"],
                                  "content_policy_generation": row["content_policy_generation"]} for row in rows]
    result["stored_packet_count"] = count
    result["stored_packets_truncated"] = count > MAX_STATUS_PACKETS
    if proposal_id is not None:
        proposal = Store._find_proposal(connection, proposal_id)
        packet = build_packet(connection, proposal)
        result.update({"proposal_id": proposal_id, "packet": packet,
                       "stored": connection.execute("SELECT 1 FROM content_packets WHERE packet_id=?",
                                                     (packet["packet_id"],)).fetchone() is not None})
    return result
