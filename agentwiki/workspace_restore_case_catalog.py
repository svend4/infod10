"""Bounded immutable membership with separate current case-control readings.

Catalogs contain only an explicitly ordered list selected by the caller. Pages
read case and optional association controls; they never inspect original job
evidence, linked jobs, restore data, or databases. Membership is historical and
does not turn sequential observations into a common filesystem snapshot.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import secrets
import stat
import time
from typing import Any, Iterator

from . import store_backup, workspace_restore_cases as cases
from .store import WorkbenchError, canonical, digest
from .workspace_restore_cases_cli import _public_case, _public_link


RULES_VERSION = "workspace-restore-case-catalog-1"
CATALOG_NAME = ".agentwiki-restore-case-catalog.json"
LOCK_NAME = ".agentwiki-restore-case-catalog.lock"
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CASES = 256
MAX_PAGE_SIZE = 16
DEADLINE_SECONDS = cases.DEADLINE_SECONDS
CATALOG_ID = re.compile(r"wrcat_[0-9a-f]{32}\Z")
CATALOG_FIELDS = {"schema_version", "rules_version", "catalog_id", "operation_id", "created_at", "catalog_path",
                  "parent", "catalog_directory", "case_count", "members", "request_digest"}
MEMBER_FIELDS = {"index", "case_path", "case", "case_directory", "case_record", "case_lock"}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_catalog_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Restore case catalog exceeded its common time bound")


def _path(value: Any) -> Path:
    return cases._path(value)


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _input_cases(catalog: Path, values: Any) -> list[Path]:
    _require(type(values) in {list, tuple} and 1 <= len(values) <= MAX_CASES,
             "invalid_input", "Catalog membership must be a bounded explicit ordered list")
    paths = [_path(value) for value in values]
    _require(len(set(paths)) == len(paths) and all(path != catalog and path.parent == catalog.parent for path in paths),
             "invalid_input", "Catalog and unique selected cases must be distinct private siblings")
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
        raise WorkbenchError("workspace_restore_case_catalog_incomplete", "Catalog coordination control is unavailable") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Catalog coordination requires a private empty regular owner file")
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


def _case_pins(case: Path, deadline: float) -> tuple[bytes, dict[str, Any]]:
    raw, record = cases._bytes(case / cases.CASE_NAME, cases.MAX_CASE_BYTES, deadline)
    _, lock = cases._bytes(case / cases.LOCK_NAME, 0, deadline)
    return raw, {"case_directory": cases._directory(case), "case_record": record, "case_lock": lock}


def _capture_member(case: Path, index: int, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    with cases._lock(case, deadline):
        body = cases._read_case(case)
        raw, pins = _case_pins(case, deadline)
        _require(canonical(body).encode("utf-8") == raw and _same(body["case_directory"], pins["case_directory"]),
                 "entry_changed", "Selected case changed while catalog membership was captured")
        member = {"index": index, "case_path": str(case), "case": _public_case(cases._public_case(body)), **pins}
    _deadline(deadline)
    return member


def _capture_members(paths: list[Path], deadline: float) -> list[dict[str, Any]]:
    members = [_capture_member(path, index, deadline) for index, path in enumerate(paths, 1)]
    _require(len({member["case"]["case_id"] for member in members}) == len(members),
             "invalid_input", "Selected cases must have unique historical identifiers")
    return members


def _public_catalog(body: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION,
            **{key: body[key] for key in ("catalog_id", "operation_id", "created_at", "case_count")},
            "membership_frozen": True, "order_frozen": True, "inspection_only": True,
            "domain_writes_allowed": False, "original_files_modified": False,
            "old_authority_activated": False, "external_fencing_established": False}


def _read_catalog(catalog: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    _require(cases._names(catalog, 2) == {CATALOG_NAME, LOCK_NAME},
             "record_invalid", "Catalog contains unsupported or incomplete control entries")
    body = store_backup._read_json(catalog / CATALOG_NAME, MAX_CATALOG_BYTES)
    _require(set(body) == CATALOG_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION
             and isinstance(body["catalog_id"], str) and CATALOG_ID.fullmatch(body["catalog_id"])
             and cases.jobs._operation(body["operation_id"]) and cases._timestamp(body["created_at"])
             and body["catalog_path"] == str(catalog)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["catalog_directory"])
             and type(body["case_count"]) is int and 1 <= body["case_count"] <= MAX_CASES
             and isinstance(body["members"], list) and len(body["members"]) == body["case_count"]
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Catalog record has unsupported immutable bindings")
    paths, case_ids = set(), set()
    for index, member in enumerate(body["members"], 1):
        _deadline(deadline)
        _require(isinstance(member, dict) and set(member) == MEMBER_FIELDS
                 and type(member["index"]) is int and member["index"] == index
                 and cases._pin_valid(member["case_directory"])
                 and cases._pin_valid(member["case_record"], full=True, file=True)
                 and 1 < member["case_record"]["size"] <= cases.MAX_CASE_BYTES
                 and cases._pin_valid(member["case_lock"], full=True, file=True) and member["case_lock"]["size"] == 0
                 and member["case_lock"]["sha256"] == hashlib.sha256(b"").hexdigest(),
                 "record_invalid", "Catalog member metadata is malformed")
        case = _path(member["case_path"])
        _require(str(case) == member["case_path"] and case.parent == catalog.parent and case != catalog,
                 "record_invalid", "Catalog member path binding is malformed")
        receipt = _public_case(member["case"])
        _require(_same(receipt, member["case"]) and str(case) not in paths and receipt["case_id"] not in case_ids,
                 "record_invalid", "Catalog membership is not uniquely ordered")
        paths.add(str(case))
        case_ids.add(receipt["case_id"])
    _require(_same(cases._directory(catalog.parent), body["parent"])
             and _same(cases._directory(catalog), body["catalog_directory"]),
             "record_invalid", "Catalog directories differ from their fixed private identities")
    _deadline(deadline)
    return body


def _replay(catalog: Path, paths: list[Path], operation_id: str, deadline: float) -> dict[str, Any]:
    with _lock(catalog, deadline):
        body = _read_catalog(catalog, deadline)
        _require(body["operation_id"] == operation_id
                 and [member["case_path"] for member in body["members"]] == [str(path) for path in paths],
                 "conflict", "Catalog is already bound to another ordered request")
        return _public_catalog(body)


def create_catalog(catalog_directory: str | Path, case_directories: Any, *, operation_id: Any) -> dict[str, Any]:
    """Freeze explicit ordered membership; exact replay needs no live cases."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        catalog = _path(catalog_directory)
        paths = _input_cases(catalog, case_directories)
        _require(cases.jobs._operation(operation_id), "invalid_input", "Catalog operation ID is unsupported")
        cases._directory(catalog.parent)
        if os.path.lexists(catalog):
            return _replay(catalog, paths, operation_id, deadline)
        preliminary = _capture_members(paths, deadline)
        try:
            catalog.mkdir(mode=0o700)
        except FileExistsError:
            return _replay(catalog, paths, operation_id, deadline)
        catalog.chmod(0o700)
        with _lock(catalog, deadline, create=True):
            members = _capture_members(paths, deadline)
            _require(_same(members, preliminary), "entry_changed", "Selected cases changed during catalog reservation")
            body = {"schema_version": 1, "rules_version": RULES_VERSION, "catalog_id": "wrcat_" + secrets.token_hex(16),
                    "operation_id": operation_id, "created_at": time.time(), "catalog_path": str(catalog),
                    "parent": cases._directory(catalog.parent), "catalog_directory": cases._directory(catalog),
                    "case_count": len(members), "members": members}
            body["request_digest"] = digest(body)
            _deadline(deadline)
            store_backup._write_json(catalog / CATALOG_NAME, body, MAX_CATALOG_BYTES)
            store_backup._fsync_directory(catalog)
            store_backup._fsync_directory(catalog.parent)
            _deadline(deadline)
            return _public_catalog(body)
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_catalog_io_failed", "Case catalog could not be recorded") from error


