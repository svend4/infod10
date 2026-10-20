"""Seal one existing Store against further cooperative writes and publication.

The reserved metadata row is permanent through this API. Its private request
digest binds one recovery plan and operation; only its safe receipt is public.
The SQLite owner, application code and local clock remain trusted. This is not
an external fencing service, snapshot activation or transfer of authority.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any

from . import governance, store_backup, store_quarantine
from .store import HASH, WorkbenchError, canonical, digest


RULES_VERSION = "source-fence-1"
METAKEY = "__source_write_fence__"
MAX_JSON_BYTES = 65_536
ENVELOPE_FIELDS = {"schema_version", "rules_version", "request_digest", "receipt"}
RECEIPT_FIELDS = {
    "schema_version", "rules_version", "fence_id", "source_epoch", "operation_id", "plan_id",
    "administrator_grant_id", "created_at", "revision", "event_high_water_before",
    "event_count_before", "fence_event_seq", "source_writes_blocked", "publication_blocked",
    "irreversible", "snapshot_activation_allowed", "external_fencing_established",
    "trusted_database_owner_can_bypass",
}
FLAGS = {
    "source_writes_blocked": True, "publication_blocked": True, "irreversible": True,
    "snapshot_activation_allowed": False, "external_fencing_established": False,
    "trusted_database_owner_can_bypass": True,
}
OPERATION_ID = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
FENCE_ID = re.compile(r"sfn_[0-9a-f]{32}\Z")
EPOCH_ID = re.compile(r"sfe_[0-9a-f]{32}\Z")
PLAN_ID = re.compile(r"rcp_[0-9a-f]{32}\Z")
GRANT_ID = re.compile(r"gvg_[0-9a-f]{32}\Z")
BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _marker(condition: bool, message: str) -> None:
    _require(condition, "store_source_fence_invalid", message)


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and 0 < value < 2**63


def _operation(value: Any) -> bool:
    return (isinstance(value, str) and bool(OPERATION_ID.fullmatch(value))
            and not BEARER.search(value) and not HASH.fullmatch(value))


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "source_fence_limit_exceeded", "Source fence exceeded its time bound")


def _receipt(value: Any) -> dict[str, Any]:
    _marker(isinstance(value, dict) and set(value) == RECEIPT_FIELDS,
            "Source fence receipt has unsupported fields")
    _marker(type(value["schema_version"]) is int and value["schema_version"] == 1
            and value["rules_version"] == RULES_VERSION
            and all(value[key] is expected for key, expected in FLAGS.items()),
            "Source fence receipt has unsupported rules or flags")
    for key, pattern in (("fence_id", FENCE_ID), ("source_epoch", EPOCH_ID),
                         ("operation_id", OPERATION_ID), ("plan_id", PLAN_ID),
                         ("administrator_grant_id", GRANT_ID)):
        _marker(isinstance(value[key], str) and bool(pattern.fullmatch(value[key])),
                "Source fence receipt has an invalid identifier")
    _marker(_operation(value["operation_id"]), "Source fence operation identifier is not safe for publication")
    _marker(_timestamp(value["created_at"])
            and all(_integer(value[key]) for key in (
                "revision", "event_high_water_before", "event_count_before", "fence_event_seq"))
            and value["event_count_before"] <= value["event_high_water_before"] < value["fence_event_seq"],
            "Source fence receipt has invalid time or counters")
    return dict(value)


def _envelope(value: Any) -> dict[str, Any]:
    try:
        _marker(isinstance(value, dict) and set(value) == ENVELOPE_FIELDS,
                "Source fence metadata has unsupported fields")
        _marker(type(value["schema_version"]) is int and value["schema_version"] == 1
                and value["rules_version"] == RULES_VERSION
                and isinstance(value["request_digest"], str) and bool(HASH.fullmatch(value["request_digest"])),
                "Source fence metadata has unsupported rules or request binding")
        receipt = _receipt(value["receipt"])
        encoded = canonical(value)
        _marker(len(encoded.encode("utf-8")) <= MAX_JSON_BYTES, "Source fence metadata exceeds its size bound")
        return {"schema_version": 1, "rules_version": RULES_VERSION,
                "request_digest": value["request_digest"], "receipt": receipt}
    except WorkbenchError:
        raise
    except (KeyError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("store_source_fence_invalid", "Malformed source fence metadata") from error


def _meta_exists(connection: sqlite3.Connection) -> bool:
    row = connection.execute(
        "SELECT type FROM main.sqlite_schema WHERE name='meta' COLLATE NOCASE LIMIT 1"
    ).fetchone()
    if row is None:
        return False
    _marker(row[0] == "table", "Reserved Store metadata must be a table")
    return True


def _where() -> str:
    # Case variants and a binary spelling of the reserved key are signals too;
    # read_fence subsequently requires one exact canonical TEXT key and value.
    return "CAST(key AS TEXT)=? COLLATE NOCASE"


def fence_present(connection: sqlite3.Connection) -> bool:
    """Recognize the reserved key even if its saved value is malformed."""
    try:
        return _meta_exists(connection) and connection.execute(
            "SELECT 1 FROM main.meta WHERE " + _where() + " LIMIT 1", (METAKEY,)
        ).fetchone() is not None
    except WorkbenchError:
        raise
    except sqlite3.Error as error:
        raise WorkbenchError("store_source_fence_invalid", "Cannot inspect reserved source fence metadata") from error


def read_fence(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Return a private envelope after bounded canonical validation."""
    try:
        if not fence_present(connection):
            return None
        headers = connection.execute(
            "SELECT typeof(key),length(CAST(key AS BLOB)),typeof(value),length(CAST(value AS BLOB)) "
            "FROM main.meta WHERE " + _where() + " LIMIT 2", (METAKEY,)
        ).fetchall()
        _marker(len(headers) == 1 and headers[0][0] == "text"
                and headers[0][1] == len(METAKEY.encode("utf-8")) and headers[0][2] == "text"
                and type(headers[0][3]) is int and 0 < headers[0][3] <= MAX_JSON_BYTES,
                "Reserved source fence row is absent, duplicated or malformed")
        row = connection.execute("SELECT key,value FROM main.meta WHERE " + _where(), (METAKEY,)).fetchone()
        _marker(row is not None and row[0] == METAKEY, "Reserved source fence key is not canonical")
        body = _envelope(store_quarantine._strict_json(row[1]))
        _marker(canonical(body) == row[1], "Source fence metadata is not canonical JSON")
        receipt = body["receipt"]
        event = connection.execute(
            "SELECT type='source_write_fenced',typeof(data),length(CAST(data AS BLOB)) FROM main.events WHERE seq=?",
            (receipt["fence_event_seq"],),
        ).fetchone()
        _marker(event is not None and event[0] == 1 and event[1] == "text"
                and type(event[2]) is int and 0 < event[2] <= MAX_JSON_BYTES,
                "Source fence event is absent or malformed")
        event_body = connection.execute("SELECT data FROM main.events WHERE seq=?",
                                        (receipt["fence_event_seq"],)).fetchone()[0]
        _marker(event_body == canonical(receipt), "Source fence event differs from its receipt")
        return body
    except WorkbenchError:
        raise
    except (sqlite3.Error, KeyError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("store_source_fence_invalid", "Cannot validate reserved source fence metadata") from error


def fence_info(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Expose only the safe immutable receipt, never its private request digest."""
    body = read_fence(connection)
    return dict(body["receipt"]) if body is not None else None


def initialize_fence(connection: sqlite3.Connection, envelope: Any) -> dict[str, Any]:
    """Insert one reserved row within the caller's transaction; never replace."""
    body = _envelope(envelope)
    _require(connection.in_transaction, "source_fence_invalid_input", "Source fencing requires an existing transaction")
    _require(not fence_present(connection), "source_fence_conflict", "Source already contains a write fence")
    connection.execute("INSERT INTO main.meta(key,value) VALUES (?,?)", (METAKEY, canonical(body)))
    return dict(body["receipt"])


def probe(path: str | Path) -> dict[str, Any] | None:
    """Recognize a source fence before Store can open a writable connection."""
    location = Path(path)
    if not location.exists():
        _marker(not os.path.lexists(location), "Store database path is unavailable")
        return None
    _marker(location.is_file(), "Store database must be a regular file")
    connection = None
    try:
        connection = store_quarantine.readonly_connection(location)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("BEGIN")
        return fence_info(connection)
    except WorkbenchError:
        raise
    except (sqlite3.Error, OSError) as error:
        raise WorkbenchError("store_source_fence_invalid", "Cannot inspect the existing source fence") from error
    finally:
        if connection is not None:
            connection.close()


def _path(value: Any) -> Path:
    try:
        return store_backup._path(value)
    except WorkbenchError as error:
        raise WorkbenchError("source_fence_invalid_input", "Expected an explicit supported filesystem path") from error


def _source_path(path: Path) -> None:
    try:
        store_backup._no_symlinks(path)
        with store_backup._regular_file(path, private=False, missing="source_fence_source_invalid"):
            pass
        _require(not any(os.path.lexists(path.parent / name) for name in (
            store_quarantine.INTENT_NAME, store_quarantine.MANIFEST_NAME)),
            "source_fence_source_invalid", "A source fence requires a live source without snapshot sidecars")
    except WorkbenchError as error:
        if error.code.startswith("source_fence_"):
            raise
        raise WorkbenchError("source_fence_source_invalid", "Cannot inspect an existing supported source") from error


def _database(connection: sqlite3.Connection, deadline: float) -> dict[str, Any]:
    from . import recovery_reconciliation as recovery

    _require(not store_quarantine.quarantine_present(connection), "source_fence_source_invalid",
             "A quarantined snapshot cannot supply source authority")
    store_backup._supported_schema(connection, marked=False)
    page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    page_count = connection.execute("PRAGMA page_count").fetchone()[0]
    _require(type(page_size) is int and type(page_count) is int
             and 0 < page_size * page_count <= store_backup.MAX_DATABASE_BYTES,
             "source_fence_limit_exceeded", "Source exceeds its supported byte bound")
    _, tables = store_backup._expected_schema(False)
    recovery._row_bounds(connection, tables, deadline)
    # _metadata reads the revision cell before the full fingerprint stream.
    # Apply its row-size guard before that body can be materialized in Python.
    for header in connection.execute("SELECT " + recovery._sizes(("key", "value"))
                                     + " FROM main.meta LIMIT ?", (recovery.MAX_TOTAL_ROWS + 1,)):
        _deadline(deadline)
        recovery._check_sizes(header, 2)
    return store_backup._metadata(connection)


def _translate(error: WorkbenchError, *, plan: bool = False) -> WorkbenchError:
    if error.code.startswith(("source_fence_", "governance_", "recovery_plan")) or error.code == "store_source_fence_invalid":
        return error
    if error.code in {"snapshot_limit_exceeded", "recovery_limit_exceeded"}:
        return WorkbenchError("source_fence_limit_exceeded", "Source fence exceeded a supported bound")
    if plan:
        return WorkbenchError("recovery_plan_invalid", "Private recovery plan cannot be validated")
    if error.code in {"snapshot_invalid_input", "recovery_invalid_input"}:
        return WorkbenchError("source_fence_invalid_input", "Source fence paths are not supported")
    return WorkbenchError("source_fence_source_invalid", "Source database cannot be validated")


def _read_plan(location: Path) -> dict[str, Any]:
    from . import recovery_reconciliation as recovery

    try:
        store_backup._private_directory(location.parent)
        plan = store_backup._read_json(location, recovery.MAX_PLAN_BYTES)
        recovery._plan_valid(plan)
        return plan
    except WorkbenchError as error:
        raise _translate(error, plan=True) from error


def seal_source(database_path: str | Path, plan_path: str | Path, *,
                credential: Any, operation_id: Any) -> dict[str, Any]:
    """Atomically validate a fresh plan and permanently fence one local source."""
    from . import recovery_reconciliation as recovery

    deadline = time.monotonic() + store_backup.DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(_operation(operation_id),
                 "source_fence_invalid_input", "Expected a bounded operation identifier")
        database, location = _path(database_path), _path(plan_path)
        recovery._separate_plan(location, database)
        plan = _read_plan(location)
        _require(str(database) == plan["source_path"], "recovery_plan_stale", "Plan names another source database")
        request_digest = digest({"operation_id": operation_id, "plan": plan})
        forbidden = (credential, governance._credential_hash(credential))
        _require(not any(secret in operation_id for secret in forbidden),
                 "source_fence_invalid_input", "Operation identifier cannot contain private credential data")
        _source_path(database)
        with store_backup._connection(database, deadline, readonly=False) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                before = _database(connection, deadline)
                recovery._guard_authorization(connection, deadline)
                administrator = governance.authorize(connection, credential, "administrator", now=time.time())
                _require(administrator["grant_id"] == plan["administrator_grant_id"],
                         "source_fence_conflict", "The original plan administrator must authorize this operation")
                previous = read_fence(connection)
                if previous is not None:
                    receipt = previous["receipt"]
                    _require(previous["request_digest"] == request_digest
                             and receipt["operation_id"] == operation_id
                             and receipt["plan_id"] == plan["plan_id"]
                             and receipt["administrator_grant_id"] == administrator["grant_id"],
                             "source_fence_conflict", "Source fence is bound to another operation or private plan")
                    _deadline(deadline)
                    connection.commit()
                    return {**receipt, "replayed": True}
                fingerprint = recovery._fingerprint(connection, deadline, forbidden=forbidden)
                _require(fingerprint == plan["source_fingerprint"]
                         and canonical(before) == canonical(plan["report"]["source"]),
                         "recovery_plan_stale", "Source rows differ from the private recovery plan")
                store_backup._integrity(connection, deadline)
                administrator = governance.authorize(connection, credential, "administrator", now=time.time())
                _require(administrator["grant_id"] == plan["administrator_grant_id"],
                         "source_fence_conflict", "The original administrator grant changed")
                sequence = connection.execute("SELECT seq FROM main.sqlite_sequence WHERE name='events'").fetchone()
                _require(sequence is None or (type(sequence[0]) is int and 0 <= sequence[0] < 2**63),
                         "source_fence_source_invalid", "Source event sequence is malformed")
                next_sequence = max(before["event_high_water"], sequence[0] if sequence else 0) + 1
                _require(next_sequence < 2**63, "source_fence_limit_exceeded", "Source event sequence is exhausted")
                receipt = {
                    "schema_version": 1, "rules_version": RULES_VERSION,
                    "fence_id": "sfn_" + secrets.token_hex(16), "source_epoch": "sfe_" + secrets.token_hex(16),
                    "operation_id": operation_id, "plan_id": plan["plan_id"],
                    "administrator_grant_id": administrator["grant_id"], "created_at": time.time(),
                    "revision": before["revision"], "event_high_water_before": before["event_high_water"],
                    "event_count_before": before["event_count"], "fence_event_seq": next_sequence, **FLAGS,
                }
                initialize_fence(connection, {"schema_version": 1, "rules_version": RULES_VERSION,
                                              "request_digest": request_digest, "receipt": receipt})
                cursor = connection.execute("INSERT INTO main.events(type,data) VALUES (?,?)",
                                            ("source_write_fenced", canonical(receipt)))
                _require(cursor.lastrowid == next_sequence, "source_fence_source_invalid",
                         "Source fence event sequence differs from its receipt")
                _, tables = store_backup._expected_schema(False)
                recovery._row_bounds(connection, tables, deadline)
                _deadline(deadline)
                connection.commit()
                return {**receipt, "replayed": False}
            except BaseException:
                connection.rollback()
                raise
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("source_fence_io_failed", "Source fence operation could not complete") from error


def source_fence_status(database_path: str | Path) -> dict[str, Any]:
    """Describe the fence on an existing non-quarantined source without writing."""
    deadline = time.monotonic() + store_backup.DEADLINE_SECONDS
    try:
        store_backup._support()
        database = _path(database_path)
        _source_path(database)
        with store_backup._connection(database, deadline, readonly=True) as connection:
            connection.execute("BEGIN")
            _database(connection, deadline)
            receipt = fence_info(connection)
            fenced = receipt is not None
            _deadline(deadline)
            return {"rules_version": RULES_VERSION, "source_fenced": fenced,
                    "source_writes_blocked": fenced, "publication_blocked": fenced,
                    "irreversible": fenced, "receipt": receipt}
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("source_fence_io_failed", "Source fence status could not be observed") from error
