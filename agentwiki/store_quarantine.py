"""Recognize inspection-only SQLite snapshots before Store can initialize them.

The marker is installed only by snapshot creation/restoration. A database owner
can bypass this cooperative boundary using raw SQL; Store has no activation API.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
from typing import Any

from .store import WorkbenchError, canonical


RULES_VERSION = "store-snapshot-1"
QUARANTINE_TABLE = "store_snapshot_quarantine"
INTENT_NAME = ".agentwiki-snapshot-intent.json"
MANIFEST_NAME = ".agentwiki-snapshot-manifest.json"
DATABASE_NAME = "state.sqlite3"
MAX_JSON_BYTES = 65_536
MAX_DATABASE_BYTES = 268_435_456
BACKUP_ID = re.compile(r"sbk_[0-9a-f]{32}\Z")
QUARANTINE_ID = re.compile(r"sq_[0-9a-f]{32}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
SCHEMA = """CREATE TABLE store_snapshot_quarantine (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), body TEXT NOT NULL
)"""
_BODY_FIELDS = {
    "schema_version", "rules_version", "backup_id", "quarantine_id", "quarantined",
    "created_at", "source_revision", "event_high_water",
}
_INTENT_FIELDS = {"schema_version", "rules_version", "kind", "backup_id", "created_at"}
_MANIFEST_FIELDS = {
    "schema_version", "rules_version", "kind", "backup_id", "created_at", "database",
    "schema_fingerprint", "revision", "event_high_water", "event_count", "table_counts",
    "quarantine", "integrity", "foreign_keys",
}


def _require(condition: bool, message: str, *, code: str = "store_snapshot_invalid") -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and value > 0


def _strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("Duplicate JSON key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise ValueError("Nonfinite JSON constant")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)


def validate_body(body: Any) -> dict[str, Any]:
    """Return the exact bounded marker body, with no authority inferred from it."""
    try:
        _require(isinstance(body, dict) and set(body) == _BODY_FIELDS, "Unexpected snapshot marker fields")
        _require(type(body["schema_version"]) is int and body["schema_version"] == 1
                 and body["rules_version"] == RULES_VERSION and body["quarantined"] is True,
                 "Unsupported snapshot marker or inspection flag")
        _require(isinstance(body["backup_id"], str) and bool(BACKUP_ID.fullmatch(body["backup_id"]))
                 and isinstance(body["quarantine_id"], str) and bool(QUARANTINE_ID.fullmatch(body["quarantine_id"])),
                 "Invalid snapshot marker identifiers")
        _require(_timestamp(body["created_at"]) and _integer(body["source_revision"])
                 and _integer(body["event_high_water"]), "Invalid snapshot marker time or counters")
        encoded = canonical(body)
        _require(len(encoded.encode("utf-8")) <= MAX_JSON_BYTES, "Snapshot marker exceeds its size limit")
        return _strict_json(encoded)
    except WorkbenchError:
        raise
    except (KeyError, ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("store_snapshot_invalid", "Malformed snapshot marker") from error


def quarantine_present(connection: sqlite3.Connection) -> bool:
    """Even a malformed object under the reserved marker name is a signal."""
    return connection.execute("SELECT 1 FROM main.sqlite_schema WHERE name=? COLLATE NOCASE", (QUARANTINE_TABLE,)).fetchone() is not None


def read_marker(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Read one exact marker, rejecting malformed or empty reserved tables."""
    try:
        row = connection.execute("SELECT type,sql FROM main.sqlite_schema WHERE name=? COLLATE NOCASE", (QUARANTINE_TABLE,)).fetchone()
        if row is None:
            return None
        expected = " ".join(SCHEMA.split())
        _require(row[0] == "table" and isinstance(row[1], str)
                 and " ".join(row[1].rstrip(";").split()) == expected,
                 "Snapshot marker table schema differs from its supported definition")
        columns = list(connection.execute("PRAGMA main.table_info(store_snapshot_quarantine)"))
        _require([tuple(item) for item in columns] == [
            (0, "singleton", "INTEGER", 0, None, 1), (1, "body", "TEXT", 1, None, 0),
        ], "Snapshot marker columns differ from their supported definition")
        rows = list(connection.execute("SELECT singleton,typeof(body),length(CAST(body AS BLOB)) FROM main.store_snapshot_quarantine LIMIT 2"))
        _require(len(rows) == 1 and type(rows[0][0]) is int and rows[0][0] == 1
                 and rows[0][1] == "text" and type(rows[0][2]) is int
                 and 0 < rows[0][2] <= MAX_JSON_BYTES, "Snapshot marker row is absent or malformed")
        text = connection.execute("SELECT body FROM main.store_snapshot_quarantine WHERE singleton=1").fetchone()[0]
        body = validate_body(_strict_json(text))
        _require(canonical(body) == text, "Snapshot marker body is not canonical JSON")
        return body
    except WorkbenchError:
        raise
    except (sqlite3.Error, KeyError, ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("store_snapshot_invalid", "Cannot validate the reserved snapshot marker") from error


def quarantine_info(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Return the full public marker; access flags are provided by Store."""
    return read_marker(connection)


def initialize_marker(connection: sqlite3.Connection, body: Any) -> dict[str, Any]:
    """Install a marker only in a copy; never replace or remove an existing one."""
    marker = validate_body(body)
    if quarantine_present(connection):
        previous = read_marker(connection)
        _require(canonical(previous) == canonical(marker), "Snapshot already has a different marker",
                 code="store_snapshot_marker_conflict")
        return marker
    connection.execute(SCHEMA)
    connection.execute("INSERT INTO store_snapshot_quarantine(singleton,body) VALUES (1,?)", (canonical(marker),))
    return marker


def readonly_connection(path: str | Path) -> sqlite3.Connection:
    connection = sqlite3.connect(Path(path).absolute().as_uri() + "?mode=ro", uri=True,
                                 timeout=30, isolation_level=None)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection
    except BaseException:
        connection.close()
        raise


def _sidecar(path: Path) -> dict[str, Any]:
    descriptor = None
    try:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        _require(hasattr(os, "O_NOFOLLOW"), "Snapshot sidecar inspection requires no-follow file support")
        descriptor = os.open(path, flags)
        metadata = os.fstat(descriptor)
        _require(stat.S_ISREG(metadata.st_mode) and metadata.st_mode & 0o077 == 0
                 and 0 < metadata.st_size <= MAX_JSON_BYTES,
                 "Snapshot sidecar must be a bounded private regular file")
        chunks = []
        size = 0
        while size <= MAX_JSON_BYTES:
            chunk = os.read(descriptor, min(16_384, MAX_JSON_BYTES + 1 - size))
            if not chunk:
                break
            chunks.append(chunk)
            size += len(chunk)
        _require(size <= MAX_JSON_BYTES, "Snapshot sidecar exceeds its size limit")
        text = b"".join(chunks).decode("utf-8")
        value = _strict_json(text)
        _require(isinstance(value, dict), "Snapshot sidecar must contain a JSON object")
        _require(canonical(value) == text, "Snapshot sidecar is not canonical JSON")
        return value
    except WorkbenchError:
        raise
    except (OSError, ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("store_snapshot_invalid", "Snapshot sidecar is unreadable or malformed") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _header(value: dict[str, Any], fields: set[str]) -> None:
    _require(set(value) == fields and type(value["schema_version"]) is int
             and value["schema_version"] == 1 and value["rules_version"] == RULES_VERSION
             and value["kind"] in {"backup", "restore"}
             and isinstance(value["backup_id"], str) and bool(BACKUP_ID.fullmatch(value["backup_id"]))
             and _timestamp(value["created_at"]), "Unsupported snapshot sidecar header")


def _complete(path: Path, marker: dict[str, Any]) -> None:
    intent = _sidecar(path.parent / INTENT_NAME)
    manifest = _sidecar(path.parent / MANIFEST_NAME)
    try:
        _header(intent, _INTENT_FIELDS)
        _header(manifest, _MANIFEST_FIELDS)
        _require(path.name == DATABASE_NAME and intent["kind"] == manifest["kind"]
                 and intent["backup_id"] == manifest["backup_id"] == marker["backup_id"]
                 and intent["created_at"] == manifest["created_at"], "Snapshot intent and completion manifest disagree")
        database = manifest["database"]
        _require(isinstance(database, dict) and set(database) == {"name", "bytes", "sha256"}
                 and database["name"] == DATABASE_NAME and type(database["bytes"]) is int
                 and 0 < database["bytes"] <= MAX_DATABASE_BYTES
                 and isinstance(database["sha256"], str) and bool(HASH.fullmatch(database["sha256"]))
                 and database["bytes"] == path.stat().st_size, "Snapshot database completion metadata differs")
        _require(isinstance(manifest["schema_fingerprint"], str)
                 and bool(HASH.fullmatch(manifest["schema_fingerprint"]))
                 and _integer(manifest["revision"]) and _integer(manifest["event_high_water"])
                 and _integer(manifest["event_count"]) and manifest["event_count"] <= manifest["event_high_water"]
                 and manifest["revision"] == marker["source_revision"]
                 and manifest["event_high_water"] == marker["event_high_water"]
                 and manifest["integrity"] == "ok" and manifest["foreign_keys"] == "ok",
                 "Invalid snapshot completion counters or integrity flags")
        counts = manifest["table_counts"]
        _require(isinstance(counts, dict) and 1 <= len(counts) <= 256
                 and all(isinstance(name, str) and 1 <= len(name) <= 200 and _integer(count)
                         for name, count in counts.items())
                 and counts.get("events") == manifest["event_count"], "Invalid snapshot table counts")
        _require(canonical(validate_body(manifest["quarantine"])) == canonical(marker),
                 "Snapshot completion manifest pins another quarantine marker")
    except WorkbenchError:
        raise
    except (OSError, KeyError, ValueError, TypeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("store_snapshot_invalid", "Malformed snapshot completion manifest") from error


def probe(path: str | Path) -> dict[str, Any] | None:
    """Inspect before mkdir, writable connect, WAL changes or schema migrations.

    The Store checks completeness and identity here. Snapshot inspection and
    restoration additionally verify the full database checksum and integrity.
    """
    location = Path(path)
    intended = os.path.lexists(location.parent / INTENT_NAME) or os.path.lexists(location.parent / MANIFEST_NAME)
    if not location.exists():
        _require(not intended and not os.path.lexists(location), "Incomplete snapshot cannot initialize a new active Store",
                 code="store_snapshot_incomplete")
        return None
    _require(location.is_file(), "Store database path must name a regular file")
    connection = None
    try:
        connection = readonly_connection(location)
        connection.execute("BEGIN")
        marker = read_marker(connection)
        if intended:
            _require(location.name == DATABASE_NAME and marker is not None,
                     "Snapshot intent has no matching marked database", code="store_snapshot_incomplete")
            _complete(location, marker)
        return marker
    except WorkbenchError:
        raise
    except sqlite3.Error as error:
        raise WorkbenchError("store_snapshot_invalid", "Cannot inspect the existing Store database") from error
    finally:
        if connection is not None:
            connection.close()
