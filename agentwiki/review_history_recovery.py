"""Read-only recovery and discovery of local advisory history audits.

Recovery retains the original administrator grant and immutable replay rules.
Catalogue membership is bounded by committed audit-start event sequence numbers;
heads are observed separately in each caller-owned read transaction. Neither
operation grants admission authority. SQLite and its owner remain trusted: this
index does not detect deleted events or attest a complete external history.
"""

from __future__ import annotations

import re
import sqlite3
import time
from typing import Any

from .store import WorkbenchError, digest


RULES_VERSION = "review-history-recovery-1"
MAX_PAGE = 20
MAX_EVENT_BYTES = 4_000
START_EVENT = "review_history_audit_started"
FLAGS = {"advisory_only": True, "acceptance_authority": False, "authority_evaluated": False}
EVENT_FIELDS = {"audit_id", "checkpoint_id", *FLAGS}
OPERATION_FIELDS = (
    "operation_id", "action", "administrator_grant_id", "request_digest", "audit_id", "checkpoint_id",
)

SCHEMA = """
CREATE INDEX IF NOT EXISTS review_history_audit_start_events ON events(type,seq);
"""


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise WorkbenchError(code, detail)


def _operation_record(connection: sqlite3.Connection, operation_id: str) -> sqlite3.Row:
    """Read only fixed-size operation metadata before loading addressed inputs."""
    from . import review_history_audit as audit

    audit._operation_id(operation_id)
    try:
        size = connection.execute(
            "SELECT length(CAST(action AS BLOB)),typeof(action),"
            "length(CAST(administrator_grant_id AS BLOB)),typeof(administrator_grant_id),"
            "length(CAST(request_digest AS BLOB)),typeof(request_digest),"
            "length(CAST(audit_id AS BLOB)),typeof(audit_id),"
            "length(CAST(checkpoint_id AS BLOB)),typeof(checkpoint_id)"
            " FROM review_history_audit_operations WHERE operation_id=?", (operation_id,),
        ).fetchone()
        _require(size is not None, "audit_operation_not_found", "No recorded audit operation has this ID")
        _require(all(size[index + 1] == "text" and size[index] <= maximum
                     for index, maximum in ((0, 7), (2, 36), (4, 64), (6, 68), (8, 68))),
                 "audit_record_invalid", "Audit operation metadata exceeds its bounds")
        operation = connection.execute(
            "SELECT operation_id,action,administrator_grant_id,request_digest,audit_id,checkpoint_id"
            " FROM review_history_audit_operations WHERE operation_id=?", (operation_id,),
        ).fetchone()
        _require(operation is not None and operation["operation_id"] == operation_id
                 and operation["action"] in {"start", "advance"}
                 and bool(re.fullmatch(r"gvg_[0-9a-f]{32}", operation["administrator_grant_id"]))
                 and audit._hash(operation["request_digest"])
                 and audit._address(operation["audit_id"], "rha")
                 and audit._address(operation["checkpoint_id"], "rhs"),
                 "audit_record_invalid", "Audit operation metadata is malformed")
        return operation
    except sqlite3.Error as error:
        raise WorkbenchError("audit_record_invalid", "Audit operation metadata is unavailable") from error


