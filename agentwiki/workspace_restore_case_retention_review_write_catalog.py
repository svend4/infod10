"""Explicit immutable write-job membership and bounded current-status pages.

Creation captures only validated immutable intent metadata. Historical show and
exact create replay read this catalog's own controls. A page releases the
catalog lock before observing each pinned job and its reserved review through
the previous write protocol. It neither advances jobs nor revalidates sources.
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
from . import workspace_restore_case_retention_review_write as writes
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_write_catalog_cli import _public_catalog, _public_page, _public_write


RULES_VERSION = "workspace-restore-case-retention-review-write-catalog-1"
CATALOG_NAME = ".agentwiki-restore-case-retention-review-write-catalog.json"
LOCK_NAME = ".agentwiki-restore-case-retention-review-write-catalog.lock"
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_PAGE_BYTES = 25 * 1024 * 1024
MAX_WRITES = 256
MAX_PAGE_SIZE = 16
DEADLINE_SECONDS = writes.DEADLINE_SECONDS
CATALOG_ID = re.compile(r"wrrwcat_[0-9a-f]{32}\Z")
CATALOG_FIELDS = {
    "schema_version", "rules_version", "catalog_id", "operation_id", "created_at", "catalog_path",
    "parent", "catalog_directory", "write_count", "members", "request_digest",
}
MEMBER_FIELDS = {"index", "job_path", "write", "job_directory", "job_record", "job_lock"}
FLAGS = {**writes.FLAGS, "membership_frozen": True, "order_frozen": True}
cases = writes.cases
_OBSERVATION_ERRORS = (WorkbenchError, OSError, sqlite3.Error, ValueError, TypeError,
                       KeyError, OverflowError, UnicodeError, RecursionError)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Write catalog exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _input_jobs(catalog: Path, values: Any) -> list[Path]:
    _require(type(values) in {list, tuple} and 1 <= len(values) <= MAX_WRITES,
             "invalid_input", "Write catalog needs a bounded explicit ordered job list")
    paths = [cases._path(value) for value in values]
    _require(len(set(paths)) == len(paths)
             and all(path != catalog and path.parent == catalog.parent for path in paths),
             "invalid_input", "Catalog and unique selected write jobs need separate private sibling paths")
    return paths


@contextmanager
def _lock(catalog: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(catalog)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(catalog / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_incomplete",
                             "Write catalog has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Catalog coordination needs a private empty regular owner file")
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


def _write(body: dict[str, Any]) -> dict[str, Any]:
    record = body["review_record"]
    return _public_write({"rules_version": writes.RULES_VERSION,
                          **{key: body[key] for key in ("write_id", "operation_id", "created_at")},
                          **{key: record[key] for key in ("review_id", "actor", "verdict")}})


def _separate(catalog: Path, body: dict[str, Any]) -> None:
    """Keep catalog output away from all known captured source/target paths."""
    record = body["review_record"]
    source = record["plan_source"]
    excluded = {body["job_path"], body["review_path"], record["plan_anchor"]["plan_path"], source["catalog_path"]}
    excluded.update(member["case_path"] for member in source["members"])
    _require(str(catalog) not in excluded, "invalid_input", "Write catalog must be separate from captured job bindings")


def _job_pins(job: Path, deadline: float) -> tuple[bytes, dict[str, Any]]:
    raw, record = cases._bytes(job / writes.INTENT_NAME, writes.MAX_INTENT_BYTES, deadline)
    _, lock = cases._bytes(job / writes.LOCK_NAME, 0, deadline)
    return raw, {"job_directory": cases._directory(job), "job_record": record, "job_lock": lock}


def _capture_member(catalog: Path, job: Path, index: int, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    with writes._lock(job, deadline):
        body = writes._read_intent(job, deadline)
        _separate(catalog, body)
        raw, pins = _job_pins(job, deadline)
        _require(canonical(body).encode("utf-8") == raw and _same(body["job_directory"], pins["job_directory"]),
                 "entry_changed", "Write intent changed while its catalog membership was captured")
        member = {"index": index, "job_path": str(job), "write": _write(body), **pins}
    _deadline(deadline)
    return member


def _capture_members(catalog: Path, paths: list[Path], deadline: float) -> list[dict[str, Any]]:
    members = [_capture_member(catalog, job, index, deadline) for index, job in enumerate(paths, 1)]
    _require(len({member["write"]["write_id"] for member in members}) == len(members)
             and len({member["write"]["review_id"] for member in members}) == len(members),
             "invalid_input", "Selected write jobs need unique write and intended review identifiers")
    return members


def _catalog(body: dict[str, Any]) -> dict[str, Any]:
    return _public_catalog({"rules_version": RULES_VERSION,
                            **{key: body[key] for key in ("catalog_id", "operation_id", "created_at", "write_count")},
                            **FLAGS})


def _read_catalog(catalog: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    _require(cases._names(catalog, 2) == {CATALOG_NAME, LOCK_NAME},
             "record_invalid", "Write catalog has unsupported or incomplete controls")
    body = store_backup._read_json(catalog / CATALOG_NAME, MAX_CATALOG_BYTES)
    _require(set(body) == CATALOG_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["catalog_id"], str)
             and CATALOG_ID.fullmatch(body["catalog_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["catalog_path"] == str(catalog)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["catalog_directory"])
             and type(body["write_count"]) is int and 1 <= body["write_count"] <= MAX_WRITES
             and isinstance(body["members"], list) and len(body["members"]) == body["write_count"]
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Write catalog has unsupported immutable fields")
    paths, write_ids, review_ids = set(), set(), set()
    for index, member in enumerate(body["members"], 1):
        _deadline(deadline)
        _require(isinstance(member, dict) and set(member) == MEMBER_FIELDS
                 and type(member["index"]) is int and member["index"] == index
                 and cases._pin_valid(member["job_directory"])
                 and cases._pin_valid(member["job_record"], full=True, file=True)
                 and 1 < member["job_record"]["size"] <= writes.MAX_INTENT_BYTES
                 and cases._pin_valid(member["job_lock"], full=True, file=True)
                 and member["job_lock"]["size"] == 0
                 and member["job_lock"]["sha256"] == hashlib.sha256(b"").hexdigest(),
                 "record_invalid", "Write catalog member has unsupported private pins")
        job = cases._path(member["job_path"])
        _require(str(job) == member["job_path"] and job.parent == catalog.parent and job != catalog,
                 "record_invalid", "Write catalog member has unsupported sibling bindings")
        receipt = _public_write(member["write"])
        _require(_same(receipt, member["write"]) and str(job) not in paths
                 and receipt["write_id"] not in write_ids and receipt["review_id"] not in review_ids,
                 "record_invalid", "Write catalog membership is not uniquely ordered")
        paths.add(str(job))
        write_ids.add(receipt["write_id"])
        review_ids.add(receipt["review_id"])
    raw, _ = cases._bytes(catalog / CATALOG_NAME, MAX_CATALOG_BYTES, deadline)
    _require(raw == canonical(body).encode("utf-8")
             and _same(cases._directory(catalog.parent), body["parent"])
             and _same(cases._directory(catalog), body["catalog_directory"]),
             "record_invalid", "Write catalog differs from its canonical record or fixed directory identities")
    _catalog(body)
    _deadline(deadline)
    return body


def _replay(catalog: Path, paths: list[Path], operation_id: str, deadline: float) -> dict[str, Any]:
    with _lock(catalog, deadline):
        body = _read_catalog(catalog, deadline)
        _require(body["operation_id"] == operation_id
                 and [member["job_path"] for member in body["members"]] == [str(path) for path in paths],
                 "conflict", "Write catalog is already bound to another exact ordered request")
        result = _catalog(body)
        _deadline(deadline)
        return result


def create_write_catalog(catalog_directory: str | Path, job_directories: Any, *, operation_id: Any) -> dict[str, Any]:
    """Freeze explicit intent membership, without observing current target state."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        catalog = cases._path(catalog_directory)
        paths = _input_jobs(catalog, job_directories)
        _require(cases.jobs._operation(operation_id), "invalid_input", "Write catalog operation ID is unsupported")
        cases._directory(catalog.parent)
        if os.path.lexists(catalog):
            return _replay(catalog, paths, operation_id, deadline)
        preliminary = _capture_members(catalog, paths, deadline)
        try:
            catalog.mkdir(mode=0o700)
        except FileExistsError:
            return _replay(catalog, paths, operation_id, deadline)
        catalog.chmod(0o700)
        with _lock(catalog, deadline, create=True):
            members = _capture_members(catalog, paths, deadline)
            _require(_same(members, preliminary), "entry_changed", "Write membership changed during catalog reservation")
            created_at = time.time()
            _require(cases._timestamp(created_at), "io_failed", "Write catalog clock is unsupported")
            body = {"schema_version": 1, "rules_version": RULES_VERSION, "catalog_id": "wrrwcat_" + secrets.token_hex(16),
                    "operation_id": operation_id, "created_at": created_at, "catalog_path": str(catalog),
                    "parent": cases._directory(catalog.parent), "catalog_directory": cases._directory(catalog),
                    "write_count": len(members), "members": members}
            body["request_digest"] = digest(body)
            result = _catalog(body)
            try:
                obligations._shape(body)
            except WorkbenchError as error:
                raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_limit_exceeded",
                                     "Write catalog JSON structure exceeds its supported bound") from error
            _require(len(canonical(body).encode("utf-8")) <= MAX_CATALOG_BYTES,
                     "limit_exceeded", "Write catalog exceeds its byte bound")
            _deadline(deadline)
            store_backup._write_json(catalog / CATALOG_NAME, body, MAX_CATALOG_BYTES)
            store_backup._fsync_directory(catalog)
            store_backup._fsync_directory(catalog.parent)
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _OBSERVATION_ERRORS[1:] as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_io_failed",
                             "Write catalog could not be created") from error


