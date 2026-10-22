"""Bounded local assignment leases over one explicit immutable write catalog.

This separate control ledger never reads current jobs or advances write phases.
Only callers using this exact ledger share its allocation discipline. Workers
are self-declared, leases use local wall time, and old direct step APIs remain
available. An exact operation replay returns its historical receipt.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Any, Iterator

from . import obligations, store_backup
from . import workspace_restore_case_retention_review_write_catalog as catalogs
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_write_catalog_cli import _public_catalog, _public_write
from .workspace_restore_case_retention_review_write_leases_cli import _public_ledger, _public_operation, _public_page


RULES_VERSION = "workspace-restore-case-retention-review-write-leases-1"
MANIFEST_NAME = ".agentwiki-restore-case-retention-review-write-leases.json"
LOCK_NAME = ".agentwiki-restore-case-retention-review-write-leases.lock"
DATABASE_NAME = ".agentwiki-restore-case-retention-review-write-leases.sqlite3"
MAX_MANIFEST_BYTES = 3 * 1024 * 1024
MAX_DATABASE_BYTES = 8 * 1024 * 1024
MAX_PAGE_BYTES = 1024 * 1024
MAX_OPERATIONS = 1024
MAX_PAGE_SIZE = 16
MAX_LEASE_SECONDS = 3600
DEADLINE_SECONDS = catalogs.DEADLINE_SECONDS
APPLICATION_ID = 0x41574C53
LEDGER_ID = re.compile(r"wrrwlgr_[0-9a-f]{32}\Z")
LEASE_ID = re.compile(r"wrrwl_[0-9a-f]{32}\Z")
FLAGS = {**catalogs.writes.FLAGS, "coordination_only": True, "job_steps_enforced": False,
         "same_ledger_only": True, "worker_authenticated": False}
MANIFEST_FIELDS = {"schema_version", "rules_version", "ledger_id", "operation_id", "created_at", "ledger_path",
                   "parent", "ledger_directory", "database_pin", "source", "request_digest"}
SOURCE_FIELDS = {"catalog_path", "catalog_directory", "catalog_record", "catalog_lock", "catalog_request_digest",
                 "catalog", "members"}
CLAIM_FIELDS = {"kind", "index", "worker", "lease_seconds", "operation_id"}
RELEASE_FIELDS = {"kind", "index", "worker", "lease_id", "generation", "operation_id"}
LEASE_FIELDS = ("lease_id", "generation", "worker", "lease_seconds", "claimed_at", "expires_at", "released_at")
SQL_OPERATIONS = ("CREATE TABLE operations (seq INTEGER PRIMARY KEY, operation_id TEXT NOT NULL UNIQUE, "
                  "request TEXT NOT NULL, result TEXT NOT NULL, request_digest TEXT NOT NULL, result_digest TEXT NOT NULL)")
SCHEMA = [SQL_OPERATIONS]
cases = catalogs.cases
_ERRORS = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Write lease ledger exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _bounded(value: Any, maximum: int, deadline: float) -> None:
    _deadline(deadline)
    try:
        obligations._shape(value)
    except WorkbenchError as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_limit_exceeded",
                             "Write lease JSON structure exceeds its supported bound") from error
    _require(len(canonical(value).encode("utf-8")) <= maximum,
             "limit_exceeded", "Write lease JSON exceeds its byte bound")
    _deadline(deadline)


def _entries(ledger: Path) -> None:
    _require(cases._names(ledger, 3) == {MANIFEST_NAME, LOCK_NAME, DATABASE_NAME},
             "incomplete", "Write lease ledger has incomplete or unsupported controls")


@contextmanager
def _lock(ledger: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(ledger)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(ledger / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_incomplete",
                             "Write lease ledger has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Write lease lock needs a private empty regular owner file")
        original = cases._full(details)
        if create:
            os.fsync(descriptor)
        while True:
            _deadline(deadline)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.025)
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((ledger / LOCK_NAME).lstat()),
                 "record_invalid", "Write lease coordination control changed while locking")
        yield
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((ledger / LOCK_NAME).lstat()),
                 "record_invalid", "Write lease coordination control changed during its operation")
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _database_pin(ledger: Path) -> dict[str, int]:
    path = ledger / DATABASE_NAME
    store_backup._no_symlinks(path)
    details = path.lstat()
    _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
             and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1,
             "private_path_required", "Write lease database needs a private regular owner file")
    _require(0 <= details.st_size <= MAX_DATABASE_BYTES,
             "limit_exceeded", "Write lease database exceeds its byte bound")
    return cases._static(details)


def _database_pin_valid(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == cases.STATIC_FIELDS
            and all(type(value[key]) is int and 0 <= value[key] < 2**128 for key in cases.STATIC_FIELDS)
            and value["uid"] == os.geteuid() and value["mode"] == 0o600)


def _database_header(ledger: Path, deadline: float) -> None:
    """Reject WAL-format files before SQLite can create connection sidecars."""
    path = ledger / DATABASE_NAME
    with store_backup._regular_file(path, maximum=MAX_DATABASE_BYTES) as descriptor:
        before = cases._full(os.fstat(descriptor))
        _deadline(deadline)
        header = os.read(descriptor, 100)
        _require(len(header) == 100 and header[:16] == b"SQLite format 3\x00"
                 and header[18:20] == b"\x01\x01",
                 "record_invalid", "Write lease database needs an existing rollback-journal SQLite header")
        _require(before == cases._full(os.fstat(descriptor)) == cases._full(path.lstat()),
                 "record_invalid", "Write lease database header changed before opening")
    _deadline(deadline)


def _capture_source(ledger: Path, catalog: Path, deadline: float) -> dict[str, Any]:
    _require(catalog != ledger and catalog.parent == ledger.parent,
             "invalid_input", "Write lease ledger and its catalog need separate private sibling paths")
    with catalogs._lock(catalog, deadline):
        body = catalogs._read_catalog(catalog, deadline)
        _require(all(member["job_path"] != str(ledger) for member in body["members"]),
                 "invalid_input", "Write lease ledger must be separate from all captured member jobs")
        raw, record = cases._bytes(catalog / catalogs.CATALOG_NAME, catalogs.MAX_CATALOG_BYTES, deadline)
        _, lock = cases._bytes(catalog / catalogs.LOCK_NAME, 0, deadline)
        _require(raw == canonical(body).encode("utf-8"),
                 "source_changed", "Write catalog changed while assignment membership was captured")
        source = {"catalog_path": str(catalog), "catalog_directory": cases._directory(catalog),
                  "catalog_record": record, "catalog_lock": lock, "catalog_request_digest": body["request_digest"],
                  "catalog": catalogs._catalog(body),
                  "members": [{"index": member["index"], "write": member["write"]} for member in body["members"]]}
    _deadline(deadline)
    return source


def _ledger(body: dict[str, Any]) -> dict[str, Any]:
    return _public_ledger({"rules_version": RULES_VERSION,
                           **{key: body[key] for key in ("ledger_id", "operation_id", "created_at")},
                           "catalog": body["source"]["catalog"], "write_count": len(body["source"]["members"]),
                           "max_operations": MAX_OPERATIONS, **FLAGS})


def _read_manifest(ledger: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    _entries(ledger)
    body = store_backup._read_json(ledger / MANIFEST_NAME, MAX_MANIFEST_BYTES)
    _require(set(body) == MANIFEST_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["ledger_id"], str)
             and LEDGER_ID.fullmatch(body["ledger_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["ledger_path"] == str(ledger)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["ledger_directory"])
             and _database_pin_valid(body["database_pin"])
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Write lease ledger has unsupported immutable fields")
    source = body["source"]
    _require(isinstance(source, dict) and set(source) == SOURCE_FIELDS
             and cases._pin_valid(source["catalog_directory"])
             and cases._pin_valid(source["catalog_record"], full=True, file=True)
             and 1 < source["catalog_record"]["size"] <= catalogs.MAX_CATALOG_BYTES
             and cases._pin_valid(source["catalog_lock"], full=True, file=True)
             and source["catalog_lock"]["size"] == 0
             and source["catalog_lock"]["sha256"] == hashlib.sha256(b"").hexdigest()
             and isinstance(source["catalog_request_digest"], str)
             and cases.jobs.HASH.fullmatch(source["catalog_request_digest"]),
             "record_invalid", "Write lease ledger has unsupported private catalog anchors")
    catalog = cases._path(source["catalog_path"])
    _require(str(catalog) == source["catalog_path"] and catalog != ledger and catalog.parent == ledger.parent,
             "record_invalid", "Write lease ledger has unsupported source path bindings")
    receipt = _public_catalog(source["catalog"])
    _require(_same(receipt, source["catalog"]) and isinstance(source["members"], list)
             and len(source["members"]) == receipt["write_count"],
             "record_invalid", "Write lease membership does not match its captured catalog")
    write_ids, review_ids = set(), set()
    for index, member in enumerate(source["members"], 1):
        _deadline(deadline)
        _require(isinstance(member, dict) and set(member) == {"index", "write"}
                 and type(member["index"]) is int and member["index"] == index,
                 "record_invalid", "Write lease membership is not canonically ordered")
        write = _public_write(member["write"])
        _require(_same(write, member["write"]) and write["write_id"] not in write_ids
                 and write["review_id"] not in review_ids,
                 "record_invalid", "Write lease membership has repeated immutable identities")
        write_ids.add(write["write_id"])
        review_ids.add(write["review_id"])
    _require(_same(cases._directory(ledger.parent), body["parent"])
             and _same(cases._directory(ledger), body["ledger_directory"])
             and _same(_database_pin(ledger), body["database_pin"]),
             "record_invalid", "Write lease ledger differs from its fixed directory or database identities")
    _bounded(body, MAX_MANIFEST_BYTES, deadline)
    _ledger(body)
    return body


@contextmanager
def _connection(ledger: Path, body: dict[str, Any], deadline: float, *, write: bool) -> Iterator[sqlite3.Connection]:
    _entries(ledger)
    _require(_same(_database_pin(ledger), body["database_pin"]),
             "record_invalid", "Write lease database identity changed before opening")
    _database_header(ledger, deadline)
    connection = sqlite3.connect((ledger / DATABASE_NAME).as_uri() + ("?mode=rw" if write else "?mode=ro"),
                                 uri=True, timeout=0, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=0")
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        if write:
            connection.execute("PRAGMA synchronous=FULL")
            page_size = connection.execute("PRAGMA page_size").fetchone()[0]
            connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // page_size}")
        else:
            connection.execute("PRAGMA query_only=ON")
        _deadline(deadline)
        connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
        yield connection
        _deadline(deadline)
        connection.commit()
    except sqlite3.Error as error:
        connection.rollback()
        _deadline(deadline)
        if getattr(error, "sqlite_errorcode", None) == sqlite3.SQLITE_FULL:
            raise WorkbenchError("workspace_restore_case_retention_review_write_leases_limit_exceeded",
                                 "Write lease database reached its page bound") from error
        raise error
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    _entries(ledger)
    _require(_same(_database_pin(ledger), body["database_pin"]),
             "record_invalid", "Write lease database identity changed during its operation")
    if write:
        store_backup._fsync_directory(ledger)
    _deadline(deadline)


def _schema(connection: sqlite3.Connection) -> None:
    _require(connection.execute("PRAGMA application_id").fetchone()[0] == APPLICATION_ID
             and connection.execute("PRAGMA user_version").fetchone()[0] == 1
             and connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete",
             "record_invalid", "Write lease database has an unsupported identity or journal mode")
    rows = [tuple(row) for row in connection.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name LIMIT 4")]
    _require(rows == [("index", "sqlite_autoindex_operations_1", "operations", None),
                      ("table", "operations", "operations", SQL_OPERATIONS)],
             "record_invalid", "Write lease database has an unsupported exact schema")


def _request(value: Any, count: int) -> dict[str, Any]:
    _require(isinstance(value, dict) and isinstance(value.get("kind"), str)
             and value["kind"] in {"claim", "release"}
             and set(value) == (CLAIM_FIELDS if value["kind"] == "claim" else RELEASE_FIELDS)
             and type(value["index"]) is int and 1 <= value["index"] <= count
             and cases.jobs._operation(value["worker"]) and cases.jobs._operation(value["operation_id"]),
             "invalid_input", "Write lease request has unsupported parameters")
    if value["kind"] == "claim":
        _require(type(value["lease_seconds"]) is int and 1 <= value["lease_seconds"] <= MAX_LEASE_SECONDS,
                 "invalid_input", "Write lease duration is outside its supported bound")
    else:
        _require(isinstance(value["lease_id"], str) and LEASE_ID.fullmatch(value["lease_id"])
                 and type(value["generation"]) is int and 1 <= value["generation"] <= MAX_OPERATIONS,
                 "invalid_input", "Write lease release has unsupported lease identity")
    return value


def _current(operation: dict[str, Any]) -> dict[str, Any]:
    return {key: operation[key] for key in LEASE_FIELDS}


def _history(connection: sqlite3.Connection, body: dict[str, Any], deadline: float
             ) -> tuple[dict[int, dict[str, Any]], dict[str, tuple[dict[str, Any], dict[str, Any]]], float]:
    """Validate every bounded operation and reconstruct heads without mirrors."""
    _schema(connection)
    sizes = connection.execute("SELECT seq,length(CAST(request AS BLOB)),length(CAST(result AS BLOB)) "
                               "FROM operations ORDER BY seq LIMIT ?", (MAX_OPERATIONS + 1,)).fetchall()
    _require(len(sizes) <= MAX_OPERATIONS
             and all(row[0] == offset and type(row[1]) is int and 1 < row[1] <= 4096
                     and type(row[2]) is int and 1 < row[2] <= 16384 for offset, row in enumerate(sizes, 1)),
             "record_invalid", "Write lease operations exceed their supported sequence or size bounds")
    heads: dict[int, dict[str, Any]] = {}
    operations: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    lease_ids: set[str] = set()
    last_at = body["created_at"]
    for sequence, row in enumerate(connection.execute("SELECT * FROM operations ORDER BY seq"), 1):
        _deadline(deadline)
        request = obligations._strict_json(row["request"])
        operation = obligations._strict_json(row["result"])
        _bounded(request, 4096, deadline)
        _bounded(operation, 16384, deadline)
        request = _request(request, len(body["source"]["members"]))
        operation = _public_operation(operation)
        _require(row["seq"] == sequence and canonical(request) == row["request"]
                 and canonical(operation) == row["result"] and digest(request) == row["request_digest"]
                 and digest(operation) == row["result_digest"]
                 and row["operation_id"] == request["operation_id"] == operation["operation_id"]
                 and request["operation_id"] != body["operation_id"] and request["operation_id"] not in operations
                 and operation["rules_version"] == RULES_VERSION and operation["ledger_id"] == body["ledger_id"]
                 and operation["kind"] == request["kind"] and operation["index"] == request["index"]
                 and operation["worker"] == request["worker"] and operation["decided_at"] >= last_at
                 and _same(operation["write"], body["source"]["members"][request["index"] - 1]["write"]),
                 "record_invalid", "Write lease history has an invalid canonical request or receipt binding")
        previous = heads.get(request["index"])
        if request["kind"] == "claim":
            _require((previous is None or previous["released_at"] is not None
                      or operation["decided_at"] >= previous["expires_at"])
                     and operation["generation"] == (1 if previous is None else previous["generation"] + 1)
                     and operation["lease_seconds"] == request["lease_seconds"]
                     and operation["claimed_at"] == operation["decided_at"]
                     and operation["released_at"] is None and operation["lease_id"] not in lease_ids,
                     "record_invalid", "Write lease claim history violates its allocation order")
            lease_ids.add(operation["lease_id"])
        else:
            _require(previous is not None and previous["released_at"] is None
                     and all(previous[key] == request[key] for key in ("lease_id", "generation", "worker"))
                     and all(operation[key] == previous[key] for key in LEASE_FIELDS if key != "released_at")
                     and operation["released_at"] == operation["decided_at"],
                     "record_invalid", "Write lease release history does not match its latest lease")
        heads[request["index"]] = _current(operation)
        operations[request["operation_id"]] = request, operation
        last_at = operation["decided_at"]
    _deadline(deadline)
    return heads, operations, last_at


def _initialize_database(ledger: Path, deadline: float) -> dict[str, int]:
    path = ledger / DATABASE_NAME
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    pin = _database_pin(ledger)
    connection = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=0, isolation_level=None)
    try:
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA journal_mode=DELETE")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("PRAGMA page_size=4096")
        connection.execute(f"PRAGMA max_page_count={MAX_DATABASE_BYTES // 4096}")
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        _deadline(deadline)
        connection.execute("BEGIN IMMEDIATE")
        for statement in SCHEMA:
            connection.execute(statement)
        connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
        connection.execute("PRAGMA user_version=1")
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
    finally:
        connection.close()
    _require(_same(_database_pin(ledger), pin), "record_invalid", "New write lease database identity changed")
    _deadline(deadline)
    return pin


def _create_replay(ledger: Path, catalog: Path, operation_id: str, deadline: float) -> dict[str, Any]:
    with _lock(ledger, deadline):
        body = _read_manifest(ledger, deadline)
        _require(body["operation_id"] == operation_id and body["source"]["catalog_path"] == str(catalog),
                 "conflict", "Write lease ledger is already bound to another exact create request")
        with _connection(ledger, body, deadline, write=False) as connection:
            _history(connection, body, deadline)
            result = _ledger(body)
    return result


def create_write_lease_ledger(ledger_directory: str | Path, catalog_directory: str | Path,
                             *, operation_id: Any) -> dict[str, Any]:
    """Capture explicit membership without reading job states or taking leases."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        ledger, catalog = cases._path(ledger_directory), cases._path(catalog_directory)
        _require(cases.jobs._operation(operation_id) and ledger != catalog and ledger.parent == catalog.parent,
                 "invalid_input", "Write lease creation needs an operation ID and separate sibling paths")
        cases._directory(ledger.parent)
        if os.path.lexists(ledger):
            return _create_replay(ledger, catalog, operation_id, deadline)
        preliminary = _capture_source(ledger, catalog, deadline)
        try:
            ledger.mkdir(mode=0o700)
        except FileExistsError:
            return _create_replay(ledger, catalog, operation_id, deadline)
        ledger.chmod(0o700)
        with _lock(ledger, deadline, create=True):
            source = _capture_source(ledger, catalog, deadline)
            _require(_same(source, preliminary), "source_changed", "Write catalog changed during ledger reservation")
            created_at = time.time()
            _require(cases._timestamp(created_at), "io_failed", "Write lease creation clock is unsupported")
            database_pin = _initialize_database(ledger, deadline)
            body = {"schema_version": 1, "rules_version": RULES_VERSION, "ledger_id": "wrrwlgr_" + secrets.token_hex(16),
                    "operation_id": operation_id, "created_at": created_at, "ledger_path": str(ledger),
                    "parent": cases._directory(ledger.parent), "ledger_directory": cases._directory(ledger),
                    "database_pin": database_pin, "source": source}
            body["request_digest"] = digest(body)
            _bounded(body, MAX_MANIFEST_BYTES, deadline)
            result = _ledger(body)
            store_backup._write_json(ledger / MANIFEST_NAME, body, MAX_MANIFEST_BYTES)
            _entries(ledger)
            store_backup._fsync_directory(ledger)
            store_backup._fsync_directory(ledger.parent)
            _deadline(deadline)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_io_failed",
                             "Write lease ledger could not be created") from error