def recover(connection: sqlite3.Connection, operation_id: str, *, credential: Any) -> dict[str, Any]:
    """Recover the exact recorded replay using only its original operation ID.

    This reconstructs an existing request; it never creates a new operation or
    retargets an audit. Current authority under the original administrator grant
    is required even when the result is historical.
    """
    from . import governance, review_history_audit as audit

    administrator = governance.authorize(connection, credential, "administrator")
    operation = _operation_record(connection, operation_id)
    _require(operation["administrator_grant_id"] == administrator["grant_id"],
             "audit_idempotency_conflict", "Operation belongs to a different administrator grant")
    try:
        root, _ = audit._load_root(connection, operation["audit_id"])
        checkpoint = audit._load_checkpoint(connection, operation["checkpoint_id"], root)
        _require(root["audit_id"] == operation["audit_id"]
                 and checkpoint["checkpoint_id"] == operation["checkpoint_id"]
                 and checkpoint["audit_id"] == root["audit_id"]
                 and checkpoint["operation_id"] == operation_id
                 and checkpoint["administrator_grant_id"] == operation["administrator_grant_id"],
                 "audit_record_invalid", "Operation differs from its addressed checkpoint")
        if operation["action"] == "start":
            _require(checkpoint["index"] == 0 and root["operation_id"] == operation_id
                     and root["administrator_grant_id"] == operation["administrator_grant_id"],
                     "audit_record_invalid", "Start operation differs from its addressed audit")
            request = {
                "decision_id": root["source_decision_id"],
                "expected_policy_generation": root["target_governance_generation"],
                "expected_target_packet_digest": root["target_packet"]["packet_digest"],
                "administrator_grant_id": administrator["grant_id"],
            }
        else:
            _require(checkpoint["index"] > 0, "audit_record_invalid", "Advance operation has no predecessor")
            request = {
                "audit_id": root["audit_id"],
                "expected_checkpoint_id": checkpoint["previous_checkpoint_id"],
                "limit": checkpoint["requested_limit"],
                "administrator_grant_id": administrator["grant_id"],
            }
        audit._bounded(request, audit.MAX_OPERATION_BYTES, "audit_record_invalid")
        _require(operation["request_digest"] == digest(request),
                 "audit_record_invalid", "Recorded request digest differs from its immutable inputs")
        checked = audit._operation(connection, operation_id, operation["action"], administrator, request)
        _require(checked is not None
                 and all(checked[key] == operation[key] for key in OPERATION_FIELDS),
                 "audit_record_invalid", "Operation map differs from its recorded links")
    except WorkbenchError as error:
        if error.code == "audit_record_invalid":
            raise
        raise WorkbenchError("audit_record_invalid", "Recovery inputs are malformed") from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("audit_record_invalid", "Recovery inputs are malformed") from error
    # _replay validates all immutable anchors and authorizes again after its last
    # observation. Return its envelope directly, without later materialization.
    return audit._replay(connection, checked, administrator, request, credential=credential)


def _entry(connection: sqlite3.Connection, event_seq: int, event: dict[str, Any]) -> dict[str, Any]:
    from . import review_history_audit as audit

    _require(set(event) == EVENT_FIELDS and audit._flags(event)
             and audit._address(event.get("audit_id"), "rha")
             and audit._address(event.get("checkpoint_id"), "rhs"),
             "audit_record_invalid", "Audit start event fields are malformed")
    root, _ = audit._load_root(connection, event["audit_id"])
    initial = audit._load_checkpoint(connection, event["checkpoint_id"], root)
    _require(initial["index"] == 0 and initial["audit_id"] == root["audit_id"]
             and initial["operation_id"] == root["operation_id"]
             and initial["administrator_grant_id"] == root["administrator_grant_id"],
             "audit_record_invalid", "Audit start event does not reference its initial checkpoint")
    head = audit._head(connection, root)
    return {
        "event_seq": event_seq,
        "audit_id": root["audit_id"],
        "proposal_id": root["proposal_id"],
        "source_decision_id": root["source_decision_id"],
        "target_packet": dict(root["target_packet"]),
        "source_governance_generation": root["source_governance_generation"],
        "target_governance_generation": root["target_governance_generation"],
        "start_operation_id": root["operation_id"],
        "initial_checkpoint_id": initial["checkpoint_id"],
        "observed_head": {
            "checkpoint_id": head["checkpoint_id"],
            "index": head["index"],
            "state": head["state"],
            "total_checked_transition_count": head["total_checked_transition_count"],
            "cursor": dict(head["to_cursor"]),
        },
        **FLAGS,
    }


