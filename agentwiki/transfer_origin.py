"""Recognize completed, path-bound imports and retain accepted dependencies.

The origin row and completion files are private local records, not signatures.
Old authority is never copied into the new Store. A historical quarantined or
source-fenced copy remains readable without the active destination path check.
"""

from __future__ import annotations

import math
import os
from pathlib import Path
import re
import sqlite3
from typing import Any

from .store import CLAIM_ID, HASH, QUERY_SCOPES, WorkbenchError, canonical, safe_file_id


RULES_VERSION = "source-transfer-1"
METAKEY = "__transfer_origin__"
ORIGINKEY = METAKEY
INTENT_NAME = ".agentwiki-transfer-intent.json"
MANIFEST_NAME = ".agentwiki-transfer-manifest.json"
DATABASE_NAME = "state.sqlite3"
HISTORY_NAME = "history.sqlite3"
MAX_ORIGIN_BYTES = 1024 * 1024
MAX_SIDECAR_BYTES = 65_536
MAX_ARTIFACTS = 10_000
MAX_OLD_CREDENTIALS = 10_000
TRANSFER_ID = re.compile(r"sft_[0-9a-f]{32}\Z")
WORKSPACE_ID = re.compile(r"wsp_[0-9a-f]{32}\Z")
GRANT_ID = re.compile(r"gvg_[0-9a-f]{32}\Z")
OPERATION_ID = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
ORIGIN_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "destination_path",
    "source_path", "history_path", "import_revision", "artifact_anchors",
    "old_credential_hashes", "request_digest", "new_administrator_grant_id", "ready",
}
INTENT_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "operation_id",
    "destination_path", "request_digest",
}
MANIFEST_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "destination_path",
    "request_digest", "database", "history", "receipt",
}
RECEIPT_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "operation_id", "created_at",
    "source_fence_id", "source_epoch", "source_revision", "source_event_high_water_before",
    "transfer_event_seq", "artifact_count", "source_count", "new_administrator_id",
    "dependencies_preserved", "old_authority_transferred", "external_fencing_established",
}


def _require(condition: bool, message: str, *, incomplete: bool = False) -> None:
    if not condition:
        raise WorkbenchError("store_transfer_incomplete" if incomplete else "store_transfer_invalid", message)


def _integer(value: Any, *, positive: bool = False) -> bool:
    return type(value) is int and (1 if positive else 0) <= value < 2**63


def _hash(value: Any) -> bool:
    return isinstance(value, str) and bool(HASH.fullmatch(value))


def _path(value: Any) -> Path:
    from . import store_backup

    _require(isinstance(value, str) and 0 < len(value.encode("utf-8")) <= 4096,
             "Transfer origin has an unsupported path")
    try:
        path = store_backup._path(value)
    except WorkbenchError as error:
        raise WorkbenchError("store_transfer_invalid", "Transfer origin has an unsupported path") from error
    _require(str(path) == value, "Transfer origin paths must be canonical absolute paths")
    return path


def _artifact_id(value: Any) -> bool:
    return isinstance(value, str) and bool(
        CLAIM_ID.fullmatch(value) or safe_file_id(value, "document") or safe_file_id(value, "code"))


def _dependency(value: Any, artifact_id: str, revision: int) -> None:
    _require(isinstance(value, dict) and set(value) == {
        "proposal_id", "baseline_revision", "readset", "queries", "write_ids",
    }, "Imported dependency record has unsupported fields")
    _require((value["proposal_id"] is None or isinstance(value["proposal_id"], str)
              and re.fullmatch(r"p_[0-9a-f]{24}", value["proposal_id"]) is not None)
             and _integer(value["baseline_revision"], positive=True)
             and value["baseline_revision"] <= revision,
             "Imported dependency record has invalid baseline provenance")
    _require(isinstance(value["readset"], dict)
             and all(_artifact_id(key) and (item is None or _hash(item))
                     for key, item in value["readset"].items())
             and isinstance(value["queries"], dict)
             and all(scope in QUERY_SCOPES and _hash(item) for scope, item in value["queries"].items()),
             "Imported dependency record has invalid read or query pins")
    writes = value["write_ids"]
    _require(isinstance(writes, list) and 0 < len(writes) <= MAX_ARTIFACTS
             and all(_artifact_id(key) for key in writes)
             and len(writes) == len(set(writes)) and artifact_id in writes,
             "Imported dependency record has an invalid write set")