def inspect_write_lease_ledger(ledger_directory: str | Path) -> dict[str, Any]:
    """Read the fixed receipt and validate own history without source access."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        ledger = cases._path(ledger_directory)
        with _lock(ledger, deadline):
            body = _read_manifest(ledger, deadline)
            with _connection(ledger, body, deadline, write=False) as connection:
                _history(connection, body, deadline)
                result = _ledger(body)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_io_failed",
                             "Write lease ledger could not be inspected") from error


def _state(lease: dict[str, Any] | None, checked_at: float) -> str:
    if lease is None:
        return "unclaimed"
    if lease["released_at"] is not None:
        return "released"
    return "active" if checked_at < lease["expires_at"] else "expired"


def read_write_lease_page(ledger_directory: str | Path, *, limit: Any = MAX_PAGE_SIZE,
                          cursor: Any = None) -> dict[str, Any]:
    """Read bounded local assignment heads; no job or source state is opened."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE,
                 "invalid_input", "Write lease page size is outside its supported bound")
        ledger = cases._path(ledger_directory)
        with _lock(ledger, deadline):
            body = _read_manifest(ledger, deadline)
            count = len(body["source"]["members"])
            after = 0
            if cursor is not None:
                _require(isinstance(cursor, dict) and set(cursor) == {"ledger_id", "after_index"}
                         and cursor["ledger_id"] == body["ledger_id"] and type(cursor["after_index"]) is int
                         and 0 <= cursor["after_index"] <= count,
                         "invalid_input", "Write lease cursor is unsupported or belongs to another ledger")
                after = cursor["after_index"]
            with _connection(ledger, body, deadline, write=False) as connection:
                heads, operations, _ = _history(connection, body, deadline)
                checked_at = time.time()
                _require(cases._timestamp(checked_at), "io_failed", "Write lease observation clock is unsupported")
                entries = [{**member, "current_lease": heads.get(member["index"]),
                            "state": _state(heads.get(member["index"]), checked_at)}
                           for member in body["source"]["members"][after:after + limit]]
                end = after + len(entries)
                page = {"rules_version": RULES_VERSION, "ledger": _ledger(body), "checked_at": checked_at,
                        "after_index": after, "limit": limit, "entries": entries,
                        "next_cursor": ({"ledger_id": body["ledger_id"], "after_index": end} if end < count else None),
                        "operation_count": len(operations), "job_states_checked": False, **FLAGS}
                _bounded(page, MAX_PAGE_BYTES, deadline)
                result = _public_page(page)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_io_failed",
                             "Write lease page could not be read") from error


