"""Resume one private, inspection-only full-workspace restore by whole files.

An immutable external plan binds the source manifest and one destination. The
finished bundle retains the existing six-entry format. Only known staging cache
inside the job directory can be rebuilt; published destination files are never
overwritten. Source paths are unnecessary for observing or finalizing exact
already copied bytes.
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
from typing import Any, Iterator

from . import store_backup, store_quarantine, workspace_backup
from .store import WorkbenchError, canonical, digest


RULES_VERSION = "workspace-restore-job-1"
PLAN_NAME = ".agentwiki-restore-job-plan.json"
LOCK_NAME = ".agentwiki-restore-job.lock"
OWNER_NAME = ".agentwiki-restore-job-owner.json"
MAX_PLAN_BYTES = 256 * 1024
MAX_OWNER_BYTES = 8 * 1024
DEADLINE_SECONDS = store_backup.DEADLINE_SECONDS
MAX_STEP_FILES = 16
JOB_ID = re.compile(r"wrj_[0-9a-f]{32}\Z")
OPERATION_ID = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
HASH = re.compile(r"[0-9a-f]{64}\Z")
PLAN_FIELDS = {
    "schema_version", "rules_version", "job_id", "operation_id", "created_at",
    "source_path", "destination_path", "job_path", "source_manifest",
    "restore_manifest", "request_digest",
}
PUBLIC_FIELDS = {
    "rules_version", "job_id", "operation_id", "created_at", "workspace_id",
    "state", "copied_file_count", "total_file_count", "completed",
    "inspection_only", "domain_writes_allowed", "full_history_preserved",
    "old_authority_activated", "external_fencing_established", "result",
}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_job_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Restore job exceeded its time bound")


def _path(value: Any) -> Path:
    path = store_backup._path(value)
    _require(len(str(path).encode("utf-8")) <= 4096,
             "invalid_input", "Restore job path exceeds its byte bound")
    return path


def _operation(value: Any) -> bool:
    return (isinstance(value, str) and OPERATION_ID.fullmatch(value) is not None
            and not BEARER.search(value) and not HASH.fullmatch(value))


def _derived_manifest(original: dict[str, Any], created_at: float) -> dict[str, Any]:
    return {**original, "kind": "restore", "created_at": created_at,
            "snapshot": {**original["snapshot"], "kind": "restore", "created_at": created_at}}


def _descriptor(plan: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in plan.items() if key != "request_digest"}


def _json_record(name: str, body: dict[str, Any]) -> dict[str, Any]:
    encoded = canonical(body).encode("utf-8")
    return {"name": name, "bytes": len(encoded), "sha256": hashlib.sha256(encoded).hexdigest()}


def _items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    body = plan["restore_manifest"]
    snapshot = body["snapshot"]
    standard_intent = {key: snapshot[key] for key in store_backup.INTENT_FIELDS}
    outer_intent = {key: body[key] for key in workspace_backup.INTENT_FIELDS}
    result = []
    for name, value in ((store_backup.INTENT_NAME, standard_intent),
                        (workspace_backup.INTENT_NAME, outer_intent),
                        (store_backup.MANIFEST_NAME, snapshot)):
        result.append({"relative": name, "record": _json_record(name, value), "body": value, "data": False})
    result.append({"relative": store_backup.DATABASE_NAME, "record": snapshot["database"], "data": True})
    for record in body["archives"]:
        relative = workspace_backup.ARCHIVES_NAME + "/" + record["name"]
        result.append({"relative": relative, "record": record, "data": True})
    result.append({"relative": workspace_backup.MANIFEST_NAME,
                   "record": _json_record(workspace_backup.MANIFEST_NAME, body), "body": body, "data": False})
    return result


def _stage(plan: dict[str, Any], index: int) -> Path:
    return Path(plan["job_path"]) / f".restore-{plan['job_id']}-{index:02d}.part"


def _plan_valid(body: Any) -> dict[str, Any]:
    _require(isinstance(body, dict) and set(body) == PLAN_FIELDS
             and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION
             and isinstance(body["job_id"], str) and JOB_ID.fullmatch(body["job_id"])
             and _operation(body["operation_id"])
             and type(body["created_at"]) in {int, float} and math.isfinite(body["created_at"])
             and 0 < body["created_at"] < 2**63
             and isinstance(body["request_digest"], str) and HASH.fullmatch(body["request_digest"]),
             "manifest_invalid", "Restore job plan is malformed")
    source, destination, job = (_path(body[key]) for key in ("source_path", "destination_path", "job_path"))
    _require(all(str(path) == body[key] for path, key in zip(
        (source, destination, job), ("source_path", "destination_path", "job_path")))
        and destination != job and destination.parent == job.parent
        and all(left != right and left not in right.parents and right not in left.parents
                for left, right in ((source, destination), (source, job))),
        "manifest_invalid", "Restore job paths do not identify separate sibling destinations")
    original = workspace_backup._manifest_valid(body["source_manifest"])
    restored = workspace_backup._manifest_valid(body["restore_manifest"])
    _require(canonical(restored) == canonical(_derived_manifest(original, body["created_at"]))
             and digest(_descriptor(body)) == body["request_digest"],
             "manifest_invalid", "Restore job does not bind its original fixed restore manifest")
    _require(len(canonical(body).encode("utf-8")) <= MAX_PLAN_BYTES,
             "limit_exceeded", "Restore job plan exceeds its byte bound")
    paths = [job / PLAN_NAME, job / LOCK_NAME, job / OWNER_NAME, destination / workspace_backup.ARCHIVES_NAME]
    for index, item in enumerate(_items(body)):
        paths.extend((destination / item["relative"], _stage(body, index)))
    _require(all(len(str(path).encode("utf-8")) <= 4096 for path in paths),
             "invalid_input", "A generated restore job path exceeds its byte bound")
    return store_quarantine._strict_json(canonical(body))


def _read_plan(job: Path) -> dict[str, Any]:
    plan = _plan_valid(store_backup._read_json(job / PLAN_NAME, MAX_PLAN_BYTES))
    _require(plan["job_path"] == str(job), "conflict", "Restore plan is bound to another job directory")
    return plan


def _ownership(plan: dict[str, Any]) -> bool:
    """Require append-once proof of this job's exclusive destination mkdir."""
    owner_path = Path(plan["job_path"]) / OWNER_NAME
    destination = Path(plan["destination_path"])
    owns = os.path.lexists(owner_path)
    exists = os.path.lexists(destination)
    _require(owns == exists, "conflict", "Restore destination has no matching durable ownership record")
    if not owns:
        return False
    owner = store_backup._read_json(owner_path, MAX_OWNER_BYTES)
    store_backup._private_directory(destination)
    details = destination.lstat()
    expected = {"schema_version": 1, "rules_version": RULES_VERSION, "job_id": plan["job_id"],
                "request_digest": plan["request_digest"], "destination_path": plan["destination_path"],
                "device": details.st_dev, "inode": details.st_ino}
    _require(canonical(owner) == canonical(expected),
             "conflict", "Restore destination is not the directory created by this exact job")
    return True