def catalogue(connection: sqlite3.Connection, *, after_seq: int = 0,
              snapshot_max_seq: int | None = None, limit: int = 10) -> dict[str, Any]:
    """Read a bounded page of audit-start events with per-page head observations.

    Continue with the returned next_cursor unchanged. Its ceiling fixes eligible
    start-event membership, while current heads may change between pages. Only
    the first ``limit`` event bodies are loaded; one metadata row is lookahead.
    Bad records produce fixed error entries and still advance the page cursor.
    """
    from . import review_history_audit as audit

    _require(audit._integer(after_seq, zero=True) and type(limit) is int and 1 <= limit <= MAX_PAGE
             and (snapshot_max_seq is None or audit._integer(snapshot_max_seq, zero=True))
             and (after_seq == 0 or snapshot_max_seq is not None),
             "audit_invalid_input", "Expected a bounded page and a frozen continuation ceiling")
    try:
        current_maximum = connection.execute(
            "SELECT COALESCE(MAX(seq),0) FROM events INDEXED BY review_history_audit_start_events WHERE type=?",
            (START_EVENT,),
        ).fetchone()[0]
        _require(audit._integer(current_maximum, zero=True),
                 "audit_record_invalid", "Audit start-event ceiling is malformed")
        ceiling = current_maximum if snapshot_max_seq is None else snapshot_max_seq
        _require(after_seq <= ceiling <= current_maximum,
                 "audit_invalid_input", "Page cursor is outside the recorded start-event ceiling")
        metadata = connection.execute(
            "SELECT seq,typeof(data) AS data_type,length(CAST(data AS BLOB)) AS data_length"
            " FROM events INDEXED BY review_history_audit_start_events"
            " WHERE type=? AND seq>? AND seq<=? ORDER BY seq LIMIT ?",
            (START_EVENT, after_seq, ceiling, limit + 1),
        ).fetchall()
    except sqlite3.Error as error:
        raise WorkbenchError("audit_record_invalid", "Audit start-event index is unavailable") from error
    candidates = metadata[:limit]
    entries = []
    errors = []
    for candidate in candidates:
        event_seq = candidate["seq"]
        _require(audit._integer(event_seq) and after_seq < event_seq <= ceiling,
                 "audit_record_invalid", "Audit event sequence is malformed")
        known_audit_id = None
        try:
            _require(candidate["data_type"] == "text"
                     and audit._integer(candidate["data_length"], zero=True)
                     and candidate["data_length"] <= MAX_EVENT_BYTES,
                     "audit_record_invalid", "Audit start event exceeds its bound")
            row = connection.execute(
                "SELECT data FROM events WHERE seq=? AND type=? AND typeof(data)='text'"
                " AND length(CAST(data AS BLOB))<=?", (event_seq, START_EVENT, MAX_EVENT_BYTES),
            ).fetchone()
            _require(row is not None, "audit_record_invalid", "Audit start event is unavailable")
            event = audit._parse(row[0], MAX_EVENT_BYTES)
            if audit._address(event.get("audit_id"), "rha"):
                known_audit_id = event["audit_id"]
            entries.append(_entry(connection, event_seq, event))
        except (WorkbenchError, KeyError, TypeError, ValueError, AttributeError, OverflowError,
                RecursionError, UnicodeError, sqlite3.Error):
            failed = {"event_seq": event_seq, "code": "audit_record_invalid"}
            if known_audit_id is not None:
                failed["audit_id"] = known_audit_id
            errors.append(failed)
    has_more = len(metadata) > limit
    next_cursor = ({"after_seq": candidates[-1]["seq"], "snapshot_max_seq": ceiling} if has_more else None)
    return {
        "rules_version": RULES_VERSION,
        "after_seq": after_seq,
        "snapshot_max_seq": ceiling,
        "limit": limit,
        "scanned_count": len(candidates),
        "returned_count": len(entries),
        "entries": entries,
        "errors": errors,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "heads_observed_at": time.time(),
        **FLAGS,
    }