def validate_origin(body: Any) -> dict[str, Any]:
    """Validate the private, immutable origin body without opening paths."""
    from . import store_quarantine

    try:
        _require(isinstance(body, dict) and set(body) == ORIGIN_FIELDS,
                 "Transfer origin has unsupported fields")
        _require(type(body["schema_version"]) is int and body["schema_version"] == 1
                 and body["rules_version"] == RULES_VERSION and body["ready"] is True
                 and isinstance(body["transfer_id"], str) and TRANSFER_ID.fullmatch(body["transfer_id"])
                 and isinstance(body["workspace_id"], str) and WORKSPACE_ID.fullmatch(body["workspace_id"])
                 and _integer(body["import_revision"], positive=True)
                 and _hash(body["request_digest"])
                 and isinstance(body["new_administrator_grant_id"], str)
                 and GRANT_ID.fullmatch(body["new_administrator_grant_id"]),
                 "Transfer origin has invalid rules, identity or readiness")
        destination, source, history = (_path(body[key]) for key in (
            "destination_path", "source_path", "history_path"))
        _require(destination.name == DATABASE_NAME and history == destination.parent / HISTORY_NAME
                 and source != destination and source != history,
                 "Transfer origin paths do not identify one fresh destination and history")
        hashes = body["old_credential_hashes"]
        _require(isinstance(hashes, list) and 0 < len(hashes) <= MAX_OLD_CREDENTIALS
                 and all(_hash(item) for item in hashes)
                 and hashes == sorted(set(hashes)), "Transfer origin has an invalid retired-credential set")
        anchors = body["artifact_anchors"]
        _require(isinstance(anchors, dict) and 0 < len(anchors) <= MAX_ARTIFACTS,
                 "Transfer origin has an invalid artifact inventory")
        for identifier, anchor in anchors.items():
            _require(_artifact_id(identifier) and isinstance(anchor, dict)
                     and set(anchor) == {"digest", "dependency"} and _hash(anchor["digest"]),
                     "Transfer origin has an invalid artifact anchor")
            _dependency(anchor["dependency"], identifier, body["import_revision"])
        encoded = canonical(body)
        _require(len(encoded.encode("utf-8")) <= MAX_ORIGIN_BYTES,
                 "Transfer origin exceeds its supported byte bound")
        return store_quarantine._strict_json(encoded)
    except WorkbenchError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("store_transfer_invalid", "Transfer origin is malformed") from error


def origin_present(connection: sqlite3.Connection) -> bool:
    try:
        if connection.execute("SELECT 1 FROM main.sqlite_schema WHERE name='meta' COLLATE NOCASE").fetchone() is None:
            return False
        return connection.execute(
            "SELECT 1 FROM main.meta WHERE CAST(key AS TEXT)=? COLLATE NOCASE LIMIT 1", (METAKEY,)
        ).fetchone() is not None
    except sqlite3.Error as error:
        raise WorkbenchError("store_transfer_invalid", "Cannot inspect transfer origin metadata") from error