@contextmanager
def _lock(job: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    store_backup._private_directory(job)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(job / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_job_incomplete", "Restore job has no coordination file") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600
                 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Restore job lock is not a private empty owner file")
        while True:
            _deadline(deadline)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.025)
        yield
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _header(path: Path, maximum: int, *, links: tuple[int, ...] = (1,)) -> os.stat_result:
    store_backup._no_symlinks(path.parent)
    details = path.lstat()
    _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
             and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink in links
             and 0 <= details.st_size <= maximum,
             "private_path_required", "Restore file is not a supported private owner file")
    return details


def _same_pair(plan: dict[str, Any], index: int) -> bool:
    item = _items(plan)[index]
    target = Path(plan["destination_path"]) / item["relative"]
    stage = _stage(plan, index)
    if not os.path.lexists(target) or not os.path.lexists(stage):
        return False
    left = _header(target, item["record"]["bytes"], links=(2,))
    right = _header(stage, item["record"]["bytes"], links=(2,))
    return left.st_dev == right.st_dev and left.st_ino == right.st_ino


def _verify_file(plan: dict[str, Any], index: int, deadline: float) -> None:
    item = _items(plan)[index]
    path = Path(plan["destination_path"]) / item["relative"]
    expected = item["record"]
    before = _header(path, expected["bytes"], links=(1, 2))
    if before.st_nlink == 2:
        _require(_same_pair(plan, index), "conflict", "Restore file has an unrecognized hard-link alias")
    _require(before.st_size == expected["bytes"], "checksum_mismatch", "A published restore file has another size")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        _require((opened.st_dev, opened.st_ino) == (before.st_dev, before.st_ino),
                 "conflict", "Restore file changed before inspection")
        hasher, size = hashlib.sha256(), 0
        while True:
            _deadline(deadline)
            block = os.read(descriptor, store_backup.COPY_BLOCK_BYTES)
            if not block:
                break
            size += len(block)
            _require(size <= expected["bytes"], "checksum_mismatch", "Restore file exceeds its expected size")
            hasher.update(block)
        after = os.fstat(descriptor)
        _require(size == expected["bytes"] and hasher.hexdigest() == expected["sha256"]
                 and (opened.st_size, opened.st_mtime_ns) == (after.st_size, after.st_mtime_ns),
                 "checksum_mismatch", "A published restore file differs from its immutable plan")
    finally:
        os.close(descriptor)


