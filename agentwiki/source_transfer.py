"""Create a fresh active baseline from one cooperatively fenced source.

A source reservation binds one canonical destination and fresh administrator.
Historical authority stays in the fenced native archive; accepted dependencies
are imported as anchored provenance, never as permission to reuse old work.
Private manifests, hashes, deny lists and request bindings are never returned.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Any

from . import authorship, content_gate, governance, recovery_reconciliation as recovery
from . import review_continuity, source_fence, store_backup, store_quarantine
from .store import HASH, Store, WorkbenchError, canonical, digest, _execute_schema

RULES_VERSION = "source-transfer-1"
METAKEY = "__source_transfer__"
INTENT_NAME = ".agentwiki-transfer-intent.json"
MANIFEST_NAME = ".agentwiki-transfer-manifest.json"
DATABASE_NAME = "state.sqlite3"
HISTORY_NAME = "history.sqlite3"
LOCK_NAME = ".agentwiki-transfer.lock"
MAX_JSON_BYTES = 65_536
MAX_MANIFEST_BYTES = 65_536
MAX_INTENT_BYTES = 4_000
MAX_ORIGIN_BYTES = 1024 * 1024
TRANSFER_ID = re.compile(r"sft_[0-9a-f]{32}\Z")
WORKSPACE_ID = re.compile(r"wsp_[0-9a-f]{32}\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
FLAGS = {"dependencies_preserved": True, "old_authority_transferred": False,
         "external_fencing_established": False}
RECEIPT_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "operation_id", "created_at",
    "source_fence_id", "source_epoch", "source_revision", "source_event_high_water_before",
    "transfer_event_seq", "artifact_count", "source_count", "new_administrator_id", *FLAGS,
}
ENVELOPE_FIELDS = {"schema_version", "rules_version", "request_digest", "destination_path",
                   "new_administrator_hash", "receipt"}
INTENT_FIELDS = {"schema_version", "rules_version", "transfer_id", "workspace_id", "operation_id",
                 "destination_path", "request_digest"}
MANIFEST_FIELDS = {"schema_version", "rules_version", "transfer_id", "workspace_id", "destination_path",
                   "request_digest", "database", "history", "receipt"}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "source_transfer_limit_exceeded", "Source transfer exceeded its time bound")


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _identifier(value: Any, forbidden: tuple[str, ...] = ()) -> bool:
    return (isinstance(value, str) and bool(IDENTIFIER.fullmatch(value)) and not BEARER.search(value)
            and not HASH.fullmatch(value) and not any(secret in value for secret in forbidden))


def _path(value: Any) -> Path:
    try:
        path = store_backup._path(value)
        _require(len(str(path).encode("utf-8")) <= 4096,
                 "source_transfer_invalid_input", "Transfer path exceeds its byte bound")
        return path
    except WorkbenchError as error:
        raise WorkbenchError("source_transfer_invalid_input", "Expected an explicit supported filesystem path") from error


def _receipt(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == RECEIPT_FIELDS
             and type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["rules_version"] == RULES_VERSION
             and isinstance(value["transfer_id"], str) and TRANSFER_ID.fullmatch(value["transfer_id"])
             and isinstance(value["workspace_id"], str) and WORKSPACE_ID.fullmatch(value["workspace_id"])
             and _identifier(value["operation_id"]) and _identifier(value["new_administrator_id"])
             and isinstance(value["source_fence_id"], str) and source_fence.FENCE_ID.fullmatch(value["source_fence_id"])
             and isinstance(value["source_epoch"], str) and source_fence.EPOCH_ID.fullmatch(value["source_epoch"])
             and type(value["created_at"]) in {int, float} and math.isfinite(value["created_at"])
             and 0 < value["created_at"] < 2**63
             and all(_integer(value[key]) for key in (
                 "source_revision", "source_event_high_water_before", "transfer_event_seq", "artifact_count", "source_count"))
             and value["source_revision"] > 0 and value["artifact_count"] > 0
             and value["transfer_event_seq"] > value["source_event_high_water_before"]
             and all(value[key] is expected for key, expected in FLAGS.items()),
             "source_transfer_manifest_invalid", "Source transfer receipt is malformed")
    return dict(value)


def _envelope(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == ENVELOPE_FIELDS
             and type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["rules_version"] == RULES_VERSION
             and isinstance(value["request_digest"], str) and HASH.fullmatch(value["request_digest"])
             and isinstance(value["new_administrator_hash"], str) and HASH.fullmatch(value["new_administrator_hash"])
             and isinstance(value["destination_path"], str)
             and str(_path(value["destination_path"])) == value["destination_path"]
             and Path(value["destination_path"]).name == DATABASE_NAME,
             "source_transfer_manifest_invalid", "Source transfer reservation is malformed")
    _receipt(value["receipt"])
    _require(len(canonical(value).encode()) <= MAX_JSON_BYTES,
             "source_transfer_limit_exceeded", "Transfer reservation exceeds its byte bound")
    return dict(value)


def _read_binding(connection: sqlite3.Connection) -> dict[str, Any] | None:
    headers = connection.execute(
        "SELECT typeof(key),length(CAST(key AS BLOB)),typeof(value),length(CAST(value AS BLOB)) "
        "FROM meta WHERE CAST(key AS TEXT)=? COLLATE NOCASE LIMIT 2", (METAKEY,)).fetchall()
    if not headers:
        return None
    _require(len(headers) == 1 and headers[0][0] == headers[0][2] == "text"
             and headers[0][1] == len(METAKEY) and type(headers[0][3]) is int
             and 0 < headers[0][3] <= MAX_JSON_BYTES,
             "source_transfer_manifest_invalid", "Reserved source transfer row is malformed")
    row = connection.execute("SELECT key,value FROM meta WHERE CAST(key AS TEXT)=? COLLATE NOCASE", (METAKEY,)).fetchone()
    _require(row[0] == METAKEY, "source_transfer_manifest_invalid", "Reserved source transfer key is not canonical")
    value = _envelope(store_quarantine._strict_json(row[1]))
    _require(canonical(value) == row[1], "source_transfer_manifest_invalid", "Source transfer reservation is not canonical")
    receipt = value["receipt"]
    header = connection.execute(
        "SELECT type='source_transferred',typeof(data),length(CAST(data AS BLOB)) FROM events WHERE seq=?",
        (receipt["transfer_event_seq"],)).fetchone()
    _require(header is not None and header[0] == 1 and header[1] == "text"
             and type(header[2]) is int and 0 < header[2] <= MAX_JSON_BYTES,
             "source_transfer_manifest_invalid", "Source transfer event is missing or malformed")
    event = connection.execute("SELECT data FROM events WHERE seq=?", (receipt["transfer_event_seq"],)).fetchone()[0]
    _require(event == canonical(receipt), "source_transfer_manifest_invalid", "Transfer event differs from its receipt")
    return value


def _old_hashes(connection: sqlite3.Connection, deadline: float) -> list[str]:
    from . import transfer_origin
    values = []
    for _, _, row in recovery._rows(connection, "governance_grants", deadline):
        columns, _, _ = recovery._columns(connection, "governance_grants")
        value = row[columns.index("credential_hash")]
        _require(isinstance(value, str) and HASH.fullmatch(value),
                 "source_transfer_source_invalid", "Source credential metadata is malformed")
        values.append(value)
    inherited = transfer_origin.read_origin(connection)
    if inherited is not None:
        values.extend(inherited["old_credential_hashes"])
    return sorted(set(values))


def _dependencies(connection: sqlite3.Connection, deadline: float) -> dict[str, Any]:
    from . import transfer_origin
    records = {}
    total = 0
    inherited_origin = transfer_origin.read_origin(connection)
    for row in connection.execute(
            "SELECT a.id,a.revision,a.digest,r.state,p.id AS proposal_id,p.payload "
            "FROM artifacts a JOIN revisions r ON a.revision=r.revision "
            "LEFT JOIN proposals p ON r.proposal_id=p.id ORDER BY a.id"):
        _deadline(deadline)
        if row["proposal_id"] is None:
            inherited = (transfer_origin.dependency_from_origin(inherited_origin, row["id"], row["revision"], row["digest"])
                         if inherited_origin is not None else None)
            value = inherited if inherited is not None else {
                "proposal_id": None, "baseline_revision": row["revision"],
                "readset": {}, "queries": {}, "write_ids": [row["id"]],
            }
        else:
            baseline, payload = store_quarantine._strict_json(row["state"]), store_quarantine._strict_json(row["payload"])
            value = {"proposal_id": row["proposal_id"], "baseline_revision": row["revision"],
                     "readset": {key: digest(baseline[key]) if key in baseline else None for key in payload["readset"]},
                     "queries": {scope: Store._query_digest(baseline, scope) for scope in payload["queries"]},
                     "write_ids": sorted(body["id"] for body in payload["writes"])}
        total += len(canonical(value).encode()) + len(row["id"].encode())
        _require(total <= MAX_ORIGIN_BYTES // 2, "source_transfer_limit_exceeded", "Imported dependencies exceed their byte bound")
        records[row["id"]] = value
    return records


def _capture(connection: sqlite3.Connection, deadline: float, forbidden: tuple[str, ...]) -> dict[str, Any]:
    state_fingerprint = recovery._fingerprint(connection, deadline, forbidden=forbidden)
    artifacts = Store._artifacts(connection)
    metadata = store_backup._metadata(connection)
    _require(metadata["revision"] > 0 and artifacts, "source_transfer_source_invalid", "Transfer requires accepted source artifacts")
    dependencies = _dependencies(connection, deadline)
    _require(set(dependencies) == set(artifacts), "source_transfer_source_invalid", "Source dependency coverage is incomplete")
    old_hashes = _old_hashes(connection, deadline)
    _require(len(artifacts) <= 10_000 and len(old_hashes) <= 10_000,
             "source_transfer_limit_exceeded", "Imported anchors or retired credentials exceed their count bound")
    anchors = {key: {"digest": digest(body), "dependency": dependencies[key]} for key, body in artifacts.items()}
    _require(len(canonical({"artifact_anchors": anchors, "old_credential_hashes": old_hashes}).encode()) <= MAX_ORIGIN_BYTES,
             "source_transfer_limit_exceeded", "Imported origin exceeds its byte bound")
    sources = [dict(row) for row in connection.execute("SELECT id,text,digest FROM sources ORDER BY id")]
    return {"artifacts": artifacts, "sources": sources, "anchors": anchors, "old_hashes": old_hashes,
            "state_fingerprint": state_fingerprint,
            "metadata": metadata, "governance_policy": governance.current_policy(connection)["policy"],
            "authorship_enabled": authorship.enabled(connection), "content_enabled": content_gate.enabled(connection),
            "content_policy": content_gate.current_policy(connection)["policy"] if content_gate.enabled(connection) else None,
            "continuity_enabled": review_continuity.enabled(connection)}


def _intent(envelope: dict[str, Any]) -> dict[str, Any]:
    receipt = envelope["receipt"]
    value = {"schema_version": 1, "rules_version": RULES_VERSION,
             "transfer_id": receipt["transfer_id"], "workspace_id": receipt["workspace_id"],
             "operation_id": receipt["operation_id"], "destination_path": envelope["destination_path"],
             "request_digest": envelope["request_digest"]}
    _require(len(canonical(value).encode("utf-8")) <= MAX_INTENT_BYTES,
             "source_transfer_limit_exceeded", "Transfer intent exceeds its byte bound")
    return value


def _origin(envelope: dict[str, Any], data: dict[str, Any], path: Path, history: Path,
            source_path: Path, administrator_grant_id: str) -> dict[str, Any]:
    from . import transfer_origin
    receipt = envelope["receipt"]
    value = {"schema_version": 1, "rules_version": RULES_VERSION,
             "transfer_id": receipt["transfer_id"], "workspace_id": receipt["workspace_id"],
             "destination_path": str(path), "source_path": str(source_path), "history_path": str(history),
             "import_revision": receipt["source_revision"], "artifact_anchors": data["anchors"],
             "old_credential_hashes": data["old_hashes"], "request_digest": envelope["request_digest"],
             "new_administrator_grant_id": administrator_grant_id, "ready": True}
    _require(len(canonical(value).encode("utf-8")) <= MAX_ORIGIN_BYTES,
             "source_transfer_limit_exceeded", "Imported origin exceeds its byte bound")
    return transfer_origin.validate_origin(value)


def _read_intent(directory: Path, envelope: dict[str, Any]) -> None:
    store_backup._private_directory(directory)
    value = store_backup._read_json(directory / INTENT_NAME, MAX_INTENT_BYTES)
    _require(set(value) == INTENT_FIELDS and canonical(value) == canonical(_intent(envelope)),
             "source_transfer_destination_exists", "Destination belongs to another transfer or has no matching intent")


def _reserve_directory(directory: Path, envelope: dict[str, Any]) -> bool:
    if os.path.lexists(directory):
        _read_intent(directory, envelope)
        return True
    store_backup._private_directory(directory.parent)
    store_backup._no_symlinks(directory, missing_final=True)
    try:
        directory.mkdir(mode=0o700)
    except FileExistsError:
        _read_intent(directory, envelope)
        return True
    directory.chmod(0o700)
    store_backup._write_json(directory / INTENT_NAME, _intent(envelope), MAX_INTENT_BYTES)
    store_backup._fsync_directory(directory)
    store_backup._fsync_directory(directory.parent)
    return False


def _empty_owned_file(path: Path, deadline: float) -> None:
    if not os.path.lexists(path):
        store_backup._new_database(path)
        return
    with store_backup._regular_file(path):
        pass
    with store_backup._connection(path, deadline, readonly=True) as connection:
        _require(connection.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is None,
                 "source_transfer_destination_exists", "An incomplete transfer contains an unsupported existing database")


def _history_valid(path: Path, envelope: dict[str, Any], deadline: float) -> str:
    with store_backup._regular_file(path):
        pass
    with store_backup._connection(path, deadline, readonly=True, immutable=True) as connection:
        connection.execute("BEGIN")
        source_fence._database(connection, deadline)
        _require(canonical(_read_binding(connection)) == canonical(envelope),
                 "source_transfer_manifest_invalid", "History archive belongs to another transfer")
        fence = source_fence.fence_info(connection)
        _require(fence is not None and fence["fence_id"] == envelope["receipt"]["source_fence_id"]
                 and fence["source_epoch"] == envelope["receipt"]["source_epoch"]
                 and connection.execute("PRAGMA journal_mode").fetchone()[0] == "delete",
                 "source_transfer_source_invalid", "History archive is not a standalone fenced source")
        store_backup._integrity(connection, deadline)
        return recovery._fingerprint(connection, deadline, forbidden=())


def _build_history(source: sqlite3.Connection, path: Path, envelope: dict[str, Any], deadline: float, expected: str) -> None:
    if os.path.lexists(path):
        try:
            _require(_history_valid(path, envelope, deadline) == expected,
                     "source_transfer_conflict", "History archive differs from the current pinned source")
            return
        except WorkbenchError as error:
            if error.code.endswith("limit_exceeded"):
                raise
            _empty_owned_file(path, deadline)
        except sqlite3.Error:
            _empty_owned_file(path, deadline)
    else:
        store_backup._new_database(path)
    with store_backup._connection(path, deadline, readonly=False) as destination:
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        def progress(status: int, remaining: int, total: int) -> None:
            _deadline(deadline)
            _require(total * page_size <= store_backup.MAX_DATABASE_BYTES,
                     "source_transfer_limit_exceeded", "History archive exceeds its byte bound")
        source.backup(destination, pages=store_backup.BACKUP_PAGES, progress=progress, sleep=0.05)
        _require(destination.execute("PRAGMA journal_mode=DELETE").fetchone()[0] == "delete",
                 "source_transfer_source_invalid", "History archive cannot be finalized standalone")
    _require(_history_valid(path, envelope, deadline) == expected,
             "source_transfer_conflict", "Native archive differs from the pinned source")


def _build_active(path: Path, history: Path, source_path: Path, envelope: dict[str, Any], data: dict[str, Any],
                  new_credential: str, deadline: float) -> None:
    from . import transfer_origin
    if os.path.lexists(path):
        with store_backup._regular_file(path):
            pass
        with store_backup._connection(path, deadline, readonly=True) as existing:
            if existing.execute("SELECT 1 FROM sqlite_master LIMIT 1").fetchone() is not None:
                origin = transfer_origin.read_origin(existing)
                _require(origin is not None and origin["request_digest"] == envelope["request_digest"]
                         and origin["workspace_id"] == envelope["receipt"]["workspace_id"]
                         and origin["destination_path"] == str(path),
                         "source_transfer_destination_exists", "Existing active database belongs to another transfer")
                transfer_origin.check_access(existing, path, require_complete=False)
                return
    else:
        store_backup._new_database(path)
    from . import (authorship as author_module, content_gate as content_module, governance as gov_module,
                   review_continuity as continuity_module, review_history_audit, review_history_recovery, store)
    receipt = envelope["receipt"]
    with store_backup._connection(path, deadline, readonly=False) as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            for script in (store.SCHEMA, gov_module.SCHEMA, author_module.SCHEMA, content_module.SCHEMA,
                           continuity_module.SCHEMA, review_history_audit.SCHEMA, review_history_recovery.SCHEMA):
                _execute_schema(connection, script)
            fake = Store.__new__(Store)
            for item in data["sources"]:
                _require(hashlib.sha256(item["text"].encode()).hexdigest() == item["digest"],
                         "source_transfer_source_invalid", "Source evidence digest is inconsistent")
                connection.execute("INSERT INTO sources VALUES (?,?,?)", (item["id"], item["text"], item["digest"]))
            errors = [error for body in data["artifacts"].values() for error in fake._artifact_errors(body, connection)]
            errors.extend(fake._invariant_errors(data["artifacts"]))
            _require(not errors, "source_transfer_source_invalid", "Source artifacts cannot form a valid imported baseline")
            revision = receipt["source_revision"]
            fake._write_artifacts(connection, list(data["artifacts"].values()), revision)
            fake._record_revision(connection, None, revision)
            fake._event(connection, "source_imported", receipt)
            enabled = gov_module.enable(connection, data["governance_policy"], receipt["new_administrator_id"], new_credential)
            generation = enabled["policy_generation"]
            if data["authorship_enabled"]:
                generation = author_module.enable(connection, credential=new_credential,
                                                  expected_policy_generation=generation)["policy_generation"]
            if data["content_enabled"]:
                generation = content_module.enable(connection, data["content_policy"], credential=new_credential,
                                                   expected_policy_generation=generation)["policy_generation"]
            if data["continuity_enabled"]:
                continuity_module.enable(connection, credential=new_credential, expected_policy_generation=generation)
            origin = _origin(envelope, data, path, history, source_path, enabled["administrator"]["grant_id"])
            transfer_origin.persist_origin(connection, origin)
            source_fence._database(connection, deadline)
            recovery._fingerprint(connection, deadline, forbidden=(new_credential, governance._credential_hash(new_credential)))
            store_backup._integrity(connection, deadline)
            _deadline(deadline)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise


def _file_record(path: Path, deadline: float) -> dict[str, Any]:
    size, checksum = store_backup._file_digest(path, deadline, sync=True)
    return {"name": path.name, "bytes": size, "sha256": checksum}


def _manifest_valid(value: Any, directory: Path) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == MANIFEST_FIELDS
             and type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["rules_version"] == RULES_VERSION
             and isinstance(value["transfer_id"], str) and TRANSFER_ID.fullmatch(value["transfer_id"])
             and isinstance(value["workspace_id"], str) and WORKSPACE_ID.fullmatch(value["workspace_id"])
             and value["destination_path"] == str(directory / DATABASE_NAME)
             and isinstance(value["request_digest"], str) and HASH.fullmatch(value["request_digest"]),
             "source_transfer_manifest_invalid", "Transfer completion metadata is malformed")
    receipt = _receipt(value["receipt"])
    _require(value["transfer_id"] == receipt["transfer_id"] and value["workspace_id"] == receipt["workspace_id"],
             "source_transfer_manifest_invalid", "Completion identities differ from their receipt")
    for field, name in (("database", DATABASE_NAME), ("history", HISTORY_NAME)):
        record = value[field]
        _require(isinstance(record, dict) and set(record) == {"name", "bytes", "sha256"}
                 and record["name"] == name and _integer(record["bytes"]) and 0 < record["bytes"] <= store_backup.MAX_DATABASE_BYTES
                 and isinstance(record["sha256"], str) and HASH.fullmatch(record["sha256"]),
                 "source_transfer_manifest_invalid", "Transfer completion file record is malformed")
    return dict(value)


def _completed(directory: Path, deadline: float) -> tuple[dict[str, Any], dict[str, Any]]:
    from . import transfer_origin
    store_backup._private_directory(directory)
    manifest = _manifest_valid(store_backup._read_json(directory / MANIFEST_NAME, MAX_MANIFEST_BYTES), directory)
    envelope = {"schema_version": 1, "rules_version": RULES_VERSION,
                "request_digest": manifest["request_digest"], "destination_path": manifest["destination_path"],
                "new_administrator_hash": "0" * 64, "receipt": manifest["receipt"]}
    _read_intent(directory, envelope)
    database, history = directory / DATABASE_NAME, directory / HISTORY_NAME
    with store_backup._regular_file(database):
        pass
    with store_backup._connection(database, deadline, readonly=True) as active:
        active.execute("BEGIN")
        source_fence._database(active, deadline)
        _require(source_fence.fence_info(active) is None and not store_quarantine.quarantine_present(active),
                 "source_transfer_manifest_invalid", "Imported active baseline has a stop marker")
        transfer_origin.check_access(active, database, require_complete=True)
        origin = transfer_origin.read_origin(active)
        _require(origin is not None and origin["request_digest"] == manifest["request_digest"]
                 and origin["workspace_id"] == manifest["workspace_id"]
                 and origin["transfer_id"] == manifest["transfer_id"],
                 "source_transfer_manifest_invalid", "Imported origin differs from completion metadata")
        store_backup._integrity(active, deadline)
    size, checksum = store_backup._file_digest(history, deadline)
    _require(size == manifest["history"]["bytes"] and checksum == manifest["history"]["sha256"],
             "source_transfer_checksum_mismatch", "History archive differs from its completion metadata")
    with store_backup._connection(history, deadline, readonly=True, immutable=True) as archive:
        archive.execute("BEGIN")
        source_fence._database(archive, deadline)
        binding = _read_binding(archive)
        _require(binding is not None and binding["request_digest"] == manifest["request_digest"]
                 and canonical(binding["receipt"]) == canonical(manifest["receipt"]),
                 "source_transfer_manifest_invalid", "History archive does not contain the completed transfer")
        flags = {"governance_enabled": governance.enabled(archive), "authorship_enabled": authorship.enabled(archive),
                 "content_enabled": content_gate.enabled(archive), "continuity_enabled": review_continuity.enabled(archive)}
    receipt = manifest["receipt"]
    destination = {"workspace_id": receipt["workspace_id"], "revision": receipt["source_revision"],
                   "artifact_count": receipt["artifact_count"], "source_count": receipt["source_count"], **flags}
    return manifest, destination


def _report(manifest: dict[str, Any], destination: dict[str, Any], *, replayed: bool, resumed: bool) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION, "receipt": dict(manifest["receipt"]), "destination": destination,
            "replayed": replayed, "resumed": resumed, **FLAGS}


@contextmanager
def _destination_lock(directory: Path, deadline: float):
    location = directory / LOCK_NAME
    try:
        descriptor = os.open(location, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, 0o600)
        os.fchmod(descriptor, 0o600)
    except FileExistsError:
        descriptor = os.open(location, os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "source_transfer_destination_exists", "Transfer coordination file is not a private regular file")
        while True:
            _deadline(deadline)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.025)
        _deadline(deadline)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def transfer_source(database_path: str | Path, destination: str | Path, *, credential: Any,
                    new_administrator_id: Any, new_credential: Any, operation_id: Any) -> dict[str, Any]:
    """Bind one destination, retain fenced history and import a fresh active baseline."""
    deadline = time.monotonic() + store_backup.DEADLINE_SECONDS
    try:
        store_backup._support()
        database, directory = _path(database_path), _path(destination)
        target = directory / DATABASE_NAME
        _require(all(len(str(path).encode("utf-8")) <= 4096 for path in (target, directory / HISTORY_NAME)),
                 "source_transfer_invalid_input", "Transfer path exceeds its byte bound")
        forbidden = (credential, governance._credential_hash(credential), new_credential, governance._credential_hash(new_credential))
        _require(_identifier(operation_id, forbidden) and _identifier(new_administrator_id, forbidden),
                 "source_transfer_invalid_input", "Transfer identifiers are not safe for public records")
        source_fence._source_path(database)
        store_backup._private_directory(directory.parent)
        store_backup._no_symlinks(directory, missing_final=True)
        existed = os.path.lexists(directory)
        if existed:
            store_backup._private_directory(directory)
            _require(os.path.lexists(directory / INTENT_NAME), "source_transfer_destination_exists",
                     "Existing destination has no transfer intent")
        new_hash = governance._credential_hash(new_credential)
        with store_backup._connection(database, deadline, readonly=False) as source:
            source.execute("BEGIN IMMEDIATE")
            try:
                from . import transfer_origin
                metadata = source_fence._database(source, deadline)
                _require(source.execute("SELECT 1 FROM meta WHERE CAST(key AS TEXT)=? COLLATE NOCASE LIMIT 1",
                                        (transfer_origin.METAKEY,)).fetchone() is None,
                         "source_transfer_source_invalid", "This transfer supports an original source with no imported ancestry")
                recovery._guard_authorization(source, deadline)
                administrator = governance.authorize(source, credential, "administrator", now=time.time())
                fence = source_fence.fence_info(source)
                _require(fence is not None, "source_transfer_source_invalid", "Transfer requires a permanently fenced source")
                _require(administrator["grant_id"] == fence["administrator_grant_id"],
                         "source_transfer_conflict", "The original fence administrator must authorize transfer")
                request = {"source_path": str(database), "destination_path": str(target),
                           "new_administrator_id": new_administrator_id, "new_administrator_hash": new_hash,
                           "operation_id": operation_id, "source_fence_id": fence["fence_id"], "source_epoch": fence["source_epoch"]}
                request_digest = digest(request)
                envelope = _read_binding(source)
                if envelope is not None:
                    _require(envelope["request_digest"] == request_digest and envelope["destination_path"] == str(target)
                             and envelope["new_administrator_hash"] == new_hash,
                             "source_transfer_conflict", "Source is permanently reserved for another exact transfer")
                else:
                    data = _capture(source, deadline, forbidden)
                    _require(new_hash not in data["old_hashes"], "source_transfer_credential_reused",
                             "New administrator credential must differ from all previous authority")
                    seq = source.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchone()
                    next_seq = max(metadata["event_high_water"], seq[0] if seq else 0) + 1
                    _require(next_seq < 2**63, "source_transfer_limit_exceeded", "Source event sequence is exhausted")
                    receipt = {"schema_version": 1, "rules_version": RULES_VERSION,
                               "transfer_id": "sft_" + secrets.token_hex(16), "workspace_id": "wsp_" + secrets.token_hex(16),
                               "operation_id": operation_id, "created_at": time.time(),
                               "source_fence_id": fence["fence_id"], "source_epoch": fence["source_epoch"],
                               "source_revision": metadata["revision"], "source_event_high_water_before": metadata["event_high_water"],
                               "transfer_event_seq": next_seq, "artifact_count": len(data["artifacts"]),
                               "source_count": len(data["sources"]), "new_administrator_id": new_administrator_id, **FLAGS}
                    envelope = {"schema_version": 1, "rules_version": RULES_VERSION, "request_digest": request_digest,
                                "destination_path": str(target), "new_administrator_hash": new_hash, "receipt": receipt}
                    _envelope(envelope)
                    _intent(envelope)
                    _origin(envelope, data, target, directory / HISTORY_NAME, database, "gvg_" + "0" * 32)
                    if existed:
                        _read_intent(directory, envelope)
                    governance.authorize(source, credential, "administrator", now=time.time())
                    source.execute("INSERT INTO meta VALUES (?,?)", (METAKEY, canonical(envelope)))
                    cursor = source.execute("INSERT INTO events(type,data) VALUES (?,?)", ("source_transferred", canonical(receipt)))
                    _require(cursor.lastrowid == next_seq, "source_transfer_source_invalid", "Transfer event sequence differs from receipt")
                    _, tables = store_backup._expected_schema(False)
                    recovery._row_bounds(source, tables, deadline)
                _deadline(deadline)
                source.commit()
            except BaseException:
                source.rollback()
                raise
        resumed = _reserve_directory(directory, envelope)
        with _destination_lock(directory, deadline):
            if os.path.lexists(directory / MANIFEST_NAME):
                manifest, facts = _completed(directory, deadline)
                _require(manifest["request_digest"] == envelope["request_digest"],
                         "source_transfer_conflict", "Completed destination belongs to another transfer")
                return _report(manifest, facts, replayed=True, resumed=False)
            with store_backup._connection(database, deadline, readonly=True) as source:
                source.execute("BEGIN")
                source_fence._database(source, deadline)
                recovery._guard_authorization(source, deadline)
                governance.authorize(source, credential, "administrator", now=time.time())
                _require(canonical(_read_binding(source)) == canonical(envelope),
                         "source_transfer_conflict", "Source reservation changed during transfer")
                data = _capture(source, deadline, forbidden)
                _require(new_hash not in data["old_hashes"], "source_transfer_credential_reused", "Historical credential reuse is forbidden")
                history = directory / HISTORY_NAME
                _build_history(source, history, envelope, deadline, data["state_fingerprint"])
                _build_active(target, history, database, envelope, data, new_credential, deadline)
                governance.authorize(source, credential, "administrator", now=time.time())
            manifest = {"schema_version": 1, "rules_version": RULES_VERSION,
                        "transfer_id": envelope["receipt"]["transfer_id"], "workspace_id": envelope["receipt"]["workspace_id"],
                        "destination_path": str(target), "request_digest": envelope["request_digest"],
                        "database": _file_record(target, deadline), "history": _file_record(directory / HISTORY_NAME, deadline),
                        "receipt": envelope["receipt"]}
            _manifest_valid(manifest, directory)
            _deadline(deadline)
            store_backup._write_json(directory / MANIFEST_NAME, manifest, MAX_MANIFEST_BYTES)
            store_backup._fsync_directory(directory)
            completed, facts = _completed(directory, deadline)
            return _report(completed, facts, replayed=False, resumed=resumed)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("source_transfer_io_failed", "Source transfer could not complete") from error


def transfer_status(destination: str | Path) -> dict[str, Any]:
    """Return immutable import facts without comparing mutable active DB bytes."""
    deadline = time.monotonic() + store_backup.DEADLINE_SECONDS
    try:
        store_backup._support()
        manifest, facts = _completed(_path(destination), deadline)
        return _report(manifest, facts, replayed=True, resumed=False)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("source_transfer_io_failed", "Transfer status could not be observed") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith(("source_transfer_", "governance_", "store_transfer_")):
        return error
    if error.code in {"snapshot_limit_exceeded", "recovery_limit_exceeded", "source_fence_limit_exceeded"}:
        return WorkbenchError("source_transfer_limit_exceeded", "Source transfer exceeded a supported bound")
    if error.code == "snapshot_incomplete":
        return WorkbenchError("source_transfer_incomplete", "Transfer destination is incomplete")
    return WorkbenchError("source_transfer_source_invalid", "Source or destination cannot be validated")
