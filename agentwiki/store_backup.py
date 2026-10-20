"""Private, quarantined SQLite snapshots for inspection and history recovery.

The live source is opened directly in read-only mode, never through Store's
schema initializer. Every published copy already contains a persistent write
quarantine. Restoring a snapshot does not renew grants, leases, operation IDs or
production authority. The filesystem owner and SQLite remain trusted; private
checksums detect disagreement and are not signatures or external attestations.
"""

from __future__ import annotations

from contextlib import contextmanager
from functools import lru_cache
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Any, Iterator

from .store import HASH, WorkbenchError, canonical, digest
from .store_quarantine import (
    DATABASE_NAME, INTENT_NAME, MANIFEST_NAME, QUARANTINE_TABLE, RULES_VERSION,
)


MAX_DATABASE_BYTES = 256 * 1024 * 1024
MAX_MANIFEST_BYTES = 64 * 1024
MAX_INTENT_BYTES = 4_000
MAX_SCHEMA_OBJECTS = 128
MAX_SCHEMA_BYTES = 128_000
BACKUP_PAGES = 128
DEADLINE_SECONDS = 60.0
COPY_BLOCK_BYTES = 1024 * 1024
INTENT_FIELDS = {"schema_version", "rules_version", "kind", "backup_id", "created_at"}
MANIFEST_FIELDS = {
    "schema_version", "rules_version", "kind", "backup_id", "created_at", "database",
    "schema_fingerprint", "revision", "event_high_water", "event_count", "table_counts",
    "quarantine", "integrity", "foreign_keys",
}
PUBLIC_FIELDS = (
    "rules_version", "kind", "backup_id", "created_at", "revision", "event_high_water",
    "event_count", "table_counts", "integrity", "foreign_keys",
)


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise WorkbenchError(code, detail)


def _integer(value: Any, *, positive: bool = False) -> bool:
    return type(value) is int and (1 if positive else 0) <= value < 2**63


def _hash(value: Any) -> bool:
    return isinstance(value, str) and bool(HASH.fullmatch(value))


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "snapshot_limit_exceeded", "Snapshot operation exceeded its time bound")


def _path(value: Any) -> Path:
    _require(isinstance(value, (str, Path)) and bool(str(value)) and "\x00" not in str(value),
             "snapshot_invalid_input", "Expected an explicit filesystem path")
    location = Path(value).expanduser().absolute()
    _require(".." not in location.parts, "snapshot_invalid_input", "Parent traversal is not supported")
    return location


def _support() -> None:
    _require(os.name == "posix" and all(hasattr(os, name) for name in ("O_NOFOLLOW", "O_DIRECTORY", "O_CLOEXEC", "O_NONBLOCK")),
             "snapshot_private_path_required", "Private snapshots require POSIX filesystem support")


def _no_symlinks(location: Path, *, missing_final: bool = False) -> None:
    for component in (*reversed(location.parents), location):
        try:
            details = component.lstat()
        except FileNotFoundError:
            if missing_final and component == location:
                return
            raise WorkbenchError("snapshot_private_path_required", "A required filesystem path is unavailable") from None
        _require(not stat.S_ISLNK(details.st_mode), "snapshot_private_path_required", "Snapshot paths must not contain symlinks")
        if component != location:
            _require(stat.S_ISDIR(details.st_mode), "snapshot_private_path_required", "Snapshot parent must be a directory")


def _private_directory(location: Path) -> None:
    _no_symlinks(location)
    details = location.lstat()
    _require(stat.S_ISDIR(details.st_mode) and details.st_uid == os.geteuid()
             and stat.S_IMODE(details.st_mode) == 0o700,
             "snapshot_private_path_required", "Snapshot directory must be owner-only mode 0700")


@contextmanager
def _regular_file(location: Path, *, private: bool = True,
                  maximum: int = MAX_DATABASE_BYTES, missing: str = "snapshot_incomplete") -> Iterator[int]:
    _no_symlinks(location.parent)
    try:
        _require(not stat.S_ISLNK(location.lstat().st_mode),
                 "snapshot_private_path_required", "Snapshot files must not be symlinks")
        descriptor = os.open(location, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    except FileNotFoundError:
        raise WorkbenchError(missing, "A required snapshot file is missing") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and (not private or (stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1)),
                 "snapshot_private_path_required", "Snapshot file must be a private regular owner file")
        _require(0 <= details.st_size <= maximum, "snapshot_limit_exceeded", "Snapshot file exceeds its byte bound")
        yield descriptor
    finally:
        os.close(descriptor)