def _job_entries(plan: dict[str, Any], deadline: float) -> None:
    job = Path(plan["job_path"])
    expected = {PLAN_NAME, LOCK_NAME, OWNER_NAME} | {_stage(plan, index).name for index in range(len(_items(plan)))}
    names = workspace_backup._names(job, len(expected))
    _require({PLAN_NAME, LOCK_NAME} <= names and names <= expected,
             "manifest_invalid", "Restore job contains unsupported entries")
    for index, item in enumerate(_items(plan)):
        stage = _stage(plan, index)
        if not os.path.lexists(stage):
            continue
        details = _header(stage, item["record"]["bytes"], links=(1, 2))
        if details.st_nlink == 2:
            _require(_same_pair(plan, index), "conflict", "Restore staging has an unrecognized hard-link alias")
            _verify_file(plan, index, deadline)


def _target_state(plan: dict[str, Any], deadline: float) -> tuple[int, bool]:
    destination = Path(plan["destination_path"])
    if not _ownership(plan):
        return 0, False
    store_backup._private_directory(destination)
    items = _items(plan)
    expected = {item["relative"] for item in items if "/" not in item["relative"]} | {workspace_backup.ARCHIVES_NAME}
    names = workspace_backup._names(destination, len(expected))
    _require(names <= expected and store_backup.INTENT_NAME in names,
             "destination_exists", "Existing restore destination has no exact ownership intent")
    archive_directory = destination / workspace_backup.ARCHIVES_NAME
    if workspace_backup.ARCHIVES_NAME in names:
        store_backup._private_directory(archive_directory)
        expected_archives = {record["name"] for record in plan["restore_manifest"]["archives"]}
        _require(workspace_backup._names(archive_directory, len(expected_archives)) <= expected_archives,
                 "manifest_invalid", "Restore archive directory contains unsupported entries")
    copied, aliases = 0, False
    for index, item in enumerate(items):
        target = destination / item["relative"]
        if not os.path.lexists(target):
            continue
        _verify_file(plan, index, deadline)
        copied += int(item["data"])
        aliases = aliases or target.lstat().st_nlink == 2
    complete = workspace_backup.MANIFEST_NAME in names
    if complete:
        _require(copied == sum(item["data"] for item in items),
                 "incomplete", "Restore completion has missing data files")
        if not aliases:
            actual = workspace_backup._load_bundle(destination, deadline)
            _require(canonical(actual) == canonical(plan["restore_manifest"]),
                     "conflict", "Completed restore differs from its immutable job")
            return copied, True
    return copied, False


def _status(plan: dict[str, Any], deadline: float) -> dict[str, Any]:
    _ownership(plan)
    _job_entries(plan, deadline)
    copied, completed = _target_state(plan, deadline)
    total = 1 + len(plan["restore_manifest"]["archives"])
    state = "completed" if completed else "ready" if copied == total else "copying" if copied else "prepared"
    return {"rules_version": RULES_VERSION, "job_id": plan["job_id"], "operation_id": plan["operation_id"],
            "created_at": plan["created_at"], "workspace_id": plan["restore_manifest"]["workspace_id"],
            "state": state, "copied_file_count": copied, "total_file_count": total, "completed": completed,
            "inspection_only": True, "domain_writes_allowed": False, "full_history_preserved": completed,
            "old_authority_activated": False, "external_fencing_established": False,
            "result": workspace_backup._summary(plan["restore_manifest"]) if completed else None}


