"""Record bounded blocked restores and explicit observational job associations.

Case and association receipts are immutable historical observations. This module
never repairs an original job, creates a retry job, infers its source, or claims
request equivalence. Current evidence and attempt status are separate readings.
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

from . import store_backup, workspace_restore_job as jobs
from .store import WorkbenchError, canonical, digest


RULES_VERSION = "workspace-restore-case-1"
CASE_NAME = ".agentwiki-restore-case.json"
LOCK_NAME = ".agentwiki-restore-case.lock"
ATTEMPT_NAME = ".agentwiki-restore-case-attempt.json"
MAX_CASE_BYTES = 32 * 1024
MAX_ATTEMPT_BYTES = 64 * 1024
DEADLINE_SECONDS = jobs.DEADLINE_SECONDS
CASE_ID = re.compile(r"wrc_[0-9a-f]{32}\Z")
LINK_ID = re.compile(r"wrl_[0-9a-f]{32}\Z")
CAUSE_CODES = {"plan_invalid": "workspace_restore_job_manifest_invalid",
               "ownership_unproven": "workspace_restore_job_conflict",
               "owner_invalid": "workspace_restore_job_manifest_invalid"}
CASE_FIELDS = {"schema_version", "rules_version", "case_id", "operation_id", "created_at", "job_path", "case_path",
               "parent", "case_directory", "cause", "original_error_code", "original_identity", "original_target_path",
               "evidence", "request_digest"}
ATTEMPT_FIELDS = {"schema_version", "rules_version", "case_id", "link_id", "operation_id", "created_at", "job_path",
                  "target_path", "case_request_digest", "plan_digest", "attempt_request_digest", "pins", "status_at_link", "request_digest"}
STATIC_FIELDS = {"device", "inode", "uid", "mode"}
FULL_FIELDS = STATIC_FIELDS | {"nlink", "size", "mtime_ns", "ctime_ns"}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Restore case observation exceeded its time bound")


def _path(value: Any) -> Path:
    return jobs._path(value)


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and math.isfinite(value) and 0 < value < 2**63


def _static(details: os.stat_result) -> dict[str, int]:
    return {"device": details.st_dev, "inode": details.st_ino, "uid": details.st_uid, "mode": stat.S_IMODE(details.st_mode)}


def _full(details: os.stat_result) -> dict[str, int]:
    return {**_static(details), "nlink": details.st_nlink, "size": details.st_size,
            "mtime_ns": details.st_mtime_ns, "ctime_ns": details.st_ctime_ns}


def _directory(path: Path, *, full: bool = False) -> dict[str, int]:
    store_backup._private_directory(path)
    details = path.lstat()
    return _full(details) if full else _static(details)


def _bytes(path: Path, maximum: int, deadline: float) -> tuple[bytes, dict[str, Any]]:
    before = jobs._header(path, maximum)
    with store_backup._regular_file(path, maximum=maximum) as descriptor:
        opened = os.fstat(descriptor)
        _require(_full(before) == _full(opened), "evidence_changed", "An observed control file changed before reading")
        raw = bytearray()
        while True:
            _deadline(deadline)
            block = os.read(descriptor, min(store_backup.COPY_BLOCK_BYTES, maximum + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            _require(len(raw) <= maximum, "limit_exceeded", "Observed control bytes exceed their supported bound")
        after = os.fstat(descriptor)
        _require(_full(opened) == _full(after) == _full(path.lstat()) and len(raw) == opened.st_size,
                 "evidence_changed", "An observed control file changed during reading")
    return bytes(raw), {**_full(before), "sha256": hashlib.sha256(raw).hexdigest()}


def _names(path: Path, maximum: int) -> set[str]:
    return jobs.workspace_backup._names(path, maximum)


def _pin_valid(pin: Any, *, full: bool = False, file: bool = False) -> bool:
    fields = FULL_FIELDS if full else STATIC_FIELDS
    if not isinstance(pin, dict) or set(pin) != fields | ({"sha256"} if file else set()):
        return False
    if not all(type(pin[key]) is int and 0 <= pin[key] < 2**128 for key in fields):
        return False
    return (pin["uid"] == os.geteuid() and pin["mode"] == (0o600 if file else 0o700)
            and (not file or (pin["nlink"] == 1 and isinstance(pin["sha256"], str) and jobs.HASH.fullmatch(pin["sha256"]))))


def _identity_valid(identity: Any) -> bool:
    return (isinstance(identity, dict) and set(identity) == {"job_id", "operation_id", "created_at", "workspace_id"}
            and isinstance(identity["job_id"], str) and bool(jobs.JOB_ID.fullmatch(identity["job_id"]))
            and jobs._operation(identity["operation_id"]) and _timestamp(identity["created_at"])
            and isinstance(identity["workspace_id"], str) and bool(re.fullmatch(r"wsp_[0-9a-f]{32}", identity["workspace_id"])))


def _identity(plan: dict[str, Any]) -> dict[str, Any]:
    return {"job_id": plan["job_id"], "operation_id": plan["operation_id"], "created_at": plan["created_at"],
            "workspace_id": plan["restore_manifest"]["workspace_id"]}


def _descriptor(body: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in body.items() if key != "request_digest"}


def _tree(job: Path, entries: set[str], deadline: float) -> dict[str, Any]:
    limits = {jobs.PLAN_NAME: jobs.MAX_PLAN_BYTES, jobs.OWNER_NAME: jobs.MAX_OWNER_BYTES,
              jobs.LOCK_NAME: 0, store_backup.INTENT_NAME: store_backup.MAX_INTENT_BYTES}
    _require(_names(job, len(entries)) == entries, "evidence_changed", "Observed control entries changed")
    return {"directory": _directory(job, full=True),
            "entries": {name: _bytes(job / name, limits[name], deadline)[1] for name in sorted(entries)}}


def _capture(job: Path, case: Path, deadline: float) -> dict[str, Any]:
    """Capture only the three bounded early layouts, with the old job locked."""
    names = _names(job, 3)
    _require(names in ({jobs.PLAN_NAME, jobs.LOCK_NAME}, {jobs.PLAN_NAME, jobs.LOCK_NAME, jobs.OWNER_NAME}),
             "unsupported_state", "Restore case capture supports only bounded early control layouts")
    _bytes(job / jobs.PLAN_NAME, jobs.MAX_PLAN_BYTES, deadline)
    try:
        plan = jobs._read_plan(job)
    except WorkbenchError as error:
        _require(jobs._translate(error).code == "workspace_restore_job_manifest_invalid"
                 and names == {jobs.PLAN_NAME, jobs.LOCK_NAME},
                 "unsupported_state", "Restore case does not identify a supported invalid plan")
        return {"cause": "plan_invalid", "original_error_code": CAUSE_CODES["plan_invalid"],
                "original_identity": None, "original_target_path": None,
                "evidence": {"job": _tree(job, names, deadline), "target": None}}
    target = Path(plan["destination_path"])
    _require(target.parent == case.parent and target not in {job, case},
             "invalid_input", "Case and original destination must be separate private siblings")
    _directory(target)
    _require(_names(target, 1) == {store_backup.INTENT_NAME},
             "unsupported_state", "Original destination is outside the supported guarded layout")
    jobs._verify_file(plan, 0, deadline)
    if jobs.OWNER_NAME not in names:
        cause = "ownership_unproven"
    else:
        _bytes(job / jobs.OWNER_NAME, jobs.MAX_OWNER_BYTES, deadline)
        try:
            store_backup._read_json(job / jobs.OWNER_NAME, jobs.MAX_OWNER_BYTES)
        except WorkbenchError as error:
            _require(jobs._translate(error).code == "workspace_restore_job_manifest_invalid",
                     "unsupported_state", "Original ownership record is outside the supported invalid format")
        else:
            raise WorkbenchError("workspace_restore_case_unsupported_state", "Canonical ownership records are not captured as malformed evidence")
        cause = "owner_invalid"
    return {"cause": cause, "original_error_code": CAUSE_CODES[cause], "original_identity": _identity(plan),
            "original_target_path": str(target),
            "evidence": {"job": _tree(job, names, deadline), "target": _tree(target, {store_backup.INTENT_NAME}, deadline)}}


@contextmanager
def _lock(case: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    _directory(case)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(case / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_incomplete", "Restore case has no existing coordination file") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Restore case lock must be a private empty regular owner file")
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


def _entries(case: Path) -> None:
    names = _names(case, 3)
    _require({CASE_NAME, LOCK_NAME} <= names <= {CASE_NAME, LOCK_NAME, ATTEMPT_NAME},
             "record_invalid", "Restore case contains unsupported or incomplete control entries")


def _tree_valid(tree: Any, entries: set[str]) -> bool:
    limits = {jobs.PLAN_NAME: jobs.MAX_PLAN_BYTES, jobs.OWNER_NAME: jobs.MAX_OWNER_BYTES,
              jobs.LOCK_NAME: 0, store_backup.INTENT_NAME: store_backup.MAX_INTENT_BYTES}
    return (isinstance(tree, dict) and set(tree) == {"directory", "entries"}
            and _pin_valid(tree["directory"], full=True) and isinstance(tree["entries"], dict)
            and set(tree["entries"]) == entries
            and all(_pin_valid(pin, full=True, file=True) and pin["size"] <= limits[name]
                    for name, pin in tree["entries"].items()))


def _read_case(case: Path) -> dict[str, Any]:
    _entries(case)
    body = store_backup._read_json(case / CASE_NAME, MAX_CASE_BYTES)
    _require(set(body) == CASE_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["case_id"], str) and CASE_ID.fullmatch(body["case_id"])
             and jobs._operation(body["operation_id"]) and _timestamp(body["created_at"])
             and body["case_path"] == str(case) and isinstance(body["cause"], str) and body["cause"] in CAUSE_CODES
             and body["original_error_code"] == CAUSE_CODES[body["cause"]]
             and _pin_valid(body["parent"]) and _pin_valid(body["case_directory"])
             and isinstance(body["request_digest"], str) and jobs.HASH.fullmatch(body["request_digest"])
             and digest(_descriptor(body)) == body["request_digest"],
             "record_invalid", "Restore case record is malformed or has different immutable bindings")
    job = _path(body["job_path"])
    _require(str(job) == body["job_path"] and job.parent == case.parent and job != case,
             "record_invalid", "Restore case original path binding is malformed")
    evidence = body["evidence"]
    _require(isinstance(evidence, dict) and set(evidence) == {"job", "target"}, "record_invalid", "Restore case evidence is malformed")
    entries = {jobs.PLAN_NAME, jobs.LOCK_NAME} | ({jobs.OWNER_NAME} if body["cause"] == "owner_invalid" else set())
    _require(_tree_valid(evidence["job"], entries), "record_invalid", "Restore case job evidence is malformed")
    if body["cause"] == "plan_invalid":
        _require(body["original_identity"] is None and body["original_target_path"] is None and evidence["target"] is None,
                 "record_invalid", "Invalid original plans must not reconstruct identity or destination")
    else:
        target = _path(body["original_target_path"])
        _require(str(target) == body["original_target_path"] and target.parent == case.parent and target not in {job, case}
                 and _identity_valid(body["original_identity"]) and _tree_valid(evidence["target"], {store_backup.INTENT_NAME}),
                 "record_invalid", "Restore case original guarded destination binding is malformed")
    _require(_directory(case.parent) == body["parent"] and _directory(case) == body["case_directory"],
             "record_invalid", "Restore case control directories differ from their fixed identities")
    return body


def _public_case(body: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION, **{key: body[key] for key in ("case_id", "operation_id", "created_at", "cause", "original_error_code", "original_identity")},
            "inspection_only": True, "domain_writes_allowed": False, "original_files_modified": False,
            "original_identity_reconstructed": False, "old_authority_activated": False, "external_fencing_established": False}


def _public_link(body: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION, **{key: body[key] for key in ("case_id", "link_id", "operation_id", "created_at")},
            "attempt_status": body["status_at_link"], "operator_association": True, "fresh_creation_verified": False,
            "request_equivalence_verified": False, "original_files_modified": False, "inspection_only": True,
            "domain_writes_allowed": False, "old_authority_activated": False, "external_fencing_established": False}


def _status_valid(value: Any) -> dict[str, Any]:
    # Reuse the established safe 15/18-field validator without opening a Store.
    from .workspace_restore_job_cli import _public_status

    return _public_status(value)


def _attempt_pins(job: Path, target: Path, deadline: float) -> dict[str, Any]:
    return {"job_directory": _directory(job), "target_directory": _directory(target),
            "plan": _bytes(job / jobs.PLAN_NAME, jobs.MAX_PLAN_BYTES, deadline)[1],
            "owner": _bytes(job / jobs.OWNER_NAME, jobs.MAX_OWNER_BYTES, deadline)[1],
            "lock": _bytes(job / jobs.LOCK_NAME, 0, deadline)[1]}


def _read_link(case: Path, original: dict[str, Any]) -> dict[str, Any] | None:
    if not os.path.lexists(case / ATTEMPT_NAME):
        return None
    body = store_backup._read_json(case / ATTEMPT_NAME, MAX_ATTEMPT_BYTES)
    _require(set(body) == ATTEMPT_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and body["case_id"] == original["case_id"]
             and body["case_request_digest"] == original["request_digest"]
             and isinstance(body["link_id"], str) and LINK_ID.fullmatch(body["link_id"])
             and jobs._operation(body["operation_id"]) and _timestamp(body["created_at"])
             and all(isinstance(body[key], str) and jobs.HASH.fullmatch(body[key]) for key in ("plan_digest", "attempt_request_digest", "request_digest"))
             and digest(_descriptor(body)) == body["request_digest"], "record_invalid", "Restore case association record is malformed")
    job, target = _path(body["job_path"]), _path(body["target_path"])
    separated = {case, Path(original["job_path"])}
    if original["original_target_path"] is not None:
        separated.add(Path(original["original_target_path"]))
    _require(str(job) == body["job_path"] and str(target) == body["target_path"] and job != target
             and job.parent == target.parent == case.parent and not ({job, target} & separated),
             "record_invalid", "Restore case association paths are not separate private siblings")
    pins = body["pins"]
    _require(isinstance(pins, dict) and set(pins) == {"job_directory", "target_directory", "plan", "owner", "lock"}
             and all(_pin_valid(pins[key]) for key in ("job_directory", "target_directory"))
             and all(_pin_valid(pins[key], full=True, file=True) for key in ("plan", "owner", "lock")),
             "record_invalid", "Restore case association control pins are malformed")
    body["status_at_link"] = _status_valid(body["status_at_link"])
    return body


def _evidence_locked(body: dict[str, Any], deadline: float) -> bool:
    job = Path(body["job_path"])
    expected = body["evidence"]
    try:
        current = {"job": _tree(job, set(expected["job"]["entries"]), deadline), "target": None}
        if body["original_target_path"] is not None:
            current["target"] = _tree(Path(body["original_target_path"]), {store_backup.INTENT_NAME}, deadline)
        return canonical(current) == canonical(expected)
    except WorkbenchError as error:
        if error.code in {"workspace_restore_case_evidence_changed", "workspace_backup_limit_exceeded"}:
            return False
        raise


def _observe_evidence(body: dict[str, Any], deadline: float) -> dict[str, Any]:
    try:
        with jobs._lock(Path(body["job_path"]), deadline):
            unchanged = _evidence_locked(body, deadline)
        return {"state": "unchanged" if unchanged else "changed",
                "error_code": None if unchanged else "workspace_restore_case_evidence_changed"}
    except (WorkbenchError, OSError, ValueError, TypeError, KeyError):
        return {"state": "unavailable", "error_code": "workspace_restore_case_evidence_unavailable"}


def _observe_attempt(body: dict[str, Any] | None, deadline: float) -> dict[str, Any]:
    if body is None:
        return {"state": "not_linked", "status": None, "error_code": None}
    try:
        job, target = Path(body["job_path"]), Path(body["target_path"])
        with jobs._lock(job, deadline):
            if _attempt_pins(job, target, deadline) != body["pins"]:
                return {"state": "changed", "status": None, "error_code": "workspace_restore_case_attempt_changed"}
            plan = jobs._read_plan(job)
            if (digest(plan) != body["plan_digest"] or plan["request_digest"] != body["attempt_request_digest"]
                    or plan["destination_path"] != str(target)):
                return {"state": "changed", "status": None, "error_code": "workspace_restore_case_attempt_changed"}
            _require(jobs._ownership(plan), "attempt_changed", "Linked job no longer has its recorded target ownership")
            status = _status_valid(jobs._status(plan, deadline))
            for key in ("job_id", "operation_id", "created_at", "workspace_id", "total_file_count"):
                _require(status[key] == body["status_at_link"][key], "attempt_changed", "Linked job identity changed")
            return {"state": "available", "status": status, "error_code": None}
    except WorkbenchError as error:
        changed = error.code == "workspace_restore_case_attempt_changed"
        return {"state": "changed" if changed else "unavailable", "status": None,
                "error_code": "workspace_restore_case_attempt_changed" if changed else "workspace_restore_case_attempt_unavailable"}
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError):
        return {"state": "unavailable", "status": None, "error_code": "workspace_restore_case_attempt_unavailable"}


def _replay_case(case: Path, job: Path, operation_id: str, deadline: float) -> dict[str, Any]:
    with _lock(case, deadline):
        body = _read_case(case)
        _require(body["job_path"] == str(job) and body["operation_id"] == operation_id,
                 "conflict", "Restore case is bound to another historical observation")
        return _public_case(body)


def record_blocked_case(job_directory: str | Path, case_directory: str | Path, *, operation_id: Any) -> dict[str, Any]:
    """Record one bounded blocked observation; exact replay is historical."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        job, case = _path(job_directory), _path(case_directory)
        _require(jobs._operation(operation_id) and job != case and job.parent == case.parent,
                 "invalid_input", "Restore case and original job must be distinct private siblings")
        _directory(case.parent)
        if os.path.lexists(case):
            return _replay_case(case, job, operation_id, deadline)
        with jobs._lock(job, deadline):
            preliminary = _capture(job, case, deadline)
        try:
            case.mkdir(mode=0o700)
        except FileExistsError:
            # No old-job lock is held while waiting for another case creator.
            return _replay_case(case, job, operation_id, deadline)
        case.chmod(0o700)
        with _lock(case, deadline, create=True):
            with jobs._lock(job, deadline):
                captured = _capture(job, case, deadline)
                _require(canonical(captured) == canonical(preliminary), "evidence_changed", "Original controls changed during case reservation")
                body = {"schema_version": 1, "rules_version": RULES_VERSION, "case_id": "wrc_" + secrets.token_hex(16),
                        "operation_id": operation_id, "created_at": time.time(), "job_path": str(job), "case_path": str(case),
                        "parent": _directory(case.parent), "case_directory": _directory(case), **captured}
                body["request_digest"] = digest(body)
                store_backup._write_json(case / CASE_NAME, body, MAX_CASE_BYTES)
                store_backup._fsync_directory(case)
                store_backup._fsync_directory(case.parent)
                return _public_case(body)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_io_failed", "Blocked restore observation could not be recorded") from error


