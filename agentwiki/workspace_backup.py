"""Private inspection backups containing a Store and all ancestral archives.

The ordinary snapshot intent is installed first, and the copied database gains
the existing persistent quarantine before either completion manifest is written.
The outer manifest completes the archive set. Restoring never activates authority
or rewrites an imported origin to point at the historical copy's new directory.
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

from . import governance, recovery_reconciliation as recovery, source_fence
from . import source_transfer, store_backup, store_quarantine, transfer_origin
from .store import WorkbenchError, canonical


RULES_VERSION = "workspace-backup-1"
INTENT_NAME = ".agentwiki-workspace-intent.json"
MANIFEST_NAME = ".agentwiki-workspace-manifest.json"
ARCHIVES_NAME = "archives"
DATABASE_NAME = store_backup.DATABASE_NAME
MAX_MANIFEST_BYTES = store_backup.MAX_MANIFEST_BYTES
MAX_INTENT_BYTES = store_backup.MAX_INTENT_BYTES
DEADLINE_SECONDS = store_backup.DEADLINE_SECONDS
WORKSPACE_BACKUP_ID = re.compile(r"wbk_[0-9a-f]{32}\Z")
WORKSPACE_ID = re.compile(r"wsp_[0-9a-f]{32}\Z")
INTENT_FIELDS = {"schema_version", "rules_version", "kind", "workspace_backup_id", "created_at", "workspace_id"}
MANIFEST_FIELDS = INTENT_FIELDS | {"snapshot", "archives"}
FLAGS = {"inspection_only": True, "domain_writes_allowed": False, "full_history_preserved": True,
         "old_authority_activated": False, "external_fencing_established": False}
PUBLIC_FIELDS = {"rules_version", "kind", "workspace_backup_id", "created_at", "workspace_id",
                 "revision", "event_high_water", "event_count", "table_counts", "archive_count", "receipts",
                 "integrity", "foreign_keys", *FLAGS}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "workspace_backup_limit_exceeded", "Workspace backup exceeded its time bound")


def _path(value: Any) -> Path:
    location = store_backup._path(value)
    _require(len(str(location).encode("utf-8")) <= 4096,
             "workspace_backup_invalid_input", "Workspace backup path exceeds its byte bound")
    return location


def _intent(kind: str, backup_id: str, created_at: float, workspace_id: str) -> dict[str, Any]:
    return {"schema_version": 1, "rules_version": RULES_VERSION, "kind": kind,
            "workspace_backup_id": backup_id, "created_at": created_at, "workspace_id": workspace_id}


def _paths_bound(directory: Path, records: list[dict[str, Any]]) -> None:
    locations = [directory / name for name in (
        DATABASE_NAME, INTENT_NAME, MANIFEST_NAME, store_backup.INTENT_NAME, store_backup.MANIFEST_NAME, ARCHIVES_NAME)]
    locations.extend(directory / ARCHIVES_NAME / record["name"] for record in records)
    _require(all(len(str(location).encode("utf-8")) <= 4096 for location in locations),
             "workspace_backup_invalid_input", "A generated workspace backup path exceeds its byte bound")


def _manifest_valid(body: Any) -> dict[str, Any]:
    _require(isinstance(body, dict) and set(body) == MANIFEST_FIELDS
             and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and body["kind"] in {"backup", "restore"}
             and isinstance(body["workspace_backup_id"], str) and WORKSPACE_BACKUP_ID.fullmatch(body["workspace_backup_id"])
             and isinstance(body["workspace_id"], str) and WORKSPACE_ID.fullmatch(body["workspace_id"])
             and type(body["created_at"]) in {int, float} and math.isfinite(body["created_at"])
             and 0 < body["created_at"] < 2**63
             and isinstance(body["snapshot"], dict),
             "workspace_backup_manifest_invalid", "Workspace backup manifest is malformed")
    store_backup._manifest_valid(body["snapshot"])
    _require(body["snapshot"]["kind"] == body["kind"] and body["snapshot"]["created_at"] == body["created_at"],
             "workspace_backup_manifest_invalid", "Workspace and snapshot operations disagree")
    archives = transfer_origin.validate_archives(body["archives"])
    _require(archives[-1]["receipt"]["workspace_id"] == body["workspace_id"],
             "workspace_backup_manifest_invalid", "Workspace archives identify another imported origin")
    _require(len(canonical(body).encode("utf-8")) <= MAX_MANIFEST_BYTES,
             "workspace_backup_limit_exceeded", "Workspace manifest exceeds its byte bound")
    return store_quarantine._strict_json(canonical(body))


def _names(directory: Path, maximum: int) -> set[str]:
    names: set[str] = set()
    with os.scandir(directory) as entries:
        for entry in entries:
            names.add(entry.name)
            _require(len(names) <= maximum, "workspace_backup_manifest_invalid", "Workspace bundle contains unsupported entries")
    return names


def _layout(directory: Path, records: list[dict[str, Any]], *, complete: bool) -> None:
    expected = {INTENT_NAME, ARCHIVES_NAME, DATABASE_NAME, store_backup.INTENT_NAME, store_backup.MANIFEST_NAME}
    if complete:
        expected.add(MANIFEST_NAME)
    names = _names(directory, 6)
    _require(expected <= names, "workspace_backup_incomplete", "Workspace backup is incomplete")
    _require(names == expected, "workspace_backup_manifest_invalid", "Workspace bundle contains unsupported entries")
    archive_directory = directory / ARCHIVES_NAME
    store_backup._private_directory(archive_directory)
    expected_archives = {record["name"] for record in records}
    actual_archives = _names(archive_directory, transfer_origin.MAX_ARCHIVES)
    _require(expected_archives <= actual_archives, "workspace_backup_incomplete", "Workspace ancestral archive is missing")
    _require(actual_archives == expected_archives,
             "workspace_backup_manifest_invalid", "Workspace archive directory contains unsupported entries")


def _summary(body: dict[str, Any]) -> dict[str, Any]:
    snapshot = body["snapshot"]
    return {"rules_version": RULES_VERSION, "kind": body["kind"],
            "workspace_backup_id": body["workspace_backup_id"], "created_at": body["created_at"],
            "workspace_id": body["workspace_id"],
            **{key: snapshot[key] for key in ("revision", "event_high_water", "event_count", "integrity", "foreign_keys")},
            "table_counts": dict(snapshot["table_counts"]), "archive_count": len(body["archives"]),
            "receipts": [source_transfer._receipt(record["receipt"]) for record in body["archives"]], **FLAGS}


def _inspect_contents(directory: Path, body: dict[str, Any], deadline: float, *, complete: bool) -> None:
    _layout(directory, body["archives"], complete=complete)
    snapshot = body["snapshot"]
    intent = store_backup._read_json(directory / store_backup.INTENT_NAME, store_backup.MAX_INTENT_BYTES)
    actual = store_backup._read_json(directory / store_backup.MANIFEST_NAME, store_backup.MAX_MANIFEST_BYTES)
    _require(canonical(actual) == canonical(snapshot) and set(intent) == store_backup.INTENT_FIELDS
             and canonical(intent) == canonical({key: snapshot[key] for key in store_backup.INTENT_FIELDS}),
             "workspace_backup_manifest_invalid", "Workspace snapshot completion files disagree")
    size, checksum = store_backup._file_digest(directory / DATABASE_NAME, deadline)
    _require(size == snapshot["database"]["bytes"] and checksum == snapshot["database"]["sha256"],
             "workspace_backup_checksum_mismatch", "Workspace snapshot checksum differs from its private manifest")
    store_backup._inspect_database(directory / DATABASE_NAME, snapshot, deadline)
    verified = source_transfer._verify_archives(directory / ARCHIVES_NAME, body["archives"], deadline)
    with store_backup._connection(directory / DATABASE_NAME, deadline, readonly=True, immutable=True) as copied:
        copied.execute("BEGIN")
        recovery._guard_authorization(copied, deadline)
        origin = transfer_origin.read_origin(copied)
        _require(origin is not None and origin["workspace_id"] == body["workspace_id"]
                 and source_fence.fence_info(copied) is None,
                 "workspace_backup_manifest_invalid", "Copied workspace origin is absent or stopped")
        source_transfer._import_matches(copied, origin, verified["last_binding"], verified["last_anchors"],
                                        body["archives"], verified["retired_hashes"])
    _deadline(deadline)


def _load_bundle(directory: Path, deadline: float) -> dict[str, Any]:
    store_backup._private_directory(directory)
    intent = store_backup._read_json(directory / INTENT_NAME, MAX_INTENT_BYTES)
    body = _manifest_valid(store_backup._read_json(directory / MANIFEST_NAME, MAX_MANIFEST_BYTES))
    _require(set(intent) == INTENT_FIELDS
             and canonical(intent) == canonical({key: body[key] for key in INTENT_FIELDS}),
             "workspace_backup_manifest_invalid", "Workspace intent and manifest disagree")
    _inspect_contents(directory, body, deadline, complete=True)
    return body


def _source_state(source: sqlite3.Connection, path: Path, credential: Any, deadline: float) -> dict[str, Any]:
    _require(not store_quarantine.quarantine_present(source),
             "workspace_backup_source_invalid", "A quarantined snapshot cannot authorize a new full backup")
    _require(source_fence.fence_info(source) is None,
             "store_source_fenced", "A stopped workspace cannot authorize a new full backup")
    metadata = source_fence._database(source, deadline)
    recovery._guard_authorization(source, deadline)
    administrator = governance.authorize(source, credential, "administrator", now=time.time())
    origin = transfer_origin.read_origin(source)
    _require(origin is not None, "workspace_backup_source_invalid", "Full backup requires an imported workspace")
    manifest = transfer_origin.completion_manifest(source, path)
    records = source_transfer._archive_records(manifest)
    verified = source_transfer._verify_archives(path.parent, records, deadline)
    source_transfer._import_matches(source, origin, verified["last_binding"], verified["last_anchors"],
                                    records, verified["retired_hashes"])
    return {"metadata": metadata, "administrator": administrator, "origin": origin, "archives": records}


def _snapshot(source: sqlite3.Connection, directory: Path, metadata: dict[str, Any],
              backup_id: str, created_at: float, deadline: float) -> dict[str, Any]:
    """Build the established quarantine format from the caller's pinned snapshot."""
    page_size = source.execute("PRAGMA page_size").fetchone()[0]
    copied_path = directory / DATABASE_NAME
    store_backup._new_database(copied_path)

    def progress(status: int, remaining: int, total: int) -> None:
        _deadline(deadline)
        _require(total * page_size <= store_backup.MAX_DATABASE_BYTES,
                 "workspace_backup_limit_exceeded", "Copied workspace exceeds its database byte bound")

    with store_backup._connection(copied_path, deadline, readonly=False) as copied:
        source.backup(copied, pages=store_backup.BACKUP_PAGES, progress=progress, sleep=0.05)
        store_backup._supported_schema(copied, marked=False)
        _require(canonical(store_backup._metadata(copied)) == canonical(metadata),
                 "workspace_backup_source_invalid", "Workspace copy differs from its pinned source")
        marker = {"schema_version": 1, "rules_version": store_backup.RULES_VERSION,
                  "backup_id": backup_id, "quarantine_id": "sq_" + secrets.token_hex(16),
                  "quarantined": True, "created_at": created_at,
                  "source_revision": metadata["revision"], "event_high_water": metadata["event_high_water"]}
        store_quarantine.initialize_marker(copied, marker)
        copied.commit()
        _require(copied.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete",
                 "workspace_backup_source_invalid", "Copied workspace could not become a standalone database")
        fingerprint = store_backup._supported_schema(copied, marked=True)
        _require(canonical(store_backup._metadata(copied)) == canonical(metadata),
                 "workspace_backup_source_invalid", "Quarantine changed copied domain state")
        store_backup._integrity(copied, deadline)
    size, checksum = store_backup._file_digest(copied_path, deadline, sync=True)
    snapshot = {"schema_version": 1, "rules_version": store_backup.RULES_VERSION, "kind": "backup",
                "backup_id": backup_id, "created_at": created_at,
                "database": {"name": DATABASE_NAME, "bytes": size, "sha256": checksum},
                "schema_fingerprint": fingerprint, **metadata, "quarantine": marker,
                "integrity": "ok", "foreign_keys": "ok"}
    store_backup._manifest_valid(snapshot)
    store_backup._inspect_database(copied_path, snapshot, deadline)
    return snapshot


