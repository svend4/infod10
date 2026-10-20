"""Incremental advisory inspection of a frozen local continuity history.

This journal has no admission authority. It never changes opinions, policy
generations, runtime revisions or the bounded review_continuity matcher. The
caller supplies one existing Store transaction for each operation. Hashes name
recorded observations; SQLite and its owner remain trusted.
"""

from __future__ import annotations

import math
import re
import sqlite3
import time
from typing import Any

from .store import HASH, Store, WorkbenchError, canonical, digest


RULES_VERSION = "review-history-audit-1"
MAX_STEP_TRANSITIONS = 64
MAX_DECISION_BYTES = 1_000_000
MAX_ROOT_BYTES = 64_000
MAX_CHECKPOINT_BYTES = 256_000
MAX_OPERATION_BYTES = 4_000
FLAGS = {"advisory_only": True, "acceptance_authority": False, "authority_evaluated": False}
STATES = {"pending", "history_complete", "scope_changed", "governance_reset", "history_invalid"}
CURSOR_FIELDS = {"governance_generation", "governance_policy_digest",
                 "content_policy_generation", "content_policy_digest"}
PIN_FIELDS = {"packet_id", "packet_digest", "content_policy_generation", "content_policy_digest"}
ROOT_FIELDS = {
    "schema_version", "rules_version", "activation_generation", "source_decision_id",
    "source_decision_digest", "proposal_id", "proposal_digest", "source_packet", "target_packet",
    "source_governance_generation", "source_governance_policy_digest",
    "target_governance_generation", "target_governance_policy_digest", "basis_id", "basis_digest",
    "scope_digest", "write_ids", "created_at", "administrator_grant_id", "operation_id",
    *FLAGS,
}
CHECKPOINT_FIELDS = {
    "schema_version", "rules_version", "audit_id", "audit_digest", "index",
    "previous_checkpoint_id", "previous_checkpoint_digest", "from_cursor", "to_cursor",
    "requested_limit", "examined_transition_count", "checked_transition_count",
    "total_checked_transition_count", "transitions", "stopped_at", "state", "operation_id",
    "created_at", "administrator_grant_id", *FLAGS,
}
LIMITS = [
    "This audit describes a frozen source decision and target packet; later catalog or artifact changes do not retarget it.",
    "Each advance examines at most 64 chronological increments; the journal is not one atomic snapshot across all advances.",
    "History completion does not validate a current reviewer grant, opinion head, quorum, runtime freshness or acceptance permission.",
    "The audit does not change review_continuity.MAX_TRANSITIONS or supply an admission certificate.",
    "The SQLite owner and application remain trusted; addressed hashes are not signatures or external attestations.",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_history_audits (
    audit_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE, body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_history_audit_checkpoints (
    checkpoint_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
    audit_id TEXT NOT NULL REFERENCES review_history_audits(audit_id),
    checkpoint_index INTEGER NOT NULL, body TEXT NOT NULL,
    UNIQUE(audit_id,checkpoint_index)
);
CREATE TABLE IF NOT EXISTS review_history_audit_heads (
    audit_id TEXT PRIMARY KEY REFERENCES review_history_audits(audit_id),
    checkpoint_id TEXT NOT NULL REFERENCES review_history_audit_checkpoints(checkpoint_id)
);
CREATE TABLE IF NOT EXISTS review_history_audit_operations (
    operation_id TEXT PRIMARY KEY, action TEXT NOT NULL,
    administrator_grant_id TEXT NOT NULL, request_digest TEXT NOT NULL,
    audit_id TEXT NOT NULL REFERENCES review_history_audits(audit_id),
    checkpoint_id TEXT NOT NULL REFERENCES review_history_audit_checkpoints(checkpoint_id)
);
CREATE INDEX IF NOT EXISTS content_policies_governance_generation
    ON content_policies(governance_policy_generation);
"""


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise WorkbenchError(code, detail)


def _integer(value: Any, *, zero: bool = False) -> bool:
    return type(value) is int and (0 if zero else 1) <= value < 2**63


def _hash(value: Any) -> bool:
    return isinstance(value, str) and bool(HASH.fullmatch(value))


def _address(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(prefix + r"_[0-9a-f]{64}", value))


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _input_address(value: Any, prefix: str) -> None:
    _require(_address(value, prefix), "audit_invalid_input", "Expected an exact addressed identifier")


def _operation_id(value: Any) -> None:
    _require(isinstance(value, str) and bool(re.fullmatch(r"[a-z][a-z0-9_.-]{0,100}", value)),
             "audit_invalid_input", "Expected a bounded operation ID")


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value.get(key)) is bool and value[key] is expected for key, expected in FLAGS.items())


def _pin(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == PIN_FIELDS
            and _address(value["packet_id"], "cgp") and _hash(value["packet_digest"])
            and value["packet_id"] == "cgp_" + value["packet_digest"]
            and _integer(value["content_policy_generation"]) and _hash(value["content_policy_digest"]))


def _cursor(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == CURSOR_FIELDS
            and _integer(value["governance_generation"]) and _hash(value["governance_policy_digest"])
            and _integer(value["content_policy_generation"]) and _hash(value["content_policy_digest"]))


def _packet_cursor(packet: dict[str, Any]) -> dict[str, Any]:
    return {"governance_generation": packet["governance_policy_generation"],
            "governance_policy_digest": packet["governance_policy_digest"],
            "content_policy_generation": packet["content_policy_generation"],
            "content_policy_digest": packet["content_policy_digest"]}


def _root_cursor(root: dict[str, Any], side: str) -> dict[str, Any]:
    packet = root[side + "_packet"]
    return {"governance_generation": root[side + "_governance_generation"],
            "governance_policy_digest": root[side + "_governance_policy_digest"],
            "content_policy_generation": packet["content_policy_generation"],
            "content_policy_digest": packet["content_policy_digest"]}


def _bounded(value: Any, maximum: int, code: str) -> str:
    from . import obligations

    try:
        obligations._shape(value)
        encoded = canonical(value)
        _require(len(encoded.encode("utf-8")) <= maximum, code, "Audit record exceeds its byte bound")
        return encoded
    except WorkbenchError as error:
        if error.code == code:
            raise
        raise WorkbenchError(code, "Audit record has unsupported JSON structure") from error
    except (TypeError, ValueError, RecursionError, UnicodeError, OverflowError) as error:
        raise WorkbenchError(code, "Audit record has unsupported JSON structure") from error


def _read_body(connection: sqlite3.Connection, table: str, key: str,
               identifier: str, maximum: int) -> sqlite3.Row:
    # All table and key names below are fixed application constants.
    size = connection.execute("SELECT length(CAST(body AS BLOB)),typeof(body) FROM " + table
                              + " WHERE " + key + "=?", (identifier,)).fetchone()
    _require(size is not None and size[1] == "text" and size[0] <= maximum,
             "audit_record_invalid", "Audit record is missing or exceeds its bound")
    return connection.execute("SELECT * FROM " + table + " WHERE " + key + "=?", (identifier,)).fetchone()


def _parse(raw: str, maximum: int) -> dict[str, Any]:
    from . import obligations

    try:
        value = obligations._strict_json(raw)
        _require(isinstance(value, dict) and _bounded(value, maximum, "audit_record_invalid") == raw,
                 "audit_record_invalid", "Expected canonical audit JSON")
        return value
    except WorkbenchError as error:
        if error.code == "audit_record_invalid":
            raise
        raise WorkbenchError("audit_record_invalid", "Malformed audit JSON") from error


def _load_source(connection: sqlite3.Connection, decision_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from . import content_gate, review_continuity as continuity

    row = _read_body(connection, "governance_decisions", "decision_id", decision_id, MAX_DECISION_BYTES)
    body = _parse(row["body"], MAX_DECISION_BYTES)
    addressed = {key: value for key, value in body.items() if key != "decision_id"}
    _require(body.get("decision_id") == decision_id and decision_id == "gvd_" + digest(addressed)
             and all(body.get(key) == row[key] for key in ("proposal_id", "artifact_id", "principal_id", "grant_id"))
             and type(body.get("schema_version")) is int and body["schema_version"] == 3,
             "audit_record_invalid", "Source decision address or stored columns differ")
    settings = continuity._settings(connection)
    stamp = body.get("review_continuity")
    pin = body.get("content_packet")
    _require(_pin(pin), "audit_record_invalid", "Source decision has no exact content pin")
    old = content_gate.load_packet(connection, pin["packet_id"])
    old_basis = continuity.basis(old)
    _require(_same(pin, continuity._pin(old)) and body.get("content_packet_digest") == old["packet_digest"]
             and _same(stamp, continuity._stamp(settings, old_basis))
             and body["proposal_id"] == old["proposal_id"] and body.get("proposal_digest") == old["proposal_digest"]
             and _integer(body.get("policy_generation"))
             and body["policy_generation"] == old["governance_policy_generation"]
             and body["policy_generation"] >= settings["activation_generation"]
             and body.get("policy_digest") == old["governance_policy_digest"]
             and body["artifact_id"] in old["write_ids"],
             "audit_record_invalid", "Source decision provenance differs from its saved packet")
    return body, old


def _load_root(connection: sqlite3.Connection, audit_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    from . import content_gate, review_continuity as continuity

    try:
        _require(_address(audit_id, "rha"), "audit_record_invalid", "Invalid audit address")
        row = _read_body(connection, "review_history_audits", "audit_id", audit_id, MAX_ROOT_BYTES)
        body = _parse(row["body"], MAX_ROOT_BYTES)
        address = digest(body)
        _require(set(body) == ROOT_FIELDS and row["digest"] == address and audit_id == "rha_" + address
                 and type(body["schema_version"]) is int and body["schema_version"] == 1
                 and body["rules_version"] == RULES_VERSION and _flags(body)
                 and _integer(body["activation_generation"])
                 and _address(body["source_decision_id"], "gvd") and _hash(body["source_decision_digest"])
                 and _pin(body["source_packet"]) and _pin(body["target_packet"])
                 and _integer(body["source_governance_generation"])
                 and _integer(body["target_governance_generation"])
                 and body["target_governance_generation"] >= body["source_governance_generation"] >= body["activation_generation"]
                 and _hash(body["source_governance_policy_digest"]) and _hash(body["target_governance_policy_digest"])
                 and _address(body["basis_id"], "rcb") and _hash(body["basis_digest"])
                 and body["basis_id"] == "rcb_" + body["basis_digest"] and _hash(body["scope_digest"])
                 and type(body["created_at"]) in {int, float} and math.isfinite(body["created_at"])
                 and isinstance(body["administrator_grant_id"], str)
                 and bool(re.fullmatch(r"gvg_[0-9a-f]{32}", body["administrator_grant_id"])),
                 "audit_record_invalid", "Audit root fields or address differ")
        _operation_id(body["operation_id"])
        source, old = _load_source(connection, body["source_decision_id"])
        target = content_gate.load_packet(connection, body["target_packet"]["packet_id"])
        old_basis, target_basis = continuity.basis(old), continuity.basis(target)
        _require(digest(source) == body["source_decision_digest"]
                 and _same(continuity._pin(old), body["source_packet"])
                 and _same(continuity._pin(target), body["target_packet"])
                 and old["governance_policy_generation"] == body["source_governance_generation"]
                 and old["governance_policy_digest"] == body["source_governance_policy_digest"]
                 and target["governance_policy_generation"] == body["target_governance_generation"]
                 and target["governance_policy_digest"] == body["target_governance_policy_digest"]
                 and old["proposal_id"] == target["proposal_id"] == body["proposal_id"]
                 and old["proposal_digest"] == target["proposal_digest"] == body["proposal_digest"]
                 and _same(old["write_ids"], body["write_ids"])
                 and _same(old_basis, target_basis)
                 and old_basis["basis_id"] == body["basis_id"] and old_basis["basis_digest"] == body["basis_digest"]
                 and continuity._settings(connection)["activation_generation"] == body["activation_generation"],
                 "audit_record_invalid", "Frozen audit inputs no longer match their addresses")
        original_policy = continuity._policy_record(connection, "content", old["content_policy_generation"])
        scope = continuity._scope(original_policy["policy"], body["write_ids"])
        _require(digest(scope) == body["scope_digest"], "audit_record_invalid", "Frozen scope digest differs")
        source_cursor, target_cursor = _root_cursor(body, "source"), _root_cursor(body, "target")
        _require(target_cursor["content_policy_generation"] >= source_cursor["content_policy_generation"]
                 and (source_cursor["governance_generation"] != target_cursor["governance_generation"]
                      or _same(source_cursor, target_cursor)),
                 "audit_record_invalid", "Frozen target precedes its source")
        return {**body, "audit_id": audit_id, "audit_digest": address}, scope
    except (WorkbenchError, KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("audit_record_invalid", "Audit root or frozen references are malformed") from error


def _embedded_edge(value: Any) -> bool:
    from . import review_continuity as continuity

    if not isinstance(value, dict) or set(value) != continuity.TRANSITION_FIELDS | {"transition_id", "transition_digest"}:
        return False
    body = {key: child for key, child in value.items() if key not in {"transition_id", "transition_digest"}}
    address = digest(body)
    return (value["transition_id"] == "rct_" + address and value["transition_digest"] == address
            and type(body["schema_version"]) is int and body["schema_version"] == 1
            and body["rules_version"] == continuity.RULES_VERSION
            and all(_integer(body[key]) for key in (
                "activation_generation", "from_content_policy_generation", "to_content_policy_generation",
                "from_governance_policy_generation", "to_governance_policy_generation"))
            and all(_hash(body[key]) for key in (
                "from_content_policy_digest", "to_content_policy_digest",
                "from_governance_policy_digest", "to_governance_policy_digest"))
            and type(body["created_at"]) in {int, float} and math.isfinite(body["created_at"])
            and isinstance(body["administrator_grant_id"], str)
            and bool(re.fullmatch(r"gvg_[0-9a-f]{32}", body["administrator_grant_id"]))
            and body["from_governance_policy_digest"] == body["to_governance_policy_digest"]
            and body["to_content_policy_generation"] == body["from_content_policy_generation"] + 1
            and body["to_governance_policy_generation"] == body["from_governance_policy_generation"] + 1)


def _edge_cursor(edge: dict[str, Any], side: str) -> dict[str, Any]:
    return {"governance_generation": edge[side + "_governance_policy_generation"],
            "governance_policy_digest": edge[side + "_governance_policy_digest"],
            "content_policy_generation": edge[side + "_content_policy_generation"],
            "content_policy_digest": edge[side + "_content_policy_digest"]}


def _load_checkpoint(connection: sqlite3.Connection, checkpoint_id: str,
                     root: dict[str, Any], *, previous: bool = True) -> dict[str, Any]:
    try:
        _require(_address(checkpoint_id, "rhs"), "audit_record_invalid", "Invalid checkpoint address")
        row = _read_body(connection, "review_history_audit_checkpoints", "checkpoint_id", checkpoint_id, MAX_CHECKPOINT_BYTES)
        body = _parse(row["body"], MAX_CHECKPOINT_BYTES)
        address = digest(body)
        _require(set(body) == CHECKPOINT_FIELDS and row["digest"] == address
                 and checkpoint_id == "rhs_" + address and row["audit_id"] == root["audit_id"]
                 and body["audit_id"] == root["audit_id"] and body["audit_digest"] == root["audit_digest"]
                 and type(body["schema_version"]) is int and body["schema_version"] == 1
                 and body["rules_version"] == RULES_VERSION and _flags(body)
                 and _integer(body["index"], zero=True) and row["checkpoint_index"] == body["index"]
                 and body["state"] in STATES and _cursor(body["from_cursor"]) and _cursor(body["to_cursor"])
                 and type(body["created_at"]) in {int, float} and math.isfinite(body["created_at"])
                 and isinstance(body["administrator_grant_id"], str)
                 and bool(re.fullmatch(r"gvg_[0-9a-f]{32}", body["administrator_grant_id"])),
                 "audit_record_invalid", "Checkpoint fields or address differ")
        _operation_id(body["operation_id"])
        counts = (body["requested_limit"], body["examined_transition_count"], body["checked_transition_count"],
                  body["total_checked_transition_count"])
        _require(all(_integer(value, zero=True) for value in counts)
                 and isinstance(body["transitions"], list) and len(body["transitions"]) <= MAX_STEP_TRANSITIONS
                 and body["checked_transition_count"] == len(body["transitions"])
                 and body["checked_transition_count"] <= body["examined_transition_count"] <= body["requested_limit"]
                 and body["total_checked_transition_count"] >= body["checked_transition_count"],
                 "audit_record_invalid", "Checkpoint counts differ")
        source, target = _root_cursor(root, "source"), _root_cursor(root, "target")
        from_total = body["total_checked_transition_count"] - body["checked_transition_count"]
        _require(body["from_cursor"]["governance_generation"] == source["governance_generation"] + from_total
                 and body["to_cursor"]["governance_generation"] == source["governance_generation"] + body["total_checked_transition_count"]
                 and body["from_cursor"]["content_policy_generation"] == source["content_policy_generation"] + from_total
                 and body["to_cursor"]["content_policy_generation"] == source["content_policy_generation"] + body["total_checked_transition_count"]
                 and body["from_cursor"]["governance_policy_digest"] == source["governance_policy_digest"]
                 and body["to_cursor"]["governance_policy_digest"] == source["governance_policy_digest"]
                 and body["to_cursor"]["governance_generation"] <= target["governance_generation"],
                 "audit_record_invalid", "Checkpoint cursor skips its counted range")
        cursor = body["from_cursor"]
        for edge in body["transitions"]:
            _require(_embedded_edge(edge) and edge["activation_generation"] == root["activation_generation"]
                     and _same(_edge_cursor(edge, "from"), cursor),
                     "audit_record_invalid", "Checkpoint transition witness differs")
            cursor = _edge_cursor(edge, "to")
        _require(_same(cursor, body["to_cursor"]), "audit_record_invalid", "Checkpoint endpoint differs from its witnesses")
        stopped = body["stopped_at"]
        if body["index"] == 0:
            _require(body["previous_checkpoint_id"] is None and body["previous_checkpoint_digest"] is None
                     and all(value == 0 for value in counts) and not body["transitions"] and stopped is None
                     and _same(body["from_cursor"], source) and _same(body["to_cursor"], source)
                     and body["operation_id"] == root["operation_id"]
                     and body["administrator_grant_id"] == root["administrator_grant_id"]
                     and _same(body["created_at"], root["created_at"])
                     and body["state"] == ("history_complete" if _same(source, target) else "pending"),
                     "audit_record_invalid", "Invalid initial checkpoint")
        else:
            _require(_address(body["previous_checkpoint_id"], "rhs") and _hash(body["previous_checkpoint_digest"])
                     and body["previous_checkpoint_id"] == "rhs_" + body["previous_checkpoint_digest"]
                     and 1 <= body["requested_limit"] <= MAX_STEP_TRANSITIONS,
                     "audit_record_invalid", "Invalid checkpoint predecessor or budget")
            if body["state"] in {"pending", "history_complete"}:
                _require(stopped is None and body["examined_transition_count"] == body["checked_transition_count"]
                         and body["checked_transition_count"] > 0
                         and (body["state"] != "pending" or body["examined_transition_count"] == body["requested_limit"])
                         and (body["state"] != "history_complete" or _same(body["to_cursor"], target))
                         and (body["state"] != "pending" or body["to_cursor"]["governance_generation"] < target["governance_generation"]),
                         "audit_record_invalid", "Invalid checkpoint completion state")
            else:
                _require(isinstance(stopped, dict) and set(stopped) == {"governance_generation", "code", "transition"}
                         and _integer(stopped["governance_generation"])
                         and stopped["governance_generation"] == body["to_cursor"]["governance_generation"]
                         and stopped["governance_generation"] < target["governance_generation"]
                         and stopped["code"] == "audit_" + body["state"]
                         and (stopped["transition"] is None or (
                             isinstance(stopped["transition"], dict)
                             and set(stopped["transition"]) == {"transition_id", "transition_digest"}
                             and _address(stopped["transition"]["transition_id"], "rct")
                             and _hash(stopped["transition"]["transition_digest"])
                             and stopped["transition"]["transition_id"] == "rct_" + stopped["transition"]["transition_digest"]))
                         and (body["state"] != "scope_changed" or stopped["transition"] is not None)
                         and (body["state"] != "governance_reset" or stopped["transition"] is None)
                         and body["examined_transition_count"] == body["checked_transition_count"] + 1,
                         "audit_record_invalid", "Invalid checkpoint stop witness")
        if previous and body["index"]:
            predecessor = _load_checkpoint(connection, body["previous_checkpoint_id"], root, previous=False)
            _require(predecessor["checkpoint_digest"] == body["previous_checkpoint_digest"]
                     and predecessor["index"] + 1 == body["index"] and predecessor["state"] == "pending"
                     and _same(predecessor["to_cursor"], body["from_cursor"])
                     and predecessor["total_checked_transition_count"] + body["checked_transition_count"] == body["total_checked_transition_count"],
                     "audit_record_invalid", "Checkpoint predecessor differs")
        return {**body, "checkpoint_id": checkpoint_id, "checkpoint_digest": address}
    except (WorkbenchError, KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("audit_record_invalid", "Checkpoint or bounded predecessor is malformed") from error


def _head(connection: sqlite3.Connection, root: dict[str, Any]) -> dict[str, Any]:
    size = connection.execute("SELECT length(CAST(checkpoint_id AS BLOB)),typeof(checkpoint_id)"
                              " FROM review_history_audit_heads WHERE audit_id=?", (root["audit_id"],)).fetchone()
    _require(size is not None and size[0] == 68 and size[1] == "text",
             "audit_record_invalid", "Audit head is missing or unbounded")
    row = connection.execute("SELECT checkpoint_id FROM review_history_audit_heads WHERE audit_id=?", (root["audit_id"],)).fetchone()
    return _load_checkpoint(connection, row[0], root)


def _operation(connection: sqlite3.Connection, operation_id: str, action: str,
               administrator: dict[str, Any], request: dict[str, Any]) -> sqlite3.Row | None:
    _operation_id(operation_id)
    _bounded(request, MAX_OPERATION_BYTES, "audit_invalid_input")
    size = connection.execute("SELECT length(CAST(action AS BLOB)),typeof(action),"
                              "length(CAST(administrator_grant_id AS BLOB)),typeof(administrator_grant_id),"
                              "length(CAST(request_digest AS BLOB)),typeof(request_digest),"
                              "length(CAST(audit_id AS BLOB)),typeof(audit_id),"
                              "length(CAST(checkpoint_id AS BLOB)),typeof(checkpoint_id)"
                              " FROM review_history_audit_operations WHERE operation_id=?", (operation_id,)).fetchone()
    if size is None:
        return None
    _require(all(size[index + 1] == "text" and size[index] <= maximum
                 for index, maximum in ((0, 7), (2, 36), (4, 64), (6, 68), (8, 68))),
             "audit_record_invalid", "Audit operation map exceeds its field bounds")
    row = connection.execute("SELECT * FROM review_history_audit_operations WHERE operation_id=?", (operation_id,)).fetchone()
    _require(row["action"] == action and row["administrator_grant_id"] == administrator["grant_id"]
             and row["request_digest"] == digest(request),
             "audit_idempotency_conflict", "Operation ID already identifies different audit input")
    return row


def _store_operation(connection: sqlite3.Connection, operation_id: str, action: str,
                     administrator: dict[str, Any], request: dict[str, Any], audit_id: str,
                     checkpoint_id: str) -> None:
    connection.execute("INSERT INTO review_history_audit_operations VALUES (?,?,?,?,?,?)",
                       (operation_id, action, administrator["grant_id"], digest(request), audit_id, checkpoint_id))


def _save_checkpoint(connection: sqlite3.Connection, body: dict[str, Any]) -> dict[str, Any]:
    encoded = _bounded(body, MAX_CHECKPOINT_BYTES, "audit_record_invalid")
    address = digest(body)
    identifier = "rhs_" + address
    connection.execute("INSERT INTO review_history_audit_checkpoints VALUES (?,?,?,?,?)",
                       (identifier, address, body["audit_id"], body["index"], encoded))
    return {**body, "checkpoint_id": identifier, "checkpoint_digest": address}


def _result(connection: sqlite3.Connection, root: dict[str, Any],
            checkpoint: dict[str, Any], *, replayed: bool = False) -> dict[str, Any]:
    current = _head(connection, root)
    return {"audit": root, "checkpoint": checkpoint, "current_checkpoint_id": current["checkpoint_id"],
            "replayed": replayed, **FLAGS, "limits": list(LIMITS)}


def _fresh_administrator(connection: sqlite3.Connection, credential: Any,
                         administrator: dict[str, Any]) -> None:
    from . import governance

    fresh = governance.authorize(connection, credential, "administrator")
    _require(fresh["grant_id"] == administrator["grant_id"], "governance_unauthorized", "Administrator grant changed")


def _replay(connection: sqlite3.Connection, operation: sqlite3.Row,
            administrator: dict[str, Any], request: dict[str, Any], *, credential: Any) -> dict[str, Any]:
    root, _ = _load_root(connection, operation["audit_id"])
    checkpoint = _load_checkpoint(connection, operation["checkpoint_id"], root)
    _require(checkpoint["operation_id"] == operation["operation_id"]
             and checkpoint["administrator_grant_id"] == operation["administrator_grant_id"],
             "audit_record_invalid", "Operation map differs from its immutable checkpoint")
    if operation["action"] == "start":
        _require(root["operation_id"] == operation["operation_id"] and checkpoint["index"] == 0
                 and root["administrator_grant_id"] == operation["administrator_grant_id"]
                 and root["source_decision_id"] == request["decision_id"]
                 and root["target_governance_generation"] == request["expected_policy_generation"]
                 and root["target_packet"]["packet_digest"] == request["expected_target_packet_digest"],
                 "audit_record_invalid", "Start operation differs from its immutable result")
    else:
        _require(operation["action"] == "advance" and root["audit_id"] == request["audit_id"]
                 and checkpoint["index"] > 0
                 and checkpoint["previous_checkpoint_id"] == request["expected_checkpoint_id"]
                 and checkpoint["requested_limit"] == request["limit"],
                 "audit_record_invalid", "Advance operation differs from its immutable result")
    result = _result(connection, root, checkpoint, replayed=True)
    _fresh_administrator(connection, credential, administrator)
    return result


def start(connection: sqlite3.Connection, decision_id: str, *, credential: Any,
          expected_policy_generation: Any, expected_target_packet_digest: Any,
          operation_id: str) -> dict[str, Any]:
    from . import content_gate, governance, review_continuity as continuity

    administrator = governance.authorize(connection, credential, "administrator")
    _input_address(decision_id, "gvd")
    _require(_integer(expected_policy_generation) and _hash(expected_target_packet_digest),
             "audit_invalid_input", "Expected an exact policy generation and target packet digest")
    request = {"decision_id": decision_id, "expected_policy_generation": expected_policy_generation,
               "expected_target_packet_digest": expected_target_packet_digest,
               "administrator_grant_id": administrator["grant_id"]}
    old_operation = _operation(connection, operation_id, "start", administrator, request)
    if old_operation is not None:
        return _replay(connection, old_operation, administrator, request, credential=credential)
    current = governance.current_policy(connection)
    _require(current["generation"] == expected_policy_generation, "audit_policy_changed", "Expected current governance generation")
    try:
        source, old = _load_source(connection, decision_id)
        settings = continuity._settings(connection)
        proposal = Store._find_proposal(connection, source["proposal_id"])
        target = content_gate.build_packet(connection, proposal)
        _require(target["packet_digest"] == expected_target_packet_digest
                 and target["governance_policy_generation"] == expected_policy_generation,
                 "audit_target_changed", "Expected target differs from the current packet")
        old_basis, target_basis = continuity.basis(old), continuity.basis(target)
        _require(_same(old_basis, target_basis), "audit_target_changed", "The whole frozen review basis differs")
        _require(target["governance_policy_generation"] >= old["governance_policy_generation"]
                 and target["content_policy_generation"] >= old["content_policy_generation"],
                 "audit_source_invalid", "Target precedes its source")
        original_policy = continuity._policy_record(connection, "content", old["content_policy_generation"])
        scope = continuity._scope(original_policy["policy"], old["write_ids"])
        source_cursor, target_cursor = _packet_cursor(old), _packet_cursor(target)
        _require(source_cursor["governance_generation"] != target_cursor["governance_generation"]
                 or _same(source_cursor, target_cursor), "audit_source_invalid", "Inconsistent zero-length history")
    except WorkbenchError as error:
        if error.code in {"audit_target_changed", "audit_source_invalid"}:
            raise
        raise WorkbenchError("audit_source_invalid", "Source decision or frozen target is malformed") from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("audit_source_invalid", "Source decision or frozen target is malformed") from error
    _fresh_administrator(connection, credential, administrator)
    content_gate.save_packet(connection, target)
    now = time.time()
    body = {"schema_version": 1, "rules_version": RULES_VERSION,
            "activation_generation": settings["activation_generation"],
            "source_decision_id": decision_id, "source_decision_digest": digest(source),
            "proposal_id": source["proposal_id"], "proposal_digest": source["proposal_digest"],
            "source_packet": continuity._pin(old), "target_packet": continuity._pin(target),
            "source_governance_generation": old["governance_policy_generation"],
            "source_governance_policy_digest": old["governance_policy_digest"],
            "target_governance_generation": target["governance_policy_generation"],
            "target_governance_policy_digest": target["governance_policy_digest"],
            "basis_id": old_basis["basis_id"], "basis_digest": old_basis["basis_digest"],
            "scope_digest": digest(scope), "write_ids": old["write_ids"],
            "created_at": now, "administrator_grant_id": administrator["grant_id"],
            "operation_id": operation_id, **FLAGS}
    encoded = _bounded(body, MAX_ROOT_BYTES, "audit_record_invalid")
    address = digest(body)
    root = {**body, "audit_id": "rha_" + address, "audit_digest": address}
    connection.execute("INSERT INTO review_history_audits VALUES (?,?,?)", (root["audit_id"], address, encoded))
    checkpoint_body = {"schema_version": 1, "rules_version": RULES_VERSION,
                       "audit_id": root["audit_id"], "audit_digest": address, "index": 0,
                       "previous_checkpoint_id": None, "previous_checkpoint_digest": None,
                       "from_cursor": source_cursor, "to_cursor": source_cursor,
                       "requested_limit": 0, "examined_transition_count": 0, "checked_transition_count": 0,
                       "total_checked_transition_count": 0, "transitions": [], "stopped_at": None,
                       "state": "history_complete" if _same(source_cursor, target_cursor) else "pending",
                       "operation_id": operation_id, "created_at": now,
                       "administrator_grant_id": administrator["grant_id"], **FLAGS}
    checkpoint = _save_checkpoint(connection, checkpoint_body)
    connection.execute("INSERT INTO review_history_audit_heads VALUES (?,?)", (root["audit_id"], checkpoint["checkpoint_id"]))
    _store_operation(connection, operation_id, "start", administrator, request, root["audit_id"], checkpoint["checkpoint_id"])
    governance._event(connection, "review_history_audit_started", {"audit_id": root["audit_id"], "checkpoint_id": checkpoint["checkpoint_id"], **FLAGS})
    result = _result(connection, root, checkpoint)
    _fresh_administrator(connection, credential, administrator)
    return result


def advance(connection: sqlite3.Connection, audit_id: str, *, credential: Any,
            expected_checkpoint_id: str, limit: int = 64, operation_id: str) -> dict[str, Any]:
    from . import governance, review_continuity as continuity

    administrator = governance.authorize(connection, credential, "administrator")
    _input_address(audit_id, "rha")
    _input_address(expected_checkpoint_id, "rhs")
    _require(type(limit) is int and 1 <= limit <= MAX_STEP_TRANSITIONS,
             "audit_invalid_input", "Expected 1..64 transitions per advance")
    request = {"audit_id": audit_id, "expected_checkpoint_id": expected_checkpoint_id,
               "limit": limit, "administrator_grant_id": administrator["grant_id"]}
    old_operation = _operation(connection, operation_id, "advance", administrator, request)
    if old_operation is not None:
        return _replay(connection, old_operation, administrator, request, credential=credential)
    root, original_scope = _load_root(connection, audit_id)
    predecessor = _head(connection, root)
    _require(predecessor["checkpoint_id"] == expected_checkpoint_id,
             "audit_checkpoint_conflict", "Expected checkpoint is not the current audit head")
    _require(predecessor["state"] == "pending", "audit_closed", "Audit already reached a terminal observation")
    cursor = dict(predecessor["to_cursor"])
    target = _root_cursor(root, "target")
    settings = continuity._settings(connection)
    transitions = []
    examined = 0
    stopped = None
    state = "pending"
    for _ in range(limit):
        if cursor["governance_generation"] == target["governance_generation"]:
            break
        generation = cursor["governance_generation"]
        edge = None
        examined += 1
        try:
            edge = continuity._transition(connection, generation, settings)
            old_g = continuity._policy_record(connection, "governance", generation)
            new_g = continuity._policy_record(connection, "governance", generation + 1)
            _require(old_g["policy_digest"] == cursor["governance_policy_digest"],
                     "audit_record_invalid", "Cursor governance policy differs")
            if edge is None:
                state = "governance_reset"
                stopped = {"governance_generation": generation, "code": "audit_governance_reset", "transition": None}
                break
            old_c = continuity._policy_record(connection, "content", edge["from_content_policy_generation"])
            new_c = continuity._policy_record(connection, "content", edge["to_content_policy_generation"])
            _require(_same(_edge_cursor(edge, "from"), cursor)
                     and old_c["policy_digest"] == edge["from_content_policy_digest"]
                     and new_c["policy_digest"] == edge["to_content_policy_digest"]
                     and old_g["policy_digest"] == edge["from_governance_policy_digest"]
                     and new_g["policy_digest"] == edge["to_governance_policy_digest"]
                     and _same(old_g["policy"], new_g["policy"])
                     and new_c["governance_policy_generation"] == generation + 1
                     and new_c["administrator_grant_id"] == edge["administrator_grant_id"]
                     and new_g["administrator_grant_id"] == edge["administrator_grant_id"],
                     "audit_record_invalid", "Continuity edge historical references differ")
            if (not _same(original_scope, continuity._scope(old_c["policy"], root["write_ids"]))
                    or not _same(original_scope, continuity._scope(new_c["policy"], root["write_ids"]))):
                state = "scope_changed"
                stopped = {"governance_generation": generation, "code": "audit_scope_changed",
                           "transition": {"transition_id": edge["transition_id"], "transition_digest": edge["transition_digest"]}}
                break
            next_cursor = _edge_cursor(edge, "to")
            _require(next_cursor["governance_generation"] != target["governance_generation"]
                     or _same(next_cursor, target),
                     "audit_record_invalid", "Last edge does not reach the exact frozen target")
            transitions.append(edge)
            cursor = next_cursor
        except (WorkbenchError, KeyError, TypeError, ValueError, AttributeError, OverflowError,
                RecursionError, UnicodeError, sqlite3.Error):
            state = "history_invalid"
            stopped = {"governance_generation": generation, "code": "audit_history_invalid",
                       "transition": ({"transition_id": edge["transition_id"], "transition_digest": edge["transition_digest"]}
                                      if isinstance(edge, dict) and _embedded_edge(edge) else None)}
            break
    if stopped is None and cursor["governance_generation"] == target["governance_generation"]:
        state = "history_complete"
    _fresh_administrator(connection, credential, administrator)
    body = {"schema_version": 1, "rules_version": RULES_VERSION,
            "audit_id": root["audit_id"], "audit_digest": root["audit_digest"],
            "index": predecessor["index"] + 1, "previous_checkpoint_id": predecessor["checkpoint_id"],
            "previous_checkpoint_digest": predecessor["checkpoint_digest"],
            "from_cursor": predecessor["to_cursor"], "to_cursor": cursor,
            "requested_limit": limit, "examined_transition_count": examined,
            "checked_transition_count": len(transitions),
            "total_checked_transition_count": predecessor["total_checked_transition_count"] + len(transitions),
            "transitions": transitions, "stopped_at": stopped, "state": state,
            "operation_id": operation_id, "created_at": time.time(),
            "administrator_grant_id": administrator["grant_id"], **FLAGS}
    checkpoint = _save_checkpoint(connection, body)
    changed = connection.execute("UPDATE review_history_audit_heads SET checkpoint_id=? WHERE audit_id=? AND checkpoint_id=?",
                                 (checkpoint["checkpoint_id"], audit_id, expected_checkpoint_id)).rowcount
    _require(changed == 1, "audit_checkpoint_conflict", "Audit head changed during advance")
    _store_operation(connection, operation_id, "advance", administrator, request, audit_id, checkpoint["checkpoint_id"])
    governance._event(connection, "review_history_audit_advanced", {"audit_id": audit_id, "checkpoint_id": checkpoint["checkpoint_id"],
                                                                  "state": state, **FLAGS})
    result = _result(connection, root, checkpoint)
    _fresh_administrator(connection, credential, administrator)
    return result


def show(connection: sqlite3.Connection, audit_id: str, checkpoint_id: str | None = None) -> dict[str, Any]:
    """Read one addressed checkpoint and the current head; never traverse all steps."""
    _input_address(audit_id, "rha")
    if checkpoint_id is not None:
        _input_address(checkpoint_id, "rhs")
    root, _ = _load_root(connection, audit_id)
    current = _head(connection, root)
    checkpoint = current if checkpoint_id is None else _load_checkpoint(connection, checkpoint_id, root)
    return {"audit": root, "checkpoint": checkpoint, "current_checkpoint_id": current["checkpoint_id"],
            "replayed": False, **FLAGS, "limits": list(LIMITS)}