def inspect_write_catalog(catalog_directory: str | Path) -> dict[str, Any]:
    """Read the catalog receipt without opening any captured member path."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        catalog = cases._path(catalog_directory)
        with _lock(catalog, deadline):
            result = _catalog(_read_catalog(catalog, deadline))
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _OBSERVATION_ERRORS[1:] as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_io_failed",
                             "Write catalog could not be inspected") from error


def _current(state: str, status: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"state": state, "status": status,
            "error_code": None if state == "available" else
            "workspace_restore_case_retention_review_write_catalog_entry_" + state}


def _current_write(catalog: Path, member: dict[str, Any], deadline: float) -> dict[str, Any]:
    try:
        _deadline(deadline)
        job = Path(member["job_path"])
        if not _same(cases._directory(job), member["job_directory"]):
            return _current("changed")
        with writes._lock(job, deadline):
            raw, pins = _job_pins(job, deadline)
            if not _same(pins, {key: member[key] for key in ("job_directory", "job_record", "job_lock")}):
                return _current("changed")
            body = writes._read_intent(job, deadline)
            if canonical(body).encode("utf-8") != raw or not _same(_write(body), member["write"]):
                return _current("changed")
            _separate(catalog, body)
            status = writes._locked_status(body, deadline)
            result = _current("available", status)
        _deadline(deadline)
        return result
    except _OBSERVATION_ERRORS:
        _deadline(deadline)
        return _current("unavailable")


def _after_index(cursor: Any, body: dict[str, Any]) -> int:
    if cursor is None:
        return 0
    _require(isinstance(cursor, dict) and set(cursor) == {"catalog_id", "after_index"}
             and cursor["catalog_id"] == body["catalog_id"]
             and type(cursor["after_index"]) is int and 0 <= cursor["after_index"] <= body["write_count"],
             "invalid_input", "Write catalog cursor is unsupported or belongs to another catalog")
    return cursor["after_index"]


def _page_limits(value: dict[str, Any], deadline: float) -> None:
    """Bound the growing response without dropping any requested member."""
    _deadline(deadline)
    try:
        obligations._shape(value)
    except WorkbenchError as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_limit_exceeded",
                             "Write catalog page JSON structure exceeds its supported bound") from error
    _require(len(canonical(value).encode("utf-8")) <= MAX_PAGE_BYTES,
             "limit_exceeded", "Write catalog page exceeds its byte bound")
    _deadline(deadline)


def read_write_catalog_page(catalog_directory: str | Path, *, limit: Any = MAX_PAGE_SIZE,
                            cursor: Any = None) -> dict[str, Any]:
    """Observe at most sixteen pinned jobs after releasing the catalog lock."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE,
                 "invalid_input", "Write catalog page size is outside its supported bound")
        catalog = cases._path(catalog_directory)
        with _lock(catalog, deadline):
            body = _read_catalog(catalog, deadline)
            after = _after_index(cursor, body)
            selected = body["members"][after:after + limit]
            receipt = _catalog(body)
        _deadline(deadline)
        checked_at = time.time()
        _require(cases._timestamp(checked_at), "io_failed", "Write catalog page clock is unsupported")
        entries = []
        page = {"rules_version": RULES_VERSION, "catalog": receipt, "checked_at": checked_at,
                "after_index": after, "limit": limit, "entries": entries, "next_cursor": None,
                "source_inputs_checked": False, "jobs_advanced": False}
        for member in selected:
            _deadline(deadline)
            entries.append({"index": member["index"], "write": member["write"],
                            "current_write": _current_write(catalog, member, deadline)})
            _deadline(deadline)
            end = after + len(entries)
            page["next_cursor"] = ({"catalog_id": body["catalog_id"], "after_index": end}
                                   if end < body["write_count"] else None)
            _page_limits(page, deadline)
        end = after + len(entries)
        page["next_cursor"] = ({"catalog_id": body["catalog_id"], "after_index": end}
                               if end < body["write_count"] else None)
        _page_limits(page, deadline)
        result = _public_page(page)
        _deadline(deadline)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _OBSERVATION_ERRORS[1:] as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_io_failed",
                             "Write catalog page could not be read") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_review_write_catalog_"):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict"):
        if error.code.endswith(suffix):
            return WorkbenchError("workspace_restore_case_retention_review_write_catalog_" + suffix,
                                  "Write catalog exceeded a bound or has unsupported private bindings")
    return WorkbenchError("workspace_restore_case_retention_review_write_catalog_record_invalid",
                          "Write catalog or selected immutable intent cannot be validated")