def _unavailable() -> dict[str, Any]:
    return {"state": "unavailable", "link": None, "error_code": "workspace_restore_case_catalog_entry_unavailable"}


def _changed() -> dict[str, Any]:
    return {"state": "changed", "link": None, "error_code": "workspace_restore_case_catalog_entry_changed"}


def _current_case(member: dict[str, Any], deadline: float) -> dict[str, Any]:
    try:
        _deadline(deadline)
        case = Path(member["case_path"])
        if not _same(cases._directory(case), member["case_directory"]):
            return _changed()
        with cases._lock(case, deadline):
            raw, pins = _case_pins(case, deadline)
            if not _same(pins, {key: member[key] for key in ("case_directory", "case_record", "case_lock")}):
                return _changed()
            body = cases._read_case(case)
            if canonical(body).encode("utf-8") != raw or not _same(_public_case(cases._public_case(body)), member["case"]):
                return _changed()
            link = cases._read_link(case, body)
            result = {"state": "available", "link": None if link is None else _public_link(cases._public_link(link)),
                      "error_code": None}
        _deadline(deadline)
        return result
    except (WorkbenchError, OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
        # Per-member failures remain visible. Expired common time never becomes
        # a successful partial page or a new per-case allowance.
        _deadline(deadline)
        return _unavailable()


def _after_index(cursor: Any, body: dict[str, Any]) -> int:
    if cursor is None:
        return 0
    _require(isinstance(cursor, dict) and set(cursor) == {"catalog_id", "after_index"}
             and cursor["catalog_id"] == body["catalog_id"]
             and type(cursor["after_index"]) is int and 0 <= cursor["after_index"] <= body["case_count"],
             "invalid_input", "Catalog cursor is unsupported or belongs to another catalog")
    return cursor["after_index"]


def read_catalog_page(catalog_directory: str | Path, *, limit: Any = MAX_PAGE_SIZE, cursor: Any = None) -> dict[str, Any]:
    """Read a bounded page of current case controls, retaining historical rows."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(type(limit) is int and 1 <= limit <= MAX_PAGE_SIZE,
                 "invalid_input", "Catalog page size is outside its supported bound")
        catalog = _path(catalog_directory)
        with _lock(catalog, deadline):
            body = _read_catalog(catalog, deadline)
            after = _after_index(cursor, body)
            selected = body["members"][after:after + limit]
            entries = []
            for member in selected:
                _deadline(deadline)
                entries.append({"index": member["index"], "case": member["case"],
                                "current_case": _current_case(member, deadline)})
                _deadline(deadline)
            end = after + len(entries)
            _deadline(deadline)
            return {"rules_version": RULES_VERSION, "catalog": _public_catalog(body), "after_index": after,
                    "limit": limit, "entries": entries,
                    "next_cursor": {"catalog_id": body["catalog_id"], "after_index": end} if end < body["case_count"] else None,
                    "original_evidence_checked": False, "live_jobs_checked": False}
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_catalog_io_failed", "Case catalog page could not be read") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_catalog_"):
        return error
    if error.code.endswith("limit_exceeded"):
        return WorkbenchError("workspace_restore_case_catalog_limit_exceeded", "Catalog exceeded a supported observation bound")
    if error.code.endswith("private_path_required"):
        return WorkbenchError("workspace_restore_case_catalog_private_path_required", "Catalog requires private owner controls")
    if error.code.endswith("invalid_input"):
        return WorkbenchError("workspace_restore_case_catalog_invalid_input", "Catalog input is unsupported")
    if error.code.endswith("incomplete"):
        return WorkbenchError("workspace_restore_case_catalog_incomplete", "A required catalog control is unavailable")
    return WorkbenchError("workspace_restore_case_catalog_record_invalid", "Catalog or selected case control cannot be validated")