def _copy_file(source: Path, target: Path, record: dict[str, Any], deadline: float) -> None:
    _require(not os.path.lexists(target), "workspace_backup_destination_exists", "Workspace copy destination already exists")
    source_transfer._copy_archive(source, target, record, deadline)


def create_backup(database_path: str | Path, destination: str | Path, *, credential: Any) -> dict[str, Any]:
    """Capture an active imported workspace and its complete ancestral history."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        source_path, destination_path = _path(database_path), _path(destination)
        _require(source_path.parent != destination_path and source_path.parent not in destination_path.parents,
                 "workspace_backup_invalid_input", "Workspace backup destination must be outside the active workspace")
        store_backup._private_directory(source_path.parent)
        with store_backup._regular_file(source_path, missing="workspace_backup_source_invalid"):
            pass
        _require(store_quarantine.probe(source_path) is None,
                 "workspace_backup_source_invalid", "A quarantined snapshot cannot authorize a new full backup")
        _require(source_fence.probe(source_path) is None,
                 "store_source_fenced", "A stopped workspace cannot authorize a new full backup")
        transfer_origin.probe(source_path)
        with store_backup._connection(source_path, deadline, readonly=True) as source:
            source.execute("BEGIN")
            state = _source_state(source, source_path, credential, deadline)
            _paths_bound(destination_path, state["archives"])
            created_at, snapshot_id = time.time(), "sbk_" + secrets.token_hex(16)
            reserved = _intent("backup", "wbk_" + secrets.token_hex(16), created_at, state["origin"]["workspace_id"])
            # This standard intent must precede every full-bundle filesystem
            # effect, so an incomplete directory cannot initialize an active DB.
            store_backup._reserve(destination_path, store_backup._intent("backup", snapshot_id, created_at))
            store_backup._write_json(destination_path / INTENT_NAME, reserved, MAX_INTENT_BYTES)
            archive_directory = destination_path / ARCHIVES_NAME
            archive_directory.mkdir(mode=0o700)
            archive_directory.chmod(0o700)
            snapshot = _snapshot(source, destination_path, state["metadata"], snapshot_id, created_at, deadline)
            source.rollback()
        for record in state["archives"]:
            _copy_file(source_path.parent / record["name"], archive_directory / record["name"], record, deadline)
        store_backup._fsync_directory(archive_directory)
        body = _manifest_valid({**reserved, "snapshot": snapshot, "archives": state["archives"]})
        store_backup._write_json(destination_path / store_backup.MANIFEST_NAME, snapshot, store_backup.MAX_MANIFEST_BYTES)
        _inspect_contents(destination_path, body, deadline, complete=False)
        # The final writer reservation serializes source revocation/fence
        # with full-manifest publication; it does not write source SQL rows.
        with store_backup._connection(source_path, deadline, readonly=False) as fresh:
            fresh.execute("BEGIN IMMEDIATE")
            current = _source_state(fresh, source_path, credential, deadline)
            _require(current["administrator"]["grant_id"] == state["administrator"]["grant_id"]
                     and canonical(current["origin"]) == canonical(state["origin"])
                     and canonical(current["archives"]) == canonical(state["archives"]),
                     "workspace_backup_source_invalid", "Workspace origin or authorization changed during capture")
            authorized = governance.authorize(fresh, credential, "administrator", now=time.time())
            _require(authorized["grant_id"] == state["administrator"]["grant_id"],
                     "governance_unauthorized", "Administrator grant changed during workspace capture")
            _deadline(deadline)
            store_backup._write_json(destination_path / MANIFEST_NAME, body, MAX_MANIFEST_BYTES)
            store_backup._fsync_directory(destination_path)
            fresh.rollback()
        return _summary(body)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_backup_io_failed", "Full workspace backup could not complete") from error


def inspect_bundle(bundle_path: str | Path) -> dict[str, Any]:
    """Verify a complete quarantined workspace without opening old live paths."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        return _summary(_load_bundle(_path(bundle_path), deadline))
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_backup_io_failed", "Full workspace inspection could not complete") from error