def inspect_case(case_directory: str | Path) -> dict[str, Any]:
    """Return immutable receipts separately from advisory current observations."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        case = _path(case_directory)
        with _lock(case, deadline):
            body = _read_case(case)
            attempt = _read_link(case, body)
            return {"rules_version": RULES_VERSION, "case": _public_case(body),
                    "link": _public_link(attempt) if attempt is not None else None,
                    "original_evidence": _observe_evidence(body, deadline), "current_attempt": _observe_attempt(attempt, deadline)}
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_io_failed", "Restore case could not be observed") from error


def link_attempt(case_directory: str | Path, job_directory: str | Path, *, operation_id: Any) -> dict[str, Any]:
    """Associate an already prepared job, without claiming its creation or equivalence."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        case, job = _path(case_directory), _path(job_directory)
        _require(jobs._operation(operation_id), "invalid_input", "Restore association operation ID is unsupported")
        with _lock(case, deadline):
            original = _read_case(case)
            existing = _read_link(case, original)
            if existing is not None:
                _require(existing["job_path"] == str(job) and existing["operation_id"] == operation_id,
                         "conflict", "Restore case already has another immutable operator association")
                return _public_link(existing)
            separated = {case, Path(original["job_path"])}
            if original["original_target_path"] is not None:
                separated.add(Path(original["original_target_path"]))
            _require(job.parent == case.parent and job not in separated,
                     "invalid_input", "Associated job must be a separate private sibling")
            with jobs._lock(Path(original["job_path"]), deadline):
                _require(_evidence_locked(original, deadline), "evidence_changed", "Original evidence differs from its recorded observation")
            with jobs._lock(job, deadline):
                plan = jobs._read_plan(job)
                target = Path(plan["destination_path"])
                _require(target.parent == case.parent and target not in separated | {job},
                         "invalid_input", "Associated destination must be a separate private sibling")
                _require(jobs._ownership(plan), "unsupported_state", "Associated job needs its own proven target ownership")
                status = _status_valid(jobs._status(plan, deadline))
                body = {"schema_version": 1, "rules_version": RULES_VERSION, "case_id": original["case_id"],
                        "link_id": "wrl_" + secrets.token_hex(16), "operation_id": operation_id, "created_at": time.time(),
                        "job_path": str(job), "target_path": str(target), "case_request_digest": original["request_digest"],
                        "plan_digest": digest(plan), "attempt_request_digest": plan["request_digest"],
                        "pins": _attempt_pins(job, target, deadline), "status_at_link": status}
                body["request_digest"] = digest(body)
            # These are sequential observations, not a cross-job transaction.
            # Never hold two job locks, including for unsupported link inputs.
            with jobs._lock(Path(original["job_path"]), deadline):
                _require(_evidence_locked(original, deadline), "evidence_changed", "Original evidence changed before association recording")
                store_backup._write_json(case / ATTEMPT_NAME, body, MAX_ATTEMPT_BYTES)
                store_backup._fsync_directory(case)
                return _public_link(body)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_io_failed", "Restore operator association could not be recorded") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_"):
        return error
    if error.code.endswith("limit_exceeded"):
        return WorkbenchError("workspace_restore_case_limit_exceeded", "Restore case exceeded a supported observation bound")
    if error.code.endswith("private_path_required"):
        return WorkbenchError("workspace_restore_case_private_path_required", "Restore case requires private owner controls")
    if error.code.endswith("invalid_input"):
        return WorkbenchError("workspace_restore_case_invalid_input", "Restore case input is unsupported")
    if error.code.endswith("incomplete"):
        return WorkbenchError("workspace_restore_case_incomplete", "A restore case control is unavailable")
    return WorkbenchError("workspace_restore_case_record_invalid", "Restore case or referenced control cannot be validated")
