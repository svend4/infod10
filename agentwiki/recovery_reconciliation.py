"""Observe a private snapshot against a trusted live Store without activation.

The private plan pins all database rows, including eventless lease heartbeats
and SQLite sequences. Public reports disclose only bounded primary-key samples
and changed column names. Neither an equal observation nor a plan grants future
authority, reserves a source, imports work or removes snapshot quarantine.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Iterator

from . import governance, store_backup, store_quarantine
from .store import HASH, WorkbenchError, canonical


RULES_VERSION = "recovery-reconciliation-1"
MAX_ROW_BYTES = 4 * 1024 * 1024
MAX_TOTAL_ROWS = 100_000
MAX_PLAN_BYTES = 1024 * 1024
MAX_DETAILS_PER_TABLE = 20
MAX_KEY_BYTES = 512
TIME_SEMANTICS = (
    "Rows are compared at separately pinned read snapshots; equal observations do not reserve future state "
    "or rule out unobserved intermediate states. Credential expiry uses current wall-clock samples."
)
FLAGS = {"advisory_only": True, "activation_allowed": False,
         "domain_writes_performed": False, "private_fingerprints_exported": False}
PLAN_FIELDS = {"schema_version", "rules_version", "plan_id", "created_at", "source_path",
               "source_fingerprint", "administrator_grant_id", "snapshot", "report"}
REPORT_FIELDS = {"schema_version", "rules_version", "plan_id", "created_at", "snapshot", "source",
                 "administrator_grant_id", "event_prefix", "append_only_preserved", "differences",
                 "source_rows_match_at_both_observations", "time_semantics", *FLAGS}
DIFFERENCE_FIELDS = {"snapshot_rows", "source_rows", "added_count", "removed_count", "changed_count",
                     "added_keys", "removed_keys", "changed", "details_truncated"}
APPEND_ONLY_TABLES = {
    "sources", "revisions", "reviews", "proposal_components", "governance_policies",
    "governance_grants", "governance_revocations", "governance_decisions", "governance_operations",
    "governance_receipts", "authorship_leases", "proposal_authorship", "content_policies",
    "content_packets", "review_continuity_transitions", "review_history_audits",
    "review_history_audit_checkpoints", "review_history_audit_operations",
    "governance_settings", "authorship_settings", "content_settings", "review_continuity_settings",
}
PRIVATE_COLUMNS = {"credential_hash", "request_digest"}
IDENTIFIER = re.compile(r"[a-z][a-z0-9_]*\Z")
BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
PLAN_ID = re.compile(r"rcp_[0-9a-f]{32}\Z")
GRANT_ID = re.compile(r"gvg_[0-9a-f]{32}\Z")


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "recovery_limit_exceeded", "Recovery observation exceeded its time bound")


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def _quoted(value: str) -> str:
    _require(bool(IDENTIFIER.fullmatch(value)), "recovery_source_invalid", "Unsupported trusted SQL identifier")
    return '"' + value + '"'


def _path(value: str | Path) -> Path:
    try:
        return store_backup._path(value)
    except WorkbenchError as error:
        raise WorkbenchError("recovery_invalid_input", "Expected an explicit supported filesystem path") from error


def _columns(connection: sqlite3.Connection, table: str) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    info = list(connection.execute("PRAGMA main.table_info(" + _quoted(table) + ")"))
    _require(0 < len(info) <= 32 and all(IDENTIFIER.fullmatch(row[1]) for row in info),
             "recovery_source_invalid", "Unsupported trusted table columns")
    columns = tuple(row[1] for row in info)
    if table == "sqlite_sequence":
        _require(columns == ("name", "seq"), "recovery_source_invalid", "Unsupported SQLite sequence columns")
        return columns, ("name",), ("TEXT",)
    keys = sorted((row[5], row[1], row[2]) for row in info if row[5])
    _require(keys and [row[0] for row in keys] == list(range(1, len(keys) + 1))
             and all(row[2] in {"INTEGER", "TEXT"} for row in keys),
             "recovery_source_invalid", "Trusted table needs supported primary keys")
    return columns, tuple(row[1] for row in keys), tuple(row[2] for row in keys)


def _safe_key(columns: tuple[str, ...], key_names: tuple[str, ...], key_types: tuple[str, ...],
              row: tuple[Any, ...], forbidden: tuple[str, ...] = ()) -> dict[str, Any]:
    result = {}
    for name, kind in zip(key_names, key_types):
        value = row[columns.index(name)]
        good = _integer(value) if kind == "INTEGER" else isinstance(value, str)
        if isinstance(value, str):
            good = (good and 0 < len(value.encode("utf-8")) <= MAX_KEY_BYTES
                    and value.isprintable() and not BEARER.search(value)
                    and not HASH.fullmatch(value) and not any(secret in value for secret in forbidden))
        _require(bool(good), "recovery_source_invalid", "A primary key cannot be safely represented")
        result[name] = value
    return result


def _typed(value: Any) -> list[Any]:
    if value is None:
        return ["null", None]
    if type(value) is int:
        return ["integer", str(value)]
    if type(value) is float:
        _require(math.isfinite(value), "recovery_source_invalid", "Nonfinite database numbers are unsupported")
        return ["real", value.hex()]
    if isinstance(value, str):
        return ["text", value]
    if isinstance(value, bytes):
        return ["blob", value.hex()]
    raise WorkbenchError("recovery_source_invalid", "Unsupported SQLite value type")


def _sizes(columns: tuple[str, ...]) -> str:
    fields = []
    for name in columns:
        quoted = _quoted(name)
        fields += ["typeof(" + quoted + ")", "length(CAST(" + quoted + " AS BLOB))"]
    return ",".join(fields)


def _check_sizes(header: sqlite3.Row, column_count: int) -> None:
    total = 0
    for index in range(column_count):
        kind, length = header[2 * index], header[2 * index + 1]
        _require(kind in {"null", "integer", "real", "text", "blob"}
                 and ((kind == "null" and length is None)
                      or (type(length) is int and 0 <= length <= MAX_ROW_BYTES)),
                 "recovery_limit_exceeded", "A database cell exceeds the supported byte bound")
        total += length or 0
    _require(total <= MAX_ROW_BYTES, "recovery_limit_exceeded", "A database row exceeds the supported byte bound")


def _guard_authorization(connection: sqlite3.Connection, deadline: float) -> None:
    # governance.authorize reads these tables internally. Check their size-only
    # headers first so its SELECT bodies have the same bounds as our streams.
    for table in ("governance_grants", "governance_revocations", "governance_operations"):
        columns, _, _ = _columns(connection, table)
        for header in connection.execute("SELECT " + _sizes(columns) + " FROM " + _quoted(table)
                                         + " LIMIT ?", (MAX_TOTAL_ROWS + 1,)):
            _deadline(deadline)
            _check_sizes(header, len(columns))


def _rows(connection: sqlite3.Connection, table: str, deadline: float, *,
          forbidden: tuple[str, ...] = ()) -> Iterator[tuple[tuple[Any, ...], dict[str, Any], tuple[Any, ...]]]:
    columns, key_names, key_types = _columns(connection, table)
    ordered = ",".join(_quoted(name) for name in key_names)
    names = ",".join(_quoted(name) for name in columns)
    suffix = " FROM " + _quoted(table) + " ORDER BY " + ordered + " LIMIT " + str(MAX_TOTAL_ROWS + 1)
    sizes = connection.execute("SELECT " + _sizes(columns) + suffix)
    bodies = connection.execute("SELECT " + names + suffix)
    previous = None
    sequence_names = {item["name"] for item in store_backup._expected_schema(False)[0]
                      if item["type"] == "table" and "AUTOINCREMENT" in (item["sql"] or "").upper()}
    count = 0
    for header in sizes:
        _deadline(deadline)
        count += 1
        _require(count <= MAX_TOTAL_ROWS, "recovery_limit_exceeded", "A table exceeds the row bound")
        _check_sizes(header, len(columns))
        # No row body is fetched into Python until its SQL type/length header
        # has been checked in the same pinned transaction.
        body = bodies.fetchone()
        _require(body is not None, "recovery_source_invalid", "Database row streams disagree")
        values = tuple(body)
        key = _safe_key(columns, key_names, key_types, values, forbidden)
        key_tuple = tuple(key[name] for name in key_names)
        _require(previous is None or previous < key_tuple,
                 "recovery_source_invalid", "Primary keys are duplicated or unordered")
        if table == "sqlite_sequence":
            _require(key_tuple[0] in sequence_names and type(values[1]) is int and values[1] >= 0,
                     "recovery_source_invalid", "SQLite sequence row is unsupported")
        previous = key_tuple
        yield key_tuple, key, values
    _require(bodies.fetchone() is None, "recovery_source_invalid", "Database row streams disagree")


def _row_bounds(connection: sqlite3.Connection, tables: tuple[str, ...], deadline: float) -> None:
    total = 0
    for table in (*tables, "sqlite_sequence"):
        _deadline(deadline)
        count = connection.execute("SELECT COUNT(*) FROM (SELECT 1 FROM " + _quoted(table)
                                   + " LIMIT ?)", (MAX_TOTAL_ROWS + 1,)).fetchone()[0]
        total += count
        _require(total <= MAX_TOTAL_ROWS, "recovery_limit_exceeded", "Database exceeds the total row bound")


def _fingerprint(connection: sqlite3.Connection, deadline: float, *, forbidden: tuple[str, ...]) -> str:
    schema = store_backup._schema(connection)
    _, tables = store_backup._expected_schema(False)
    _row_bounds(connection, tables, deadline)
    hasher = hashlib.sha256()

    def add(value: Any) -> None:
        encoded = canonical(value).encode("utf-8")
        hasher.update(str(len(encoded)).encode("ascii") + b":" + encoded)

    add({"rules_version": RULES_VERSION, "schema": schema})
    for table in (*tables, "sqlite_sequence"):
        columns, keys, _ = _columns(connection, table)
        add(["table", table, list(columns), list(keys)])
        for _, _, row in _rows(connection, table, deadline, forbidden=forbidden):
            add([_typed(value) for value in row])
        add(["end", table])
    _deadline(deadline)
    return hasher.hexdigest()


def _source_path(path: Path) -> None:
    store_backup._no_symlinks(path)
    with store_backup._regular_file(path, private=False, missing="recovery_source_invalid"):
        pass
    _require(not any(os.path.lexists(path.parent / name) for name in (
        store_quarantine.INTENT_NAME, store_quarantine.MANIFEST_NAME)),
        "recovery_source_invalid", "A live source cannot have reserved snapshot sidecars")


@contextmanager
def _source(path: Path, credential: Any, deadline: float) -> Iterator[tuple[sqlite3.Connection, dict[str, Any]]]:
    from . import source_fence

    _source_path(path)
    with store_backup._connection(path, deadline, readonly=True) as connection:
        connection.execute("BEGIN")
        _require(not store_quarantine.quarantine_present(connection),
                 "recovery_source_invalid", "A quarantined snapshot cannot supply live authority")
        _require(source_fence.fence_info(connection) is None,
                 "recovery_source_invalid", "A fenced source cannot supply live recovery authority")
        store_backup._supported_schema(connection, marked=False)
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
        page_count = connection.execute("PRAGMA page_count").fetchone()[0]
        _require(type(page_size) is int and type(page_count) is int
                 and 0 < page_size * page_count <= store_backup.MAX_DATABASE_BYTES,
                 "recovery_limit_exceeded", "Live database exceeds the byte bound")
        _, tables = store_backup._expected_schema(False)
        _row_bounds(connection, tables, deadline)
        _guard_authorization(connection, deadline)
        administrator = governance.authorize(connection, credential, "administrator", now=time.time())
        store_backup._integrity(connection, deadline)
        yield connection, administrator


def _observe(path: Path, credential: Any, deadline: float, grant_id: str,
             forbidden: tuple[str, ...]) -> tuple[str, dict[str, Any], float]:
    with _source(path, credential, deadline) as (connection, administrator):
        _require(administrator["grant_id"] == grant_id,
                 "recovery_lineage_mismatch", "The original current administrator grant is required")
        fingerprint = _fingerprint(connection, deadline, forbidden=forbidden)
        metadata = store_backup._metadata(connection)
        observed_at = time.time()
        current = governance.authorize(connection, credential, "administrator", now=observed_at)
        _require(current["grant_id"] == grant_id,
                 "recovery_lineage_mismatch", "The administrator grant changed")
        return fingerprint, metadata, observed_at


def _differences(snapshot: sqlite3.Connection, source: sqlite3.Connection, deadline: float,
                 forbidden: tuple[str, ...]) -> tuple[dict[str, Any], int]:
    _, tables = store_backup._expected_schema(False)
    differences = {}
    compared_events = 0
    for table in tables:
        columns, _, _ = _columns(source, table)
        left_rows = iter(_rows(snapshot, table, deadline, forbidden=forbidden))
        right_rows = iter(_rows(source, table, deadline, forbidden=forbidden))
        left, right = next(left_rows, None), next(right_rows, None)
        result = {"snapshot_rows": 0, "source_rows": 0, "added_count": 0, "removed_count": 0,
                  "changed_count": 0, "added_keys": [], "removed_keys": [], "changed": [],
                  "details_truncated": False}
        samples = 0
        while left is not None or right is not None:
            _deadline(deadline)
            if right is None or (left is not None and left[0] < right[0]):
                result["snapshot_rows"] += 1
                result["removed_count"] += 1
                if samples < MAX_DETAILS_PER_TABLE:
                    result["removed_keys"].append(left[1])
                    samples += 1
                left = next(left_rows, None)
            elif left is None or right[0] < left[0]:
                result["source_rows"] += 1
                result["added_count"] += 1
                if samples < MAX_DETAILS_PER_TABLE:
                    result["added_keys"].append(right[1])
                    samples += 1
                right = next(right_rows, None)
            else:
                result["snapshot_rows"] += 1
                result["source_rows"] += 1
                changed = sorted({"private_fields" if name in PRIVATE_COLUMNS else name
                                  for name, before, after in zip(columns, left[2], right[2])
                                  if _typed(before) != _typed(after)})
                if changed:
                    result["changed_count"] += 1
                    if samples < MAX_DETAILS_PER_TABLE:
                        result["changed"].append({"key": left[1], "fields": changed})
                        samples += 1
                elif table == "events":
                    compared_events += 1
                left, right = next(left_rows, None), next(right_rows, None)
        if table == "events":
            _require(not result["removed_count"] and not result["changed_count"],
                     "recovery_history_mismatch", "Snapshot events are not an exact live event prefix")
            high_water = snapshot.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            prefix_count = source.execute("SELECT COUNT(*) FROM events WHERE seq<=?", (high_water,)).fetchone()[0]
            _require(prefix_count == result["snapshot_rows"] == compared_events,
                     "recovery_history_mismatch", "Live events do not preserve the ordered snapshot prefix")
        if table in APPEND_ONLY_TABLES:
            _require(not result["removed_count"] and not result["changed_count"],
                     "recovery_lineage_mismatch", "An immutable snapshot row is absent or different in the live source")
        result["details_truncated"] = (
            result["added_count"] + result["removed_count"] + result["changed_count"] > samples)
        differences[table] = result
    return differences, compared_events


def _metadata_valid(value: Any) -> bool:
    _, tables = store_backup._expected_schema(False)
    return (isinstance(value, dict) and set(value) == {"revision", "event_high_water", "event_count", "table_counts"}
            and all(_integer(value.get(key)) for key in ("revision", "event_high_water", "event_count"))
            and isinstance(value.get("table_counts"), dict) and set(value["table_counts"]) == set(tables)
            and all(_integer(count) for count in value["table_counts"].values())
            and sum(value["table_counts"].values()) <= MAX_TOTAL_ROWS
            and value["event_count"] == value["table_counts"]["events"] <= value["event_high_water"])


def _public_key_valid(key: Any, names: tuple[str, ...], kinds: tuple[str, ...]) -> bool:
    if not isinstance(key, dict) or set(key) != set(names):
        return False
    try:
        _safe_key(names, names, kinds, tuple(key[name] for name in names))
        return True
    except (WorkbenchError, UnicodeError):
        return False


def _report_valid(report: Any) -> bool:
    if not (isinstance(report, dict) and set(report) == REPORT_FIELDS
            and type(report["schema_version"]) is int and report["schema_version"] == 1
            and report["rules_version"] == RULES_VERSION
            and isinstance(report["plan_id"], str) and PLAN_ID.fullmatch(report["plan_id"])
            and _timestamp(report["created_at"])
            and isinstance(report["administrator_grant_id"], str) and GRANT_ID.fullmatch(report["administrator_grant_id"])
            and all(report[key] is value for key, value in FLAGS.items())
            and report["time_semantics"] == TIME_SEMANTICS
            and report["append_only_preserved"] is True
            and report["source_rows_match_at_both_observations"] is True
            and _metadata_valid(report["source"])):
        return False
    snapshot = report["snapshot"]
    expected_summary = set(store_backup.PUBLIC_FIELDS) | {"inspection_only", "domain_writes_allowed"}
    if not (isinstance(snapshot, dict) and set(snapshot) == expected_summary
            and snapshot["rules_version"] == store_backup.RULES_VERSION
            and snapshot["kind"] in {"backup", "restore"}
            and isinstance(snapshot["backup_id"], str) and store_quarantine.BACKUP_ID.fullmatch(snapshot["backup_id"])
            and _timestamp(snapshot["created_at"]) and snapshot["integrity"] == snapshot["foreign_keys"] == "ok"
            and snapshot["inspection_only"] is True and snapshot["domain_writes_allowed"] is False
            and _metadata_valid({key: snapshot[key] for key in ("revision", "event_high_water", "event_count", "table_counts")})):
        return False
    event_prefix = report["event_prefix"]
    if not (isinstance(event_prefix, dict) and set(event_prefix) == {"matches", "compared_events"}
            and event_prefix["matches"] is True and type(event_prefix["compared_events"]) is int
            and event_prefix["compared_events"] == snapshot["event_count"]):
        return False
    differences = report["differences"]
    _, tables = store_backup._expected_schema(False)
    if not isinstance(differences, dict) or set(differences) != set(tables):
        return False
    # Derive validation metadata from trusted application SQL, never plan SQL.
    expected = sqlite3.connect(":memory:")
    expected.row_factory = sqlite3.Row
    try:
        for name, body in ((item["name"], item["sql"]) for item in store_backup._expected_schema(False)[0]
                           if item["type"] == "table"):
            if name != "sqlite_sequence":
                expected.execute(body)
        for table, value in differences.items():
            columns, names, kinds = _columns(expected, table)
            if not (isinstance(value, dict) and set(value) == DIFFERENCE_FIELDS
                    and all(_integer(value[key]) for key in ("snapshot_rows", "source_rows", "added_count", "removed_count", "changed_count"))
                    and value["snapshot_rows"] == snapshot["table_counts"][table]
                    and value["source_rows"] == report["source"]["table_counts"][table]
                    and value["source_rows"] == value["snapshot_rows"] + value["added_count"] - value["removed_count"]
                    and value["changed_count"] <= min(value["snapshot_rows"], value["source_rows"])
                    and value["removed_count"] <= value["snapshot_rows"]
                    and value["added_count"] <= value["source_rows"]
                    and all(isinstance(value[key], list) for key in ("added_keys", "removed_keys", "changed"))
                    and type(value["details_truncated"]) is bool):
                return False
            count = sum(len(value[key]) for key in ("added_keys", "removed_keys", "changed"))
            total = value["added_count"] + value["removed_count"] + value["changed_count"]
            if not (count == min(total, MAX_DETAILS_PER_TABLE) and value["details_truncated"] is (total > count)
                    and len(value["added_keys"]) <= value["added_count"]
                    and len(value["removed_keys"]) <= value["removed_count"]
                    and len(value["changed"]) <= value["changed_count"]
                    and all(_public_key_valid(key, names, kinds) for key in value["added_keys"] + value["removed_keys"])):
                return False
            allowed_fields = set(columns) - PRIVATE_COLUMNS | {"private_fields"}
            for entry in value["changed"]:
                if not (isinstance(entry, dict) and set(entry) == {"key", "fields"}
                        and _public_key_valid(entry["key"], names, kinds)
                        and isinstance(entry["fields"], list) and entry["fields"]
                        and all(isinstance(field, str) and field in allowed_fields for field in entry["fields"])
                        and entry["fields"] == sorted(set(entry["fields"]))):
                    return False
            if table in APPEND_ONLY_TABLES | {"events"} and (value["removed_count"] or value["changed_count"]):
                return False
        return True
    finally:
        expected.close()


def _plan_content(plan: Any) -> None:
    _require(isinstance(plan, dict) and set(plan) == PLAN_FIELDS
             and type(plan["schema_version"]) is int and plan["schema_version"] == 1
             and plan["rules_version"] == RULES_VERSION
             and isinstance(plan["plan_id"], str) and PLAN_ID.fullmatch(plan["plan_id"])
             and _timestamp(plan["created_at"])
             and isinstance(plan["source_path"], str) and str(_path(plan["source_path"])) == plan["source_path"]
             and isinstance(plan["source_fingerprint"], str) and HASH.fullmatch(plan["source_fingerprint"])
             and isinstance(plan["administrator_grant_id"], str) and GRANT_ID.fullmatch(plan["administrator_grant_id"]),
             "recovery_plan_invalid", "Private recovery plan header is malformed")
    snapshot = plan["snapshot"]
    _require(isinstance(snapshot, dict) and set(snapshot) == {"backup_id", "quarantine_id", "database_sha256"}
             and isinstance(snapshot["backup_id"], str) and store_quarantine.BACKUP_ID.fullmatch(snapshot["backup_id"])
             and isinstance(snapshot["quarantine_id"], str) and store_quarantine.QUARANTINE_ID.fullmatch(snapshot["quarantine_id"])
             and isinstance(snapshot["database_sha256"], str) and HASH.fullmatch(snapshot["database_sha256"])
             and _report_valid(plan["report"])
             and plan["report"]["plan_id"] == plan["plan_id"]
             and plan["report"]["created_at"] == plan["created_at"]
             and plan["report"]["administrator_grant_id"] == plan["administrator_grant_id"]
             and plan["report"]["snapshot"]["backup_id"] == snapshot["backup_id"],
             "recovery_plan_invalid", "Private recovery plan body is malformed")


def _plan_valid(plan: Any) -> None:
    try:
        _plan_content(plan)
    except WorkbenchError as error:
        if error.code == "recovery_plan_invalid":
            raise
        raise WorkbenchError("recovery_plan_invalid", "Private recovery plan is malformed") from error
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("recovery_plan_invalid", "Private recovery plan is malformed") from error


def _separate_plan(location: Path, database: Path) -> None:
    reserved = {database, *(Path(str(database) + suffix) for suffix in ("-wal", "-shm", "-journal"))}
    _require(location not in reserved, "recovery_invalid_input",
             "Private recovery plan cannot use a live database or its SQLite journal paths")


def _translate(error: WorkbenchError, *, plan: bool = False) -> WorkbenchError:
    if error.code.startswith(("recovery_", "governance_")):
        return error
    if error.code == "snapshot_limit_exceeded":
        return WorkbenchError("recovery_limit_exceeded", "Recovery observation exceeded a supported bound")
    if plan:
        return WorkbenchError("recovery_plan_invalid", "Private recovery plan cannot be validated")
    return WorkbenchError("recovery_source_invalid", "Snapshot or live source cannot be validated")


def assess_recovery(bundle_path: str | Path, database_path: str | Path, plan_path: str | Path, *,
                    credential: Any) -> dict[str, Any]:
    """Write a new private advisory plan after two equal live observations."""
    deadline = time.monotonic() + store_backup.DEADLINE_SECONDS
    try:
        store_backup._support()
        bundle, database, destination = _path(bundle_path), _path(database_path), _path(plan_path)
        _separate_plan(destination, database)
        _require(bundle not in destination.parents and destination.name not in {
            store_quarantine.INTENT_NAME, store_quarantine.MANIFEST_NAME, store_quarantine.DATABASE_NAME},
            "recovery_invalid_input", "Private plan must be outside the snapshot bundle and reserved filenames")
        store_backup._private_directory(destination.parent)
        _require(not os.path.lexists(destination), "recovery_plan_exists", "Private recovery plan must name a new file")
        store_backup._no_symlinks(destination, missing_final=True)
        manifest = store_backup._load_bundle(bundle, deadline)
        forbidden = (credential, governance._credential_hash(credential))
        with _source(database, credential, deadline) as (source, administrator):
            initial_fingerprint = _fingerprint(source, deadline, forbidden=forbidden)
            source_metadata = store_backup._metadata(source)
            with store_backup._connection(bundle / store_quarantine.DATABASE_NAME, deadline,
                                          readonly=True, immutable=True) as snapshot:
                snapshot.execute("BEGIN")
                _, tables = store_backup._expected_schema(False)
                _row_bounds(snapshot, tables, deadline)
                row = next((body for _, key, body in _rows(snapshot, "governance_grants", deadline, forbidden=forbidden)
                            if key["grant_id"] == administrator["grant_id"]), None)
                current = next((body for _, key, body in _rows(source, "governance_grants", deadline, forbidden=forbidden)
                                if key["grant_id"] == administrator["grant_id"]), None)
                _require(row is not None and current is not None
                         and [_typed(value) for value in row] == [_typed(value) for value in current],
                         "recovery_lineage_mismatch", "Snapshot and source do not share the exact administrator grant")
                differences, compared_events = _differences(snapshot, source, deadline, forbidden)
            governance.authorize(source, credential, "administrator", now=time.time())
        fresh_fingerprint, fresh_metadata, _ = _observe(database, credential, deadline,
                                                       administrator["grant_id"], forbidden)
        _require(fresh_fingerprint == initial_fingerprint and canonical(fresh_metadata) == canonical(source_metadata),
                 "recovery_source_changed", "Live database rows changed between assessment observations")
        created_at, plan_id = time.time(), "rcp_" + secrets.token_hex(16)
        report = {"schema_version": 1, "rules_version": RULES_VERSION,
                  "plan_id": plan_id, "created_at": created_at,
                  "snapshot": store_backup._summary(manifest), "source": source_metadata,
                  "administrator_grant_id": administrator["grant_id"],
                  "event_prefix": {"matches": True, "compared_events": compared_events},
                  "append_only_preserved": True, "differences": differences,
                  "source_rows_match_at_both_observations": True,
                  "time_semantics": TIME_SEMANTICS, **FLAGS}
        plan = {"schema_version": 1, "rules_version": RULES_VERSION, "plan_id": plan_id,
                "created_at": created_at, "source_path": str(database),
                "source_fingerprint": initial_fingerprint,
                "administrator_grant_id": administrator["grant_id"],
                "snapshot": {"backup_id": manifest["backup_id"],
                             "quarantine_id": manifest["quarantine"]["quarantine_id"],
                             "database_sha256": manifest["database"]["sha256"]}, "report": report}
        _plan_valid(plan)
        _require(len(canonical(plan).encode("utf-8")) <= MAX_PLAN_BYTES,
                 "recovery_limit_exceeded", "Private recovery plan exceeds its byte bound")
        _deadline(deadline)
        try:
            store_backup._write_json(destination, plan, MAX_PLAN_BYTES)
        except FileExistsError:
            raise WorkbenchError("recovery_plan_exists", "Private recovery plan destination already exists") from None
        store_backup._fsync_directory(destination.parent)
        return report
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("recovery_io_failed", "Recovery assessment could not complete") from error


def revalidate_recovery(plan_path: str | Path, database_path: str | Path, *, credential: Any) -> dict[str, Any]:
    """Observe a private plan against two fresh live snapshots; never activate."""
    deadline = time.monotonic() + store_backup.DEADLINE_SECONDS
    try:
        store_backup._support()
        location, database = _path(plan_path), _path(database_path)
        _separate_plan(location, database)
        store_backup._private_directory(location.parent)
        try:
            plan = store_backup._read_json(location, MAX_PLAN_BYTES)
            _plan_valid(plan)
        except WorkbenchError as error:
            raise _translate(error, plan=True) from error
        _require(str(database) == plan["source_path"],
                 "recovery_plan_stale", "Plan names another live database path")
        forbidden = (credential, governance._credential_hash(credential))
        first, first_metadata, _ = _observe(database, credential, deadline, plan["administrator_grant_id"], forbidden)
        _require(first == plan["source_fingerprint"],
                 "recovery_plan_stale", "Live database rows differ from the private plan")
        second, source_metadata, observed_at = _observe(database, credential, deadline,
                                                      plan["administrator_grant_id"], forbidden)
        _require(second == first == plan["source_fingerprint"]
                 and canonical(first_metadata) == canonical(source_metadata),
                 "recovery_plan_stale", "Live database rows changed during revalidation")
        _deadline(deadline)
        return {"rules_version": RULES_VERSION, "plan_id": plan["plan_id"], "observed_at": observed_at,
                "database_rows_unchanged_at_observation": True, "current_administrator_authorized": True,
                "elapsed_seconds": max(0.0, observed_at - plan["created_at"]),
                "source": source_metadata, **FLAGS}
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("recovery_io_failed", "Recovery revalidation could not complete") from error