def _repair_staging(plan: dict[str, Any], deadline: float) -> None:
    _job_entries(plan, deadline)
    for index, item in enumerate(_items(plan)):
        stage = _stage(plan, index)
        if not os.path.lexists(stage):
            continue
        details = _header(stage, item["record"]["bytes"], links=(1, 2))
        if details.st_nlink == 2:
            _require(_same_pair(plan, index), "conflict", "Restore staging does not identify its published file")
            _verify_file(plan, index, deadline)
            store_backup._fsync_directory((Path(plan["destination_path"]) / item["relative"]).parent)
        stage.unlink()
        store_backup._fsync_directory(Path(plan["job_path"]))


def _install(plan: dict[str, Any], index: int, deadline: float) -> None:
    item = _items(plan)[index]
    destination = Path(plan["destination_path"])
    target = destination / item["relative"]
    if os.path.lexists(target):
        _verify_file(plan, index, deadline)
        return
    stage = _stage(plan, index)
    _require(not os.path.lexists(stage), "conflict", "Restore staging cache was not reconciled")
    descriptor = os.open(stage, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb", closefd=False) as output:
            if item["data"]:
                source = Path(plan["source_path"]) / item["relative"]
                with store_backup._regular_file(source) as source_descriptor:
                    hasher, size = hashlib.sha256(), 0
                    while True:
                        _deadline(deadline)
                        block = os.read(source_descriptor, store_backup.COPY_BLOCK_BYTES)
                        if not block:
                            break
                        size += len(block)
                        _require(size <= item["record"]["bytes"], "source_changed", "Restore source exceeds its pinned size")
                        hasher.update(block)
                        output.write(block)
                    _require(size == item["record"]["bytes"] and hasher.hexdigest() == item["record"]["sha256"],
                             "source_changed", "Restore source differs from its pinned bytes")
            else:
                output.write(canonical(item["body"]).encode("utf-8"))
            output.flush()
            os.fsync(output.fileno())
    finally:
        os.close(descriptor)
    _deadline(deadline)
    os.link(stage, target, follow_symlinks=False)
    store_backup._fsync_directory(target.parent)
    stage.unlink()
    store_backup._fsync_directory(Path(plan["job_path"]))
    _verify_file(plan, index, deadline)


def _ensure_target(plan: dict[str, Any], deadline: float) -> None:
    destination = Path(plan["destination_path"])
    created = not _ownership(plan)
    if created:
        store_backup._private_directory(destination.parent)
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError:
            raise WorkbenchError("workspace_restore_job_conflict", "Another operation reserved the restore destination") from None
        destination.chmod(0o700)
        store_backup._fsync_directory(destination.parent)
        # Guard the path through the established Store quarantine protocol
        # before persisting the separate proof of this exclusive mkdir.
        _install(plan, 0, deadline)
        details = destination.lstat()
        owner = {"schema_version": 1, "rules_version": RULES_VERSION, "job_id": plan["job_id"],
                 "request_digest": plan["request_digest"], "destination_path": plan["destination_path"],
                 "device": details.st_dev, "inode": details.st_ino}
        store_backup._write_json(Path(plan["job_path"]) / OWNER_NAME, owner, MAX_OWNER_BYTES)
        store_backup._fsync_directory(Path(plan["job_path"]))
    else:
        _target_state(plan, deadline)
    # The ordinary snapshot intent is always the first destination file.
    _install(plan, 0, deadline)
    _install(plan, 1, deadline)
    archives = destination / workspace_backup.ARCHIVES_NAME
    if not os.path.lexists(archives):
        archives.mkdir(mode=0o700)
        archives.chmod(0o700)
    store_backup._private_directory(archives)
    _install(plan, 2, deadline)
    store_backup._fsync_directory(destination)


def _source_matches(plan: dict[str, Any], deadline: float) -> None:
    actual = workspace_backup._load_bundle(Path(plan["source_path"]), deadline)
    _require(canonical(actual) == canonical(plan["source_manifest"]),
             "source_changed", "Restore source manifest differs from its immutable job")


def prepare_restore(bundle_path: str | Path, destination: str | Path, job_directory: str | Path,
                    *, operation_id: Any) -> dict[str, Any]:
    """Bind one historical restore; exact repeated input returns its fixed job."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        source, target, job = (_path(value) for value in (bundle_path, destination, job_directory))
        _require(_operation(operation_id), "invalid_input", "Restore operation ID is unsupported")
        _require(target != job and target.parent == job.parent
                 and all(left != right and left not in right.parents and right not in left.parents
                         for left, right in ((source, target), (source, job))),
                 "invalid_input", "Restore job and destination must be separate private siblings")
        store_backup._private_directory(job.parent)
        if os.path.lexists(job):
            with _lock(job, deadline):
                plan = _read_plan(job)
                _require(plan["source_path"] == str(source) and plan["destination_path"] == str(target)
                         and plan["operation_id"] == operation_id,
                         "conflict", "Restore job is permanently bound to another request")
                return _status(plan, deadline)
        _require(not os.path.lexists(target), "destination_exists", "Restore destination already exists")
        original = workspace_backup._load_bundle(source, deadline)
        created_at = time.time()
        plan = {"schema_version": 1, "rules_version": RULES_VERSION,
                "job_id": "wrj_" + secrets.token_hex(16), "operation_id": operation_id, "created_at": created_at,
                "source_path": str(source), "destination_path": str(target), "job_path": str(job),
                "source_manifest": original, "restore_manifest": _derived_manifest(original, created_at)}
        plan["request_digest"] = digest(plan)
        plan = _plan_valid(plan)
        store_backup._no_symlinks(job, missing_final=True)
        try:
            job.mkdir(mode=0o700)
        except FileExistsError:
            # A concurrent prepare must finish its own plan before it can be
            # recognized; this call never adopts an unowned directory.
            with _lock(job, deadline):
                existing = _read_plan(job)
                _require(existing["source_path"] == str(source) and existing["destination_path"] == str(target)
                         and existing["operation_id"] == operation_id,
                         "conflict", "Restore job is bound to another request")
                return _status(existing, deadline)
        job.chmod(0o700)
        with _lock(job, deadline, create=True):
            store_backup._write_json(job / PLAN_NAME, plan, MAX_PLAN_BYTES)
            store_backup._fsync_directory(job)
            store_backup._fsync_directory(job.parent)
            _ensure_target(plan, deadline)
            return _status(plan, deadline)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_job_io_failed", "Restore job could not be prepared") from error


def step_restore(job_directory: str | Path, *, limit: Any = 1) -> dict[str, Any]:
    """Copy a bounded number of whole files and safely finish exact ready bytes."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(type(limit) is int and 1 <= limit <= MAX_STEP_FILES,
                 "invalid_input", "Restore step file limit is unsupported")
        job = _path(job_directory)
        with _lock(job, deadline):
            plan = _read_plan(job)
            status = _status(plan, deadline)
            if not status["completed"] and status["copied_file_count"] < status["total_file_count"]:
                _source_matches(plan, deadline)
            _repair_staging(plan, deadline)
            _ensure_target(plan, deadline)
            count = 0
            items = _items(plan)
            for index, item in enumerate(items):
                if not item["data"] or os.path.lexists(Path(plan["destination_path"]) / item["relative"]):
                    continue
                if count >= limit:
                    break
                _install(plan, index, deadline)
                count += 1
            status = _status(plan, deadline)
            if status["copied_file_count"] == status["total_file_count"] and not status["completed"]:
                destination = Path(plan["destination_path"])
                workspace_backup._inspect_contents(destination, plan["restore_manifest"], deadline, complete=False)
                _install(plan, len(items) - 1, deadline)
                store_backup._fsync_directory(destination)
                status = _status(plan, deadline)
            return status
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_job_io_failed", "Restore step could not complete") from error


def restore_status(job_directory: str | Path) -> dict[str, Any]:
    """Observe planned destination bytes without changing files or opening source."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        job = _path(job_directory)
        with _lock(job, deadline):
            return _status(_read_plan(job), deadline)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_job_io_failed", "Restore status could not be observed") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_job_"):
        return error
    if error.code.endswith("limit_exceeded"):
        return WorkbenchError("workspace_restore_job_limit_exceeded", "Restore job exceeded a supported bound")
    if "checksum_mismatch" in error.code:
        return WorkbenchError("workspace_restore_job_checksum_mismatch", "Restore bytes differ from their private pins")
    if error.code in {"snapshot_incomplete", "workspace_backup_incomplete", "store_snapshot_incomplete"}:
        return WorkbenchError("workspace_restore_job_incomplete", "Restore job or referenced bundle is incomplete")
    if error.code.endswith("private_path_required"):
        return WorkbenchError("workspace_restore_job_private_path_required", "Restore job requires private owner-only files")
    if error.code.endswith("invalid_input"):
        return WorkbenchError("workspace_restore_job_invalid_input", "Restore job input is unsupported")
    return WorkbenchError("workspace_restore_job_manifest_invalid", "Restore plan or bundle cannot be validated")
