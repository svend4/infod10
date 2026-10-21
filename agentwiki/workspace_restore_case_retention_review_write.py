"""Prepare and advance an owned file write for an immutable advisory review.

A complete private intent captures the exact historical review record and the
inode of an exclusively allocated staging file. Subsequent status and steps use
only this job and its reserved review directory. They neither reread the plan
nor turn the saved actor/verdict into authorization. Incomplete intent records
and unproved file identities remain unsupported and are never repaired.
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
from . import workspace_restore_case_retention_review as reviews
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_cli import _public_review
from .workspace_restore_case_retention_review_write_cli import _public_status


RULES_VERSION = "workspace-restore-case-retention-review-write-1"
INTENT_NAME = ".agentwiki-restore-case-retention-review-write.json"
LOCK_NAME = ".agentwiki-restore-case-retention-review-write.lock"
STAGE_NAME = ".agentwiki-restore-case-retention-review-write.part"
MAX_INTENT_BYTES = 16 * 1024 * 1024
DEADLINE_SECONDS = reviews.DEADLINE_SECONDS
WRITE_ID = re.compile(r"wrrw_[0-9a-f]{32}\Z")
INTENT_FIELDS = {
    "schema_version", "rules_version", "write_id", "operation_id", "created_at",
    "job_path", "parent", "job_directory", "review_path", "review_lock",
    "review_record", "stage_pin", "request_digest",
}
FLAGS = dict(reviews.FLAGS)
cases = reviews.cases


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_review_write_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Review write exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _file_pin_valid(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == cases.STATIC_FIELDS
            and all(type(value[key]) is int and 0 <= value[key] < 2**128 for key in cases.STATIC_FIELDS)
            and value["uid"] == os.geteuid() and value["mode"] == 0o600)


def _inputs(plan: Path, review: Path, job: Path, operation_id: Any, actor: Any, verdict: Any) -> None:
    reviews._inputs(plan, review, operation_id, actor, verdict)
    _require(job.parent == review.parent and job not in {plan, review}, "invalid_input",
             "Plan, review and write job need separate private sibling paths")
    generated = (job / INTENT_NAME, job / LOCK_NAME, job / STAGE_NAME,
                 review / reviews.REVIEW_NAME, review / reviews.LOCK_NAME)
    _require(all(len(str(path).encode("utf-8")) <= 4096 for path in generated),
             "invalid_input", "A generated review write path exceeds its supported bound")


def _source_separate(captured: dict[str, Any], job: Path) -> None:
    source = captured["plan_source"]
    _require(str(job) != source["catalog_path"]
             and all(member["case_path"] != str(job) for member in source["members"]),
             "invalid_input", "Write job must remain separate from all captured source paths")


@contextmanager
def _lock(job: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(job)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(job / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_retention_review_write_incomplete",
                             "Write job has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Write coordination needs a private empty regular owner file")
        if create:
            os.fsync(descriptor)
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


def _record_valid(record: Any, review: Path, parent: dict[str, int], deadline: float) -> dict[str, Any]:
    """Validate the captured old schema without reading its old source paths."""
    _require(isinstance(record, dict) and set(record) == reviews.REVIEW_FIELDS
             and type(record["schema_version"]) is int and record["schema_version"] == 1
             and record["rules_version"] == reviews.RULES_VERSION
             and isinstance(record["review_id"], str) and reviews.REVIEW_ID.fullmatch(record["review_id"])
             and cases.jobs._operation(record["operation_id"]) and cases._timestamp(record["created_at"])
             and record["review_path"] == str(review) and _same(record["parent"], parent)
             and cases._pin_valid(record["parent"]) and cases._pin_valid(record["review_directory"])
             and cases.jobs._operation(record["actor"]) and isinstance(record["verdict"], str)
             and record["verdict"] in reviews.VERDICTS
             and isinstance(record["request_digest"], str) and cases.jobs.HASH.fullmatch(record["request_digest"])
             and digest({key: value for key, value in record.items() if key != "request_digest"}) == record["request_digest"],
             "record_invalid", "Write intent has an unsupported fixed review record")
    reviews._captured_valid(record, review, deadline)
    _require(len(canonical(record).encode("utf-8")) <= reviews.MAX_REVIEW_BYTES,
             "limit_exceeded", "Intended historical review exceeds its byte bound")
    result = _public_review(reviews._public(record))
    _deadline(deadline)
    return result


def _intent_valid(body: Any, job: Path, deadline: float) -> dict[str, Any]:
    _require(isinstance(body, dict) and set(body) == INTENT_FIELDS
             and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["write_id"], str)
             and WRITE_ID.fullmatch(body["write_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["job_path"] == str(job)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["job_directory"])
             and _file_pin_valid(body["stage_pin"])
             and cases._pin_valid(body["review_lock"], full=True, file=True)
             and body["review_lock"]["size"] == 0
             and body["review_lock"]["sha256"] == hashlib.sha256(b"").hexdigest()
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Write intent has unsupported immutable fields")
    review = cases._path(body["review_path"])
    _require(str(review) == body["review_path"] and review.parent == job.parent and review != job,
             "record_invalid", "Write intent has unsupported sibling bindings")
    intended = _record_valid(body["review_record"], review, body["parent"], deadline)
    record = body["review_record"]
    plan = cases._path(record["plan_anchor"]["plan_path"])
    _inputs(plan, review, job, body["operation_id"], record["actor"], record["verdict"])
    _source_separate({"plan_source": record["plan_source"]}, job)
    _require(record["operation_id"] == body["operation_id"] and _same(record["created_at"], body["created_at"])
             and _same(cases._directory(job.parent), body["parent"])
             and _same(cases._directory(job), body["job_directory"]),
             "record_invalid", "Write intent differs from its fixed operation or directory bindings")
    _deadline(deadline)
    return intended


def _read_intent(job: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    names = cases._names(job, 3)
    _require({INTENT_NAME, LOCK_NAME} <= names, "incomplete", "Write intent is unavailable")
    _require(names <= {INTENT_NAME, LOCK_NAME, STAGE_NAME}, "record_invalid", "Write job has unsupported entries")
    body = store_backup._read_json(job / INTENT_NAME, MAX_INTENT_BYTES)
    _intent_valid(body, job, deadline)
    raw, _ = cases._bytes(job / INTENT_NAME, MAX_INTENT_BYTES, deadline)
    _require(raw == canonical(body).encode("utf-8"), "record_invalid", "Write intent is not its canonical immutable record")
    _deadline(deadline)
    return body


def _owned_header(path: Path, body: dict[str, Any], maximum: int, links: tuple[int, ...]) -> os.stat_result:
    details = path.lstat()
    _require(stat.S_ISREG(details.st_mode) and _same(cases._static(details), body["stage_pin"])
             and details.st_nlink in links and 0 <= details.st_size <= maximum,
             "conflict", "Write file does not identify the reserved private staging inode")
    return details


def _owned_bytes(path: Path, body: dict[str, Any], expected: bytes, deadline: float,
                 *, links: tuple[int, ...] = (1,)) -> tuple[bytes, os.stat_result]:
    before = _owned_header(path, body, len(expected), links)
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        opened = os.fstat(descriptor)
        _require(cases._full(opened) == cases._full(before), "conflict", "Reserved write inode changed before reading")
        raw = bytearray()
        while True:
            _deadline(deadline)
            block = os.read(descriptor, min(store_backup.COPY_BLOCK_BYTES, len(expected) + 1 - len(raw)))
            if not block:
                break
            raw.extend(block)
            _require(len(raw) <= len(expected), "conflict", "Reserved write bytes exceed the intended record")
        after = os.fstat(descriptor)
        _require(cases._full(opened) == cases._full(after) == cases._full(path.lstat())
                 and len(raw) == opened.st_size and expected.startswith(raw),
                 "conflict", "Reserved write bytes do not match the exact intended prefix")
        _deadline(deadline)
        return bytes(raw), after
    finally:
        os.close(descriptor)


def _status(body: dict[str, Any], deadline: float) -> dict[str, Any]:
    """Observe the reserved two directories while their locks are held."""
    job, review = Path(body["job_path"]), Path(body["review_path"])
    _require(_same(cases._directory(review), body["review_record"]["review_directory"]),
             "conflict", "Reserved review directory differs from its captured identity")
    _, lock_pin = cases._bytes(review / reviews.LOCK_NAME, 0, deadline)
    _require(_same(lock_pin, body["review_lock"]), "conflict", "Reserved review coordination control changed")
    job_names, review_names = cases._names(job, 3), cases._names(review, 2)
    _require({INTENT_NAME, LOCK_NAME} <= job_names <= {INTENT_NAME, LOCK_NAME, STAGE_NAME}
             and {reviews.LOCK_NAME} <= review_names <= {reviews.LOCK_NAME, reviews.REVIEW_NAME},
             "conflict", "Reserved write directories contain unsupported entries")
    stage, target = job / STAGE_NAME, review / reviews.REVIEW_NAME
    has_stage, has_target = STAGE_NAME in job_names, reviews.REVIEW_NAME in review_names
    expected = canonical(body["review_record"]).encode("utf-8")
    if has_stage:
        stage_bytes, stage_details = _owned_bytes(stage, body, expected, deadline, links=(1, 2))
        if has_target:
            target_bytes, target_details = _owned_bytes(target, body, expected, deadline, links=(2,))
            _require(stage_details.st_nlink == target_details.st_nlink == 2
                     and stage_bytes == target_bytes == expected,
                     "conflict", "Linked write does not identify exactly the intended two-path inode")
            state = "linked"
        else:
            _require(stage_details.st_nlink == 1, "conflict", "Reserved staging has an unrecognized alias")
            state = "ready" if stage_bytes == expected else "staging" if stage_bytes else "prepared"
    else:
        _require(has_target, "conflict", "Write has neither its reserved staging nor completed review")
        target_bytes, _ = _owned_bytes(target, body, expected, deadline)
        _require(target_bytes == expected, "conflict", "Completed review differs from its exact intended bytes")
        _require(_same(reviews._read_review(review, deadline), body["review_record"]),
                 "conflict", "Completed review differs from its captured historical record")
        state = "completed"
    intended = _public_review(reviews._public(body["review_record"]))
    result = _public_status({"rules_version": RULES_VERSION,
                             **{key: body[key] for key in ("write_id", "operation_id", "created_at")},
                             "state": state, "completed": state == "completed", "intended_review": intended,
                             "result": intended if state == "completed" else None, **FLAGS})
    _deadline(deadline)
    return result


def _locked_status(body: dict[str, Any], deadline: float) -> dict[str, Any]:
    with reviews._lock(Path(body["review_path"]), deadline):
        return _status(body, deadline)


def _replay(plan: Path, review: Path, job: Path, operation_id: str, actor: str,
            verdict: str, deadline: float) -> dict[str, Any]:
    with _lock(job, deadline):
        body = _read_intent(job, deadline)
        record = body["review_record"]
        _require(body["review_path"] == str(review) and record["plan_anchor"]["plan_path"] == str(plan)
                 and body["operation_id"] == operation_id and record["actor"] == actor and record["verdict"] == verdict,
                 "conflict", "Write job is already bound to another exact request")
        return _locked_status(body, deadline)


def prepare_review_write(plan_directory: str | Path, review_directory: str | Path,
                         job_directory: str | Path, *, operation_id: Any, actor: Any,
                         verdict: Any) -> dict[str, Any]:
    """Capture a fixed historical review and reserve its owned empty staging inode."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        plan, review, job = (cases._path(path) for path in (plan_directory, review_directory, job_directory))
        _inputs(plan, review, job, operation_id, actor, verdict)
        cases._directory(job.parent)
        if os.path.lexists(job):
            return _replay(plan, review, job, operation_id, actor, verdict, deadline)
        preliminary = reviews._capture_plan(plan, review, deadline)
        _source_separate(preliminary, job)
        _require(not os.path.lexists(review), "conflict", "Review write requires a new unoccupied review directory")
        try:
            job.mkdir(mode=0o700)
        except FileExistsError:
            return _replay(plan, review, job, operation_id, actor, verdict, deadline)
        job.chmod(0o700)
        with _lock(job, deadline, create=True):
            try:
                review.mkdir(mode=0o700)
            except FileExistsError:
                raise WorkbenchError("workspace_restore_case_retention_review_write_conflict",
                                     "Review destination was occupied during reservation") from None
            review.chmod(0o700)
            with reviews._lock(review, deadline, create=True):
                captured = reviews._capture_plan(plan, review, deadline)
                _source_separate(captured, job)
                _require(_same(captured, preliminary), "source_changed", "Plan changed during review write reservation")
                created_at = time.time()
                _require(cases._timestamp(created_at), "io_failed", "Review write clock is unsupported")
                record = {"schema_version": 1, "rules_version": reviews.RULES_VERSION,
                          "review_id": "wrrv_" + secrets.token_hex(16), "operation_id": operation_id,
                          "created_at": created_at, "review_path": str(review),
                          "parent": cases._directory(job.parent), "review_directory": cases._directory(review),
                          "actor": actor, "verdict": verdict, **captured}
                record["request_digest"] = digest(record)
                _record_valid(record, review, record["parent"], deadline)
                descriptor = os.open(job / STAGE_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
                try:
                    os.fchmod(descriptor, 0o600)
                    os.fsync(descriptor)
                    stage_pin = cases._static(os.fstat(descriptor))
                finally:
                    os.close(descriptor)
                _, review_lock = cases._bytes(review / reviews.LOCK_NAME, 0, deadline)
                body = {"schema_version": 1, "rules_version": RULES_VERSION,
                        "write_id": "wrrw_" + secrets.token_hex(16), "operation_id": operation_id,
                        "created_at": created_at, "job_path": str(job), "parent": record["parent"],
                        "job_directory": cases._directory(job), "review_path": str(review),
                        "review_lock": review_lock, "review_record": record, "stage_pin": stage_pin}
                body["request_digest"] = digest(body)
                _intent_valid(body, job, deadline)
                try:
                    obligations._shape(body)
                except WorkbenchError as error:
                    raise WorkbenchError("workspace_restore_case_retention_review_write_limit_exceeded",
                                         "Write intent JSON structure exceeds its supported bound") from error
                _require(len(canonical(body).encode("utf-8")) <= MAX_INTENT_BYTES,
                         "limit_exceeded", "Write intent exceeds its byte bound")
                _deadline(deadline)
                store_backup._write_json(job / INTENT_NAME, body, MAX_INTENT_BYTES)
                store_backup._fsync_directory(job)
                store_backup._fsync_directory(review)
                store_backup._fsync_directory(job.parent)
                result = _status(body, deadline)
                _deadline(deadline)
                return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_io_failed", "Review write could not be prepared") from error


def inspect_review_write(job_directory: str | Path) -> dict[str, Any]:
    """Observe the own intent and reserved files without writing or reading sources."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        job = cases._path(job_directory)
        with _lock(job, deadline):
            result = _locked_status(_read_intent(job, deadline), deadline)
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_io_failed", "Review write could not be observed") from error


def _fill_stage(body: dict[str, Any], deadline: float) -> None:
    stage = Path(body["job_path"]) / STAGE_NAME
    expected = canonical(body["review_record"]).encode("utf-8")
    prefix, before = _owned_bytes(stage, body, expected, deadline)
    descriptor = os.open(stage, os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        _require(cases._full(os.fstat(descriptor)) == cases._full(before),
                 "conflict", "Owned staging changed before continuation")
        offset = os.lseek(descriptor, len(prefix), os.SEEK_SET)
        _require(offset == len(prefix), "io_failed", "Owned staging could not retain its exact prefix")
        while offset < len(expected):
            _deadline(deadline)
            written = os.write(descriptor, expected[offset:offset + store_backup.COPY_BLOCK_BYTES])
            _require(written > 0, "io_failed", "Owned staging write did not advance")
            offset += written
        os.fsync(descriptor)
        _require(_same(cases._static(os.fstat(descriptor)), body["stage_pin"]),
                 "conflict", "Owned staging identity changed during continuation")
    finally:
        os.close(descriptor)
    raw, _ = _owned_bytes(stage, body, expected, deadline)
    _require(raw == expected, "conflict", "Owned staging is not the complete intended review")
    store_backup._fsync_directory(Path(body["job_path"]))
    _deadline(deadline)


def _sync_owned(path: Path, body: dict[str, Any], deadline: float, *, links: tuple[int, ...]) -> None:
    """Sync only a descriptor proved to be this intent's complete reserved inode."""
    expected = canonical(body["review_record"]).encode("utf-8")
    raw, before = _owned_bytes(path, body, expected, deadline, links=links)
    _require(raw == expected, "conflict", "Owned review is not complete before synchronization")
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
    try:
        _require(cases._full(os.fstat(descriptor)) == cases._full(before),
                 "conflict", "Owned review changed before synchronization")
        _deadline(deadline)
        os.fsync(descriptor)
        _require(cases._full(os.fstat(descriptor)) == cases._full(before) == cases._full(path.lstat()),
                 "conflict", "Owned review changed during synchronization")
    finally:
        os.close(descriptor)
    _deadline(deadline)


def step_review_write(job_directory: str | Path) -> dict[str, Any]:
    """Advance one phase: fill, exclusive link, or remove its proved staging alias."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        job = cases._path(job_directory)
        with _lock(job, deadline):
            body = _read_intent(job, deadline)
            review = Path(body["review_path"])
            with reviews._lock(review, deadline):
                status = _status(body, deadline)
                if status["state"] in {"prepared", "staging"}:
                    _fill_stage(body, deadline)
                elif status["state"] == "ready":
                    _sync_owned(job / STAGE_NAME, body, deadline, links=(1,))
                    _deadline(deadline)
                    try:
                        os.link(job / STAGE_NAME, review / reviews.REVIEW_NAME, follow_symlinks=False)
                    except FileExistsError:
                        raise WorkbenchError("workspace_restore_case_retention_review_write_conflict",
                                             "Review publication refuses an occupied target") from None
                    store_backup._fsync_directory(review)
                    store_backup._fsync_directory(job)
                elif status["state"] == "linked":
                    _sync_owned(review / reviews.REVIEW_NAME, body, deadline, links=(2,))
                    _deadline(deadline)
                    store_backup._fsync_directory(review)
                    _require(_status(body, deadline)["state"] == "linked", "conflict",
                             "Write alias changed before its proved staging removal")
                    (job / STAGE_NAME).unlink()
                    store_backup._fsync_directory(job)
                    _sync_owned(review / reviews.REVIEW_NAME, body, deadline, links=(1,))
                    store_backup._fsync_directory(review)
                result = _status(body, deadline)
                _deadline(deadline)
                return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_io_failed", "Review write phase could not complete") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_review_write_"):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict"):
        if error.code.endswith(suffix):
            return WorkbenchError("workspace_restore_case_retention_review_write_" + suffix,
                                  "Review write exceeded a bound or has unsupported private bindings")
    return WorkbenchError("workspace_restore_case_retention_review_write_record_invalid",
                          "Review write intent or captured historical record cannot be validated")