def _mutate(ledger_directory: str | Path, requested: dict[str, Any]) -> dict[str, Any]:
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        ledger = cases._path(ledger_directory)
        with _lock(ledger, deadline):
            body = _read_manifest(ledger, deadline)
            request = _request(requested, len(body["source"]["members"]))
            _require(request["operation_id"] != body["operation_id"],
                     "conflict", "Write lease creation operation ID cannot name a mutation")
            with _connection(ledger, body, deadline, write=True) as connection:
                heads, operations, last_at = _history(connection, body, deadline)
                old = operations.get(request["operation_id"])
                if old is not None:
                    _require(_same(old[0], request), "conflict", "Write lease operation ID names another exact request")
                    result = old[1]
                else:
                    _require(len(operations) < MAX_OPERATIONS,
                             "limit_exceeded", "Write lease ledger has reached its immutable operation bound")
                    decided_at = time.time()
                    _require(cases._timestamp(decided_at), "io_failed", "Write lease mutation clock is unsupported")
                    _require(decided_at >= last_at, "clock_regressed", "Write lease mutation clock precedes its saved history")
                    previous = heads.get(request["index"])
                    if request["kind"] == "claim":
                        _require(_state(previous, decided_at) != "active", "busy", "Write lease slot already has an active assignment")
                        used_ids = {old_operation[1]["lease_id"] for old_operation in operations.values()}
                        lease_id = "wrrwl_" + secrets.token_hex(16)
                        _require(lease_id not in used_ids, "io_failed", "Write lease ID unexpectedly repeats saved history")
                        lease = {"lease_id": lease_id,
                                 "generation": 1 if previous is None else previous["generation"] + 1,
                                 "worker": request["worker"], "lease_seconds": request["lease_seconds"],
                                 "claimed_at": decided_at, "expires_at": decided_at + request["lease_seconds"],
                                 "released_at": None}
                    else:
                        _require(previous is not None and previous["released_at"] is None
                                 and all(previous[key] == request[key] for key in ("lease_id", "generation", "worker")),
                                 "conflict", "Write lease release does not match the latest unreleased assignment")
                        lease = {**previous, "released_at": decided_at}
                    result = _public_operation({"rules_version": RULES_VERSION, "ledger_id": body["ledger_id"],
                                                "operation_id": request["operation_id"], "kind": request["kind"],
                                                "decided_at": decided_at, "index": request["index"],
                                                "write": body["source"]["members"][request["index"] - 1]["write"],
                                                **lease, **FLAGS})
                    _bounded(request, 4096, deadline)
                    _bounded(result, 16384, deadline)
                    connection.execute("INSERT INTO operations VALUES (?,?,?,?,?,?)",
                                       (len(operations) + 1, request["operation_id"], canonical(request), canonical(result),
                                        digest(request), digest(result)))
                    _deadline(deadline)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_io_failed",
                             "Write lease assignment could not be changed") from error


def claim_write_lease(ledger_directory: str | Path, *, index: Any, worker: Any,
                       lease_seconds: Any, operation_id: Any) -> dict[str, Any]:
    """Allocate one explicit slot among cooperating callers of this ledger."""
    return _mutate(ledger_directory, {"kind": "claim", "index": index, "worker": worker,
                                      "lease_seconds": lease_seconds, "operation_id": operation_id})


def release_write_lease(ledger_directory: str | Path, *, index: Any, worker: Any,
                         lease_id: Any, generation: Any, operation_id: Any) -> dict[str, Any]:
    """Release only the exact latest unreleased lease, including an expired one."""
    return _mutate(ledger_directory, {"kind": "release", "index": index, "worker": worker, "lease_id": lease_id,
                                      "generation": generation, "operation_id": operation_id})


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_review_write_leases_"):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict"):
        if error.code.endswith(suffix):
            return WorkbenchError("workspace_restore_case_retention_review_write_leases_" + suffix,
                                  "Write lease ledger exceeded a bound or has unsupported private bindings")
    return WorkbenchError("workspace_restore_case_retention_review_write_leases_record_invalid",
                          "Write lease ledger cannot be validated")