def read_origin(connection: sqlite3.Connection) -> dict[str, Any] | None:
    """Read one bounded canonical origin, retaining private fields internally."""
    from . import store_quarantine

    try:
        if not origin_present(connection):
            return None
        where = "CAST(key AS TEXT)=? COLLATE NOCASE"
        headers = connection.execute(
            "SELECT typeof(key),length(CAST(key AS BLOB)),typeof(value),length(CAST(value AS BLOB)) "
            "FROM main.meta WHERE " + where + " LIMIT 2", (METAKEY,)
        ).fetchall()
        _require(len(headers) == 1 and headers[0][0] == "text"
                 and headers[0][1] == len(METAKEY) and headers[0][2] == "text"
                 and type(headers[0][3]) is int and 0 < headers[0][3] <= MAX_ORIGIN_BYTES,
                 "Transfer origin row is absent, ambiguous or oversized")
        row = connection.execute("SELECT key,value FROM main.meta WHERE " + where, (METAKEY,)).fetchone()
        _require(row is not None and row[0] == METAKEY, "Transfer origin key is not canonical")
        body = validate_origin(store_quarantine._strict_json(row[1]))
        _require(canonical(body) == row[1], "Transfer origin is not canonical JSON")
        return body
    except WorkbenchError:
        raise
    except (sqlite3.Error, KeyError, TypeError, ValueError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("store_transfer_invalid", "Cannot validate transfer origin metadata") from error


def _public(body: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION, "transfer_id": body["transfer_id"],
            "workspace_id": body["workspace_id"], "import_revision": body["import_revision"],
            "new_administrator_grant_id": body["new_administrator_grant_id"],
            "dependencies_preserved": True, "old_authority_transferred": False, "path_bound": True}


def origin_info(connection: sqlite3.Connection) -> dict[str, Any] | None:
    body = read_origin(connection)
    return _public(body) if body is not None else None


def persist_origin(connection: sqlite3.Connection, body: Any) -> dict[str, Any]:
    """Install one origin in the fresh builder transaction; never overwrite."""
    value = validate_origin(body)
    _require(connection.in_transaction, "Transfer origin installation needs an existing transaction")
    _require(not origin_present(connection), "Transfer origin already exists")
    connection.execute("INSERT INTO main.meta(key,value) VALUES (?,?)", (METAKEY, canonical(value)))
    return _public(value)


def _sidecar(path: Path) -> dict[str, Any]:
    from . import store_backup

    try:
        return store_backup._read_json(path, MAX_SIDECAR_BYTES)
    except WorkbenchError as error:
        raise WorkbenchError("store_transfer_incomplete" if error.code == "snapshot_incomplete" else "store_transfer_invalid",
                             "Transfer completion files are unavailable or invalid") from error
    except OSError as error:
        raise WorkbenchError("store_transfer_invalid", "Transfer completion files cannot be read") from error


def _identity(body: Any, fields: set[str]) -> None:
    _require(isinstance(body, dict) and set(body) == fields
             and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION
             and isinstance(body["transfer_id"], str) and TRANSFER_ID.fullmatch(body["transfer_id"])
             and isinstance(body["workspace_id"], str) and WORKSPACE_ID.fullmatch(body["workspace_id"])
             and _hash(body["request_digest"]), "Transfer completion identity is invalid")
    _path(body["destination_path"])


def validate_receipt(receipt: Any) -> dict[str, Any]:
    _require(isinstance(receipt, dict) and set(receipt) == RECEIPT_FIELDS,
             "Transfer receipt has unsupported fields")
    _require(type(receipt["schema_version"]) is int and receipt["schema_version"] == 1
             and receipt["rules_version"] == RULES_VERSION
             and isinstance(receipt["transfer_id"], str) and TRANSFER_ID.fullmatch(receipt["transfer_id"])
             and isinstance(receipt["workspace_id"], str) and WORKSPACE_ID.fullmatch(receipt["workspace_id"])
             and isinstance(receipt["operation_id"], str) and OPERATION_ID.fullmatch(receipt["operation_id"])
             and not BEARER.search(receipt["operation_id"]) and not HASH.fullmatch(receipt["operation_id"])
             and type(receipt["created_at"]) in {int, float} and math.isfinite(receipt["created_at"])
             and 0 < receipt["created_at"] < 2**63
             and isinstance(receipt["source_fence_id"], str) and re.fullmatch(r"sfn_[0-9a-f]{32}", receipt["source_fence_id"])
             and isinstance(receipt["source_epoch"], str) and re.fullmatch(r"sfe_[0-9a-f]{32}", receipt["source_epoch"])
             and _integer(receipt["source_revision"], positive=True)
             and _integer(receipt["source_event_high_water_before"])
             and _integer(receipt["transfer_event_seq"], positive=True)
             and receipt["transfer_event_seq"] > receipt["source_event_high_water_before"]
             and _integer(receipt["artifact_count"], positive=True) and receipt["artifact_count"] <= MAX_ARTIFACTS
             and _integer(receipt["source_count"])
             and isinstance(receipt["new_administrator_id"], str)
             and OPERATION_ID.fullmatch(receipt["new_administrator_id"])
             and not BEARER.search(receipt["new_administrator_id"])
             and not HASH.fullmatch(receipt["new_administrator_id"])
             and receipt["dependencies_preserved"] is True
             and receipt["old_authority_transferred"] is False
             and receipt["external_fencing_established"] is False,
             "Transfer receipt has invalid identity, counters or authority flags")
    return dict(receipt)


def _file_header(value: Any, name: str) -> None:
    from . import store_backup

    _require(isinstance(value, dict) and set(value) == {"name", "bytes", "sha256"}
             and value["name"] == name and _integer(value["bytes"], positive=True)
             and value["bytes"] <= store_backup.MAX_DATABASE_BYTES and _hash(value["sha256"]),
             "Transfer completion database header is invalid")


def _complete(path: Path, body: dict[str, Any]) -> None:
    from . import store_backup

    try:
        store_backup._no_symlinks(path)
        store_backup._private_directory(path.parent)
        intent_path, manifest_path = path.parent / INTENT_NAME, path.parent / MANIFEST_NAME
        _require(os.path.lexists(intent_path) and os.path.lexists(manifest_path),
                 "Transfer destination is not complete", incomplete=True)
        intent, manifest = _sidecar(intent_path), _sidecar(manifest_path)
        _identity(intent, INTENT_FIELDS)
        _identity(manifest, MANIFEST_FIELDS)
        common = ("schema_version", "rules_version", "transfer_id", "workspace_id", "destination_path", "request_digest")
        _require(all(intent[key] == manifest[key] == body[key] for key in common),
                 "Transfer origin and completion identities disagree")
        _require(isinstance(intent["operation_id"], str) and OPERATION_ID.fullmatch(intent["operation_id"])
                 and not BEARER.search(intent["operation_id"]) and not HASH.fullmatch(intent["operation_id"]),
                 "Transfer intent has an invalid operation identifier")
        _file_header(manifest["database"], DATABASE_NAME)
        _file_header(manifest["history"], HISTORY_NAME)
        receipt = validate_receipt(manifest["receipt"])
        _require(receipt["transfer_id"] == body["transfer_id"]
                 and receipt["workspace_id"] == body["workspace_id"]
                 and receipt["operation_id"] == intent["operation_id"]
                 and receipt["source_revision"] == body["import_revision"]
                 and receipt["artifact_count"] == len(body["artifact_anchors"]),
                 "Transfer completion receipt pins another imported baseline")
        with store_backup._regular_file(path, maximum=2**63 - 1):
            pass
        history = Path(body["history_path"])
        with store_backup._regular_file(history) as descriptor:
            _require(os.fstat(descriptor).st_size == manifest["history"]["bytes"],
                     "Transfer history file differs from its completion size")
        # The destination is mutable after activation: its initial checksum and
        # byte size are not compared with current state on every transaction.
    except WorkbenchError as error:
        if error.code in {"store_transfer_incomplete", "store_transfer_invalid"}:
            raise
        raise WorkbenchError("store_transfer_invalid", "Transfer completion files cannot be validated") from error
    except (OSError, KeyError, ValueError, TypeError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("store_transfer_invalid", "Transfer completion metadata is malformed") from error


def check_access(connection: sqlite3.Connection, path: str | Path, *, require_complete: bool = True) -> dict[str, Any] | None:
    """Validate active path binding; fenced and quarantined history stays RO."""
    from . import source_fence, store_quarantine

    location = Path(path).absolute()
    body = read_origin(connection)
    if store_quarantine.quarantine_info(connection) is not None or source_fence.fence_info(connection) is not None:
        return _public(body) if body is not None else None
    intended = location.name == DATABASE_NAME and any(os.path.lexists(location.parent / name)
                                                     for name in (INTENT_NAME, MANIFEST_NAME))
    if body is None:
        _require(not intended, "Transfer intent has no matching ready imported database", incomplete=True)
        return None
    _require(str(location) == body["destination_path"], "Imported database is bound to another canonical destination")
    if require_complete:
        _complete(location, body)
    return _public(body)


def probe(path: str | Path) -> dict[str, Any] | None:
    """Check import completion before Store creates a DB or performs schema work."""
    from . import source_fence, store_quarantine

    location = Path(path).absolute()
    intended = location.name == DATABASE_NAME and any(os.path.lexists(location.parent / name)
                                                     for name in (INTENT_NAME, MANIFEST_NAME))
    if not location.exists():
        _require(not intended and not os.path.lexists(location),
                 "Incomplete transfer cannot initialize a new active Store", incomplete=True)
        return None
    _require(location.is_file(), "Transfer database must be a regular file")
    connection = None
    try:
        connection = store_quarantine.readonly_connection(location)
        connection.execute("PRAGMA trusted_schema=OFF")
        connection.execute("BEGIN")
        # A real read-only marker takes precedence over active path checks.
        if store_quarantine.quarantine_info(connection) is not None or source_fence.fence_info(connection) is not None:
            body = read_origin(connection)
            return _public(body) if body is not None else None
        return check_access(connection, location)
    except WorkbenchError:
        raise
    except (OSError, sqlite3.Error) as error:
        raise WorkbenchError("store_transfer_invalid", "Cannot inspect imported workspace origin") from error
    finally:
        if connection is not None:
            connection.close()


def dependency_from_origin(body: dict[str, Any] | None, artifact_id: str, revision: int,
                           artifact_digest: str) -> dict[str, Any] | None:
    """Look up one anchor in an origin already validated in this transaction."""
    from . import store_quarantine

    if body is None:
        return None
    anchor = body["artifact_anchors"].get(artifact_id)
    _require(revision == body["import_revision"] and anchor is not None
             and anchor["digest"] == artifact_digest,
             "Imported artifact no longer matches its baseline provenance")
    return store_quarantine._strict_json(canonical(anchor["dependency"]))


def dependency_for(connection: sqlite3.Connection, artifact_id: str, revision: int,
                   artifact_digest: str) -> dict[str, Any] | None:
    """Read and validate an origin for a single independent dependency lookup."""
    return dependency_from_origin(read_origin(connection), artifact_id, revision, artifact_digest)


def enforce_credential_fresh(connection: sqlite3.Connection, credential_hash: str) -> None:
    """Reject explicit reuse of every retired source credential in a new grant."""
    body = read_origin(connection)
    if body is not None and credential_hash in body["old_credential_hashes"]:
        raise WorkbenchError("governance_credential_conflict", "A retired source credential cannot authorize a new grant")