def _read_json(location: Path, maximum: int) -> dict[str, Any]:
    from . import obligations

    try:
        with _regular_file(location, maximum=maximum) as descriptor:
            raw = bytearray()
            while len(raw) <= maximum:
                block = os.read(descriptor, min(COPY_BLOCK_BYTES, maximum + 1 - len(raw)))
                if not block:
                    break
                raw.extend(block)
        _require(len(raw) <= maximum, "snapshot_manifest_invalid", "Snapshot JSON exceeds its byte bound")
        encoded = bytes(raw).decode("utf-8")
        body = obligations._strict_json(encoded)
        obligations._shape(body)
        _require(isinstance(body, dict) and canonical(body) == encoded,
                 "snapshot_manifest_invalid", "Expected canonical snapshot JSON")
        return body
    except WorkbenchError as error:
        if error.code.startswith("snapshot_"):
            raise
        raise WorkbenchError("snapshot_manifest_invalid", "Snapshot JSON is malformed") from error
    except (UnicodeError, TypeError, ValueError, RecursionError, OverflowError) as error:
        raise WorkbenchError("snapshot_manifest_invalid", "Snapshot JSON is malformed") from error


def _write_json(location: Path, body: dict[str, Any], maximum: int) -> None:
    encoded = canonical(body).encode("utf-8")
    _require(len(encoded) <= maximum, "snapshot_manifest_invalid", "Snapshot JSON exceeds its byte bound")
    descriptor = os.open(location, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(descriptor)


def _fsync_directory(location: Path) -> None:
    descriptor = os.open(location, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _reserve(location: Path, intent: dict[str, Any]) -> None:
    _private_directory(location.parent)
    _no_symlinks(location, missing_final=True)
    try:
        location.mkdir(mode=0o700)
    except FileExistsError:
        raise WorkbenchError("snapshot_destination_exists", "Snapshot destination must be a new directory") from None
    location.chmod(0o700)
    _write_json(location / INTENT_NAME, intent, MAX_INTENT_BYTES)
    _fsync_directory(location)
    _fsync_directory(location.parent)


def _new_database(location: Path) -> None:
    descriptor = os.open(location, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)


@contextmanager
def _connection(location: Path, deadline: float, *, readonly: bool,
                immutable: bool = False) -> Iterator[sqlite3.Connection]:
    _deadline(deadline)
    uri = location.as_uri() + ("?mode=ro" if readonly else "?mode=rw")
    if immutable:
        uri += "&immutable=1"
    connection = sqlite3.connect(uri, uri=True, timeout=1, isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("PRAGMA busy_timeout=1000")
        if readonly:
            connection.execute("PRAGMA query_only=ON")
        else:
            connection.execute("PRAGMA synchronous=FULL")
        connection.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        yield connection
    except sqlite3.Error as error:
        if time.monotonic() >= deadline:
            raise WorkbenchError("snapshot_limit_exceeded", "Snapshot operation exceeded its time bound") from error
        raise
    finally:
        connection.close()


def _schema(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    sizes = connection.execute(
        "SELECT length(CAST(type AS BLOB)),length(CAST(name AS BLOB)),length(CAST(tbl_name AS BLOB)),"
        "length(CAST(sql AS BLOB)),typeof(sql) FROM sqlite_master ORDER BY type,name LIMIT ?",
        (MAX_SCHEMA_OBJECTS + 1,),
    ).fetchall()
    _require(len(sizes) <= MAX_SCHEMA_OBJECTS
             and all(row[0] <= 16 and row[1] <= 256 and row[2] <= 256
                     and ((row[4] == "null" and row[3] is None)
                          or (row[4] == "text" and row[3] <= MAX_SCHEMA_BYTES)) for row in sizes)
             and sum(row[3] or 0 for row in sizes) <= MAX_SCHEMA_BYTES,
             "snapshot_schema_unsupported", "SQLite schema exceeds the supported bounds")
    rows = [dict(row) for row in connection.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]
    _require(all(row["type"] in {"table", "index"} for row in rows),
             "snapshot_schema_unsupported", "Snapshot schema contains unsupported objects")
    return rows


@lru_cache(maxsize=2)
def _expected_schema(marked: bool) -> tuple[list[dict[str, Any]], tuple[str, ...]]:
    from . import (authorship, content_gate, governance, review_continuity,
                   review_history_audit, review_history_recovery, store, store_quarantine)

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    try:
        for script in (store.SCHEMA, governance.SCHEMA, authorship.SCHEMA, content_gate.SCHEMA,
                       review_continuity.SCHEMA, review_history_audit.SCHEMA, review_history_recovery.SCHEMA):
            connection.executescript(script)
        if marked:
            connection.executescript(store_quarantine.SCHEMA)
        rows = _schema(connection)
        tables = tuple(row["name"] for row in rows if row["type"] == "table"
                       and not row["name"].startswith("sqlite_") and row["name"] != QUARANTINE_TABLE)
        return rows, tables
    finally:
        connection.close()


def _supported_schema(connection: sqlite3.Connection, *, marked: bool) -> str:
    expected, _ = _expected_schema(marked)
    actual = _schema(connection)
    _require(canonical(actual) == canonical(expected)
             and connection.execute("PRAGMA user_version").fetchone()[0] == 0
             and connection.execute("PRAGMA application_id").fetchone()[0] == 0,
             "snapshot_schema_unsupported", "SQLite schema is not the supported Store schema")
    return digest(actual)


def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    _, tables = _expected_schema(False)
    counts = {table: connection.execute('SELECT COUNT(*) FROM "' + table + '"').fetchone()[0] for table in tables}
    revision = connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
    _require(revision is not None and isinstance(revision[0], str)
             and bool(re.fullmatch(r"0|[1-9][0-9]{0,18}", revision[0])),
             "snapshot_source_invalid", "Snapshot revision is malformed")
    number = int(revision[0])
    high_water = connection.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
    _require(_integer(number) and _integer(high_water)
             and all(_integer(value) for value in counts.values()) and counts["events"] <= high_water,
             "snapshot_source_invalid", "Snapshot counters are malformed")
    return {"revision": number, "event_high_water": high_water, "event_count": counts["events"], "table_counts": counts}


def _integrity(connection: sqlite3.Connection, deadline: float) -> None:
    try:
        result = connection.execute("PRAGMA integrity_check").fetchmany(2)
        _require(len(result) == 1 and result[0][0] == "ok",
                 "snapshot_integrity_invalid", "SQLite integrity check failed")
        _require(connection.execute("PRAGMA foreign_key_check").fetchone() is None,
                 "snapshot_integrity_invalid", "SQLite foreign key check failed")
    except sqlite3.Error as error:
        _deadline(deadline)
        raise WorkbenchError("snapshot_integrity_invalid", "SQLite snapshot checks could not complete") from error
    _deadline(deadline)


def _file_digest(location: Path, deadline: float, *, sync: bool = False) -> tuple[int, str]:
    hasher = hashlib.sha256()
    size = 0
    with _regular_file(location) as descriptor:
        before = os.fstat(descriptor)
        if sync:
            os.fsync(descriptor)
        while True:
            _deadline(deadline)
            block = os.read(descriptor, COPY_BLOCK_BYTES)
            if not block:
                break
            size += len(block)
            _require(size <= MAX_DATABASE_BYTES, "snapshot_limit_exceeded", "Snapshot database exceeds its byte bound")
            hasher.update(block)
        after = os.fstat(descriptor)
        _require(before.st_size == after.st_size == size and before.st_mtime_ns == after.st_mtime_ns,
                 "snapshot_checksum_mismatch", "Snapshot file changed during inspection")
    return size, hasher.hexdigest()


def _intent(kind: str, backup_id: str, created_at: float) -> dict[str, Any]:
    return {"schema_version": 1, "rules_version": RULES_VERSION, "kind": kind,
            "backup_id": backup_id, "created_at": created_at}


def _manifest_valid(body: dict[str, Any]) -> None:
    from . import store_quarantine

    _, tables = _expected_schema(False)
    database = body.get("database")
    _require(set(body) == MANIFEST_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and body["kind"] in {"backup", "restore"}
             and isinstance(body["backup_id"], str) and bool(re.fullmatch(r"sbk_[0-9a-f]{32}", body["backup_id"]))
             and type(body["created_at"]) in {int, float} and math.isfinite(body["created_at"]) and body["created_at"] > 0
             and isinstance(database, dict) and set(database) == {"name", "bytes", "sha256"}
             and database["name"] == DATABASE_NAME and _integer(database["bytes"], positive=True)
             and database["bytes"] <= MAX_DATABASE_BYTES and _hash(database["sha256"])
             and _hash(body["schema_fingerprint"])
             and all(_integer(body[key]) for key in ("revision", "event_high_water", "event_count"))
             and isinstance(body["table_counts"], dict) and set(body["table_counts"]) == set(tables)
             and all(_integer(value) for value in body["table_counts"].values())
             and body["event_count"] == body["table_counts"]["events"] <= body["event_high_water"]
             and body["integrity"] == "ok" and body["foreign_keys"] == "ok",
             "snapshot_manifest_invalid", "Snapshot manifest fields are malformed")
    marker = store_quarantine.validate_body(body["quarantine"])
    _require(marker["backup_id"] == body["backup_id"] and marker["source_revision"] == body["revision"]
             and marker["event_high_water"] == body["event_high_water"],
             "snapshot_manifest_invalid", "Snapshot manifest differs from its quarantine provenance")


def _summary(manifest: dict[str, Any]) -> dict[str, Any]:
    result = {key: manifest[key] for key in PUBLIC_FIELDS}
    result["table_counts"] = dict(manifest["table_counts"])
    return {**result, "inspection_only": True, "domain_writes_allowed": False}


def _inspect_database(location: Path, manifest: dict[str, Any], deadline: float) -> None:
    from . import store_quarantine

    with _connection(location, deadline, readonly=True, immutable=True) as connection:
        _require(connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete",
                 "snapshot_integrity_invalid", "Snapshot database must be standalone without WAL dependencies")
        _require(_supported_schema(connection, marked=True) == manifest["schema_fingerprint"],
                 "snapshot_schema_unsupported", "Snapshot schema differs from its manifest")
        try:
            marker = store_quarantine.read_marker(connection)
        except WorkbenchError as error:
            raise WorkbenchError("snapshot_manifest_invalid", "Snapshot quarantine marker is malformed") from error
        _require(canonical(marker) == canonical(manifest["quarantine"]),
                 "snapshot_manifest_invalid", "Snapshot quarantine marker differs from its manifest")
        actual = _metadata(connection)
        _require(all(canonical(actual[key]) == canonical(manifest[key]) for key in actual),
                 "snapshot_manifest_invalid", "Snapshot counters differ from its manifest")
        _integrity(connection, deadline)
    _deadline(deadline)


def _layout(location: Path, *, complete: bool) -> None:
    names = []
    with os.scandir(location) as iterator:
        for entry in iterator:
            names.append(entry.name)
            _require(len(names) <= 3, "snapshot_manifest_invalid", "Snapshot directory contains unsupported files")
    expected = {INTENT_NAME, DATABASE_NAME} | ({MANIFEST_NAME} if complete else set())
    _require(expected <= set(names),
             "snapshot_incomplete", "Snapshot directory is incomplete")
    _require(set(names) == expected,
             "snapshot_manifest_invalid", "Snapshot directory contains unsupported files")


def _load_bundle(location: Path, deadline: float) -> dict[str, Any]:
    _private_directory(location)
    _layout(location, complete=True)
    intent = _read_json(location / INTENT_NAME, MAX_INTENT_BYTES)
    manifest = _read_json(location / MANIFEST_NAME, MAX_MANIFEST_BYTES)
    try:
        _manifest_valid(manifest)
        _require(set(intent) == INTENT_FIELDS and canonical(intent) == canonical({key: manifest[key] for key in INTENT_FIELDS}),
                 "snapshot_manifest_invalid", "Snapshot intent differs from its completion manifest")
    except WorkbenchError as error:
        if error.code == "snapshot_manifest_invalid":
            raise
        raise WorkbenchError("snapshot_manifest_invalid", "Snapshot manifest or quarantine fields are malformed") from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("snapshot_manifest_invalid", "Snapshot manifest fields are malformed") from error
    size, checksum = _file_digest(location / DATABASE_NAME, deadline)
    _require(size == manifest["database"]["bytes"] and checksum == manifest["database"]["sha256"],
             "snapshot_checksum_mismatch", "Snapshot database checksum differs from its private manifest")
    _inspect_database(location / DATABASE_NAME, manifest, deadline)
    return manifest


def create_backup(database_path: str | Path, destination: str | Path, *, credential: Any) -> dict[str, Any]:
    """Capture one committed WAL snapshot into a new private quarantined bundle.

    Failure leaves a private incomplete destination. It is never completed by a
    retry or used as an active Store; choose a new destination for another try.
    """
    from . import governance, source_fence, store_quarantine

    _support()
    source_path, destination_path = _path(database_path), _path(destination)
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        _no_symlinks(source_path)
        with _regular_file(source_path, private=False, missing="snapshot_source_invalid"):
            pass
        reserved_intent = os.path.lexists(source_path.parent / INTENT_NAME)
        reserved_manifest = os.path.lexists(source_path.parent / MANIFEST_NAME)
        _require(reserved_intent == reserved_manifest, "snapshot_incomplete", "Snapshot source has incomplete reserved sidecars")
        try:
            existing_marker = store_quarantine.probe(source_path)
        except WorkbenchError as error:
            code = "snapshot_incomplete" if error.code == "store_snapshot_incomplete" else "snapshot_source_invalid"
            raise WorkbenchError(code, "Snapshot source is incomplete or malformed") from error
        _require(existing_marker is None, "snapshot_source_invalid", "Creating another backup from a quarantined snapshot is unsupported")
        _require(source_fence.probe(source_path) is None,
                 "store_source_fenced", "A fenced source cannot authorize a new backup")
        with _connection(source_path, deadline, readonly=True) as source:
            source.execute("BEGIN")
            _require(not store_quarantine.quarantine_present(source),
                     "snapshot_source_invalid", "Creating another backup from a quarantined snapshot is unsupported")
            _require(source_fence.fence_info(source) is None,
                     "store_source_fenced", "A fenced source cannot authorize a new backup")
            administrator = governance.authorize(source, credential, "administrator")
            _supported_schema(source, marked=False)
            source_metadata = _metadata(source)
            page_size = source.execute("PRAGMA page_size").fetchone()[0]
            page_count = source.execute("PRAGMA page_count").fetchone()[0]
            _require(_integer(page_size, positive=True) and _integer(page_count, positive=True)
                     and page_size * page_count <= MAX_DATABASE_BYTES,
                     "snapshot_limit_exceeded", "Source snapshot exceeds its database byte bound")
            backup_id, created_at = "sbk_" + secrets.token_hex(16), time.time()
            _reserve(destination_path, _intent("backup", backup_id, created_at))
            copied_path = destination_path / DATABASE_NAME
            _new_database(copied_path)

            def progress(status: int, remaining: int, total: int) -> None:
                _deadline(deadline)
                _require(total * page_size <= MAX_DATABASE_BYTES,
                         "snapshot_limit_exceeded", "Copied snapshot exceeds its database byte bound")

            with _connection(copied_path, deadline, readonly=False) as copied:
                source.backup(copied, pages=BACKUP_PAGES, progress=progress, sleep=0.05)
                _supported_schema(copied, marked=False)
                _require(canonical(_metadata(copied)) == canonical(source_metadata),
                         "snapshot_source_invalid", "Copied snapshot differs from the pinned source")
                marker = {
                    "schema_version": 1, "rules_version": RULES_VERSION,
                    "backup_id": backup_id, "quarantine_id": "sq_" + secrets.token_hex(16),
                    "quarantined": True, "created_at": created_at,
                    "source_revision": source_metadata["revision"],
                    "event_high_water": source_metadata["event_high_water"],
                }
                store_quarantine.initialize_marker(copied, marker)
                copied.commit()
                _require(copied.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete",
                         "snapshot_integrity_invalid", "Copied snapshot could not be finalized as one database file")
                fingerprint = _supported_schema(copied, marked=True)
                metadata = _metadata(copied)
                _require(canonical(metadata) == canonical(source_metadata),
                         "snapshot_source_invalid", "Quarantine changed copied domain state")
                _integrity(copied, deadline)
            source.rollback()
        size, checksum = _file_digest(copied_path, deadline, sync=True)
        manifest = {
            "schema_version": 1, "rules_version": RULES_VERSION, "kind": "backup",
            "backup_id": backup_id, "created_at": created_at,
            "database": {"name": DATABASE_NAME, "bytes": size, "sha256": checksum},
            "schema_fingerprint": fingerprint, **metadata, "quarantine": marker,
            "integrity": "ok", "foreign_keys": "ok",
        }
        _manifest_valid(manifest)
        _inspect_database(copied_path, manifest, deadline)
        _layout(destination_path, complete=False)
        with _connection(source_path, deadline, readonly=True) as fresh:
            fresh.execute("BEGIN")
            _require(not store_quarantine.quarantine_present(fresh),
                     "snapshot_source_invalid", "Source became a quarantined snapshot during backup")
            _require(source_fence.fence_info(fresh) is None,
                     "store_source_fenced", "The source stopped writes during backup creation")
            current = governance.authorize(fresh, credential, "administrator")
            _require(current["grant_id"] == administrator["grant_id"],
                     "governance_unauthorized", "Administrator grant changed during snapshot creation")
        _deadline(deadline)
        _write_json(destination_path / MANIFEST_NAME, manifest, MAX_MANIFEST_BYTES)
        _fsync_directory(destination_path)
        return _summary(manifest)
    except WorkbenchError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("snapshot_io_failed", "Private snapshot creation could not complete") from error


def inspect_bundle(bundle_path: str | Path) -> dict[str, Any]:
    """Verify a completed private bundle and return only safe snapshot metadata."""
    _support()
    location = _path(bundle_path)
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        return _summary(_load_bundle(location, deadline))
    except WorkbenchError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("snapshot_io_failed", "Private snapshot inspection could not complete") from error


def restore_bundle(bundle_path: str | Path, destination: str | Path) -> dict[str, Any]:
    """Copy verified bytes into a new private inspection workspace, retaining quarantine."""
    _support()
    location, destination_path = _path(bundle_path), _path(destination)
    _require(destination_path != location and location not in destination_path.parents,
             "snapshot_invalid_input", "Restore destination must be outside the source bundle")
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        original = _load_bundle(location, deadline)
        created_at = time.time()
        _reserve(destination_path, _intent("restore", original["backup_id"], created_at))
        copied_path = destination_path / DATABASE_NAME
        descriptor = os.open(copied_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            copied_bytes, hasher = 0, hashlib.sha256()
            with _regular_file(location / DATABASE_NAME) as source, os.fdopen(descriptor, "wb", closefd=False) as output:
                while True:
                    _deadline(deadline)
                    block = os.read(source, COPY_BLOCK_BYTES)
                    if not block:
                        break
                    copied_bytes += len(block)
                    _require(copied_bytes <= MAX_DATABASE_BYTES,
                             "snapshot_limit_exceeded", "Restored database exceeds its byte bound")
                    hasher.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            _require(copied_bytes == original["database"]["bytes"]
                     and hasher.hexdigest() == original["database"]["sha256"],
                     "snapshot_checksum_mismatch", "Copied bytes differ from the private source manifest")
        finally:
            os.close(descriptor)
        copied_size, copied_checksum = _file_digest(copied_path, deadline)
        _require(copied_size == original["database"]["bytes"] and copied_checksum == original["database"]["sha256"],
                 "snapshot_checksum_mismatch", "Restored file differs from the verified copied bytes")
        manifest = {**original, "kind": "restore", "created_at": created_at}
        _manifest_valid(manifest)
        _inspect_database(copied_path, manifest, deadline)
        _layout(destination_path, complete=False)
        _deadline(deadline)
        _write_json(destination_path / MANIFEST_NAME, manifest, MAX_MANIFEST_BYTES)
        _fsync_directory(destination_path)
        return _summary(manifest)
    except WorkbenchError:
        raise
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("snapshot_io_failed", "Private snapshot restore could not complete") from error