def restore_bundle(bundle_path: str | Path, destination: str | Path) -> dict[str, Any]:
    """Restore all verified files into one new private inspection-only bundle."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        source_path, destination_path = _path(bundle_path), _path(destination)
        _require(destination_path != source_path and source_path not in destination_path.parents,
                 "workspace_backup_invalid_input", "Workspace restore destination must be outside its source bundle")
        original = _load_bundle(source_path, deadline)
        created_at = time.time()
        snapshot = {**original["snapshot"], "kind": "restore", "created_at": created_at}
        reserved = _intent("restore", original["workspace_backup_id"], created_at, original["workspace_id"])
        body = _manifest_valid({**reserved, "snapshot": snapshot, "archives": original["archives"]})
        _paths_bound(destination_path, body["archives"])
        store_backup._reserve(destination_path, store_backup._intent("restore", snapshot["backup_id"], created_at))
        store_backup._write_json(destination_path / INTENT_NAME, reserved, MAX_INTENT_BYTES)
        archive_directory = destination_path / ARCHIVES_NAME
        archive_directory.mkdir(mode=0o700)
        archive_directory.chmod(0o700)
        _copy_file(source_path / DATABASE_NAME, destination_path / DATABASE_NAME, snapshot["database"], deadline)
        for record in body["archives"]:
            _copy_file(source_path / ARCHIVES_NAME / record["name"], archive_directory / record["name"], record, deadline)
        store_backup._fsync_directory(archive_directory)
        store_backup._write_json(destination_path / store_backup.MANIFEST_NAME, snapshot, store_backup.MAX_MANIFEST_BYTES)
        _inspect_contents(destination_path, body, deadline, complete=False)
        _deadline(deadline)
        store_backup._write_json(destination_path / MANIFEST_NAME, body, MAX_MANIFEST_BYTES)
        store_backup._fsync_directory(destination_path)
        return _summary(body)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_backup_io_failed", "Full workspace restore could not complete") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith(("workspace_backup_", "governance_")) or error.code == "store_source_fenced":
        return error
    if error.code.endswith("limit_exceeded"):
        return WorkbenchError("workspace_backup_limit_exceeded", "Workspace backup exceeded a supported bound")
    if error.code in {"snapshot_incomplete", "store_snapshot_incomplete", "store_transfer_incomplete", "source_transfer_incomplete"}:
        return WorkbenchError("workspace_backup_incomplete", "Full workspace backup is incomplete")
    if "checksum_mismatch" in error.code:
        return WorkbenchError("workspace_backup_checksum_mismatch", "Workspace files differ from their private pins")
    if error.code == "snapshot_destination_exists" or error.code == "source_transfer_destination_exists":
        return WorkbenchError("workspace_backup_destination_exists", "Workspace backup destination already exists")
    if error.code == "snapshot_private_path_required":
        return WorkbenchError("workspace_backup_private_path_required", "Workspace backup needs private owner-only paths")
    if error.code in {"snapshot_io_failed", "source_transfer_io_failed"}:
        return WorkbenchError("workspace_backup_io_failed", "Full workspace operation could not complete")
    if error.code == "snapshot_invalid_input":
        return WorkbenchError("workspace_backup_invalid_input", "Workspace backup input is unsupported")
    return WorkbenchError("workspace_backup_manifest_invalid", "Workspace source or completion metadata cannot be validated")
