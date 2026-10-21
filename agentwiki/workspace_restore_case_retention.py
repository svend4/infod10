"""Capture immutable advisory retention assessments for selected catalog cases.

The saved assessment has one age-evaluation clock and bounded sequential detail
observations. Reading or replaying it needs no live catalog, case, job or source.
Nothing here archives, deletes, repairs or authorizes changes to those objects.
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

from . import obligations, store_backup, workspace_restore_case_catalog as catalogs
from . import workspace_restore_cases as cases
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_catalog_cli import _public_catalog
from .workspace_restore_cases_cli import _public_case, _public_inspection
from .workspace_restore_case_retention_cli import _expected_reasons, _public_plan


RULES_VERSION = "workspace-restore-case-retention-1"
PLAN_NAME = ".agentwiki-restore-case-retention.json"
LOCK_NAME = ".agentwiki-restore-case-retention.lock"
MAX_PLAN_BYTES = 8 * 1024 * 1024
MAX_RETAIN_SECONDS = 315360000
MAX_SELECTED_CASES = 16
DEADLINE_SECONDS = cases.DEADLINE_SECONDS
PLAN_ID = re.compile(r"wrrp_[0-9a-f]{32}\Z")
PLAN_FIELDS = {"schema_version", "rules_version", "plan_id", "operation_id", "created_at", "plan_path", "parent",
               "plan_directory", "source", "policy", "after_index", "limit", "entries", "request_digest"}
SOURCE_FIELDS = {"catalog_path", "catalog_directory", "catalog_record", "catalog_lock", "catalog_request_digest",
                 "catalog", "members"}


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Retention assessment exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _inputs(catalog: Path, plan: Path, operation_id: Any, retain_seconds: Any, after_index: Any, limit: Any) -> None:
    _require(catalog != plan and catalog.parent == plan.parent and cases.jobs._operation(operation_id)
             and _integer(retain_seconds, 0, MAX_RETAIN_SECONDS)
             and _integer(after_index, 0, catalogs.MAX_CASES - 1) and _integer(limit, 1, MAX_SELECTED_CASES),
             "invalid_input", "Retention request needs separate private siblings and bounded policy and range")


@contextmanager
def _lock(plan: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(plan)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(plan / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_retention_incomplete", "Retention coordination control is unavailable") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Retention coordination needs a private empty regular owner file")
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


def _capture_source(catalog: Path, plan: Path, after: int, limit: int, deadline: float) -> dict[str, Any]:
    """Capture only catalog controls; release its lock before any case detail."""
    _deadline(deadline)
    with catalogs._lock(catalog, deadline):
        body = catalogs._read_catalog(catalog, deadline)
        _require(after < body["case_count"] and all(member["case_path"] != str(plan) for member in body["members"]),
                 "invalid_input", "Retention selection must be nonempty and separate from every catalog case")
        raw, record = cases._bytes(catalog / catalogs.CATALOG_NAME, catalogs.MAX_CATALOG_BYTES, deadline)
        _, lock = cases._bytes(catalog / catalogs.LOCK_NAME, 0, deadline)
        directory = cases._directory(catalog)
        _require(canonical(body).encode("utf-8") == raw and _same(body["catalog_directory"], directory),
                 "source_changed", "Catalog controls changed during capture")
        source = {"catalog_path": str(catalog), "catalog_directory": directory,
                  "catalog_record": record, "catalog_lock": lock, "catalog_request_digest": body["request_digest"],
                  "catalog": _public_catalog(catalogs._public_catalog(body)), "members": body["members"][after:after + limit]}
    _deadline(deadline)
    return source


def _source_valid(source: Any, plan: Path, after: int, limit: int, deadline: float) -> None:
    """Validate captured membership without opening any captured source path."""
    _require(isinstance(source, dict) and set(source) == SOURCE_FIELDS, "record_invalid", "Retention source fields are malformed")
    catalog = cases._path(source["catalog_path"])
    receipt = _public_catalog(source["catalog"])
    _require(str(catalog) == source["catalog_path"] and catalog != plan and catalog.parent == plan.parent
             and _same(receipt, source["catalog"]) and after < receipt["case_count"]
             and cases._pin_valid(source["catalog_directory"])
             and cases._pin_valid(source["catalog_record"], full=True, file=True)
             and 1 < source["catalog_record"]["size"] <= catalogs.MAX_CATALOG_BYTES
             and cases._pin_valid(source["catalog_lock"], full=True, file=True)
             and source["catalog_lock"]["size"] == 0
             and source["catalog_lock"]["sha256"] == hashlib.sha256(b"").hexdigest()
             and isinstance(source["catalog_request_digest"], str) and cases.jobs.HASH.fullmatch(source["catalog_request_digest"])
             and isinstance(source["members"], list) and len(source["members"]) == min(limit, receipt["case_count"] - after),
             "record_invalid", "Retention catalog snapshot has unsupported bindings")
    paths, identifiers = set(), set()
    for index, member in enumerate(source["members"], after + 1):
        _deadline(deadline)
        _require(isinstance(member, dict) and set(member) == catalogs.MEMBER_FIELDS
                 and type(member["index"]) is int and member["index"] == index
                 and cases._pin_valid(member["case_directory"])
                 and cases._pin_valid(member["case_record"], full=True, file=True)
                 and 1 < member["case_record"]["size"] <= cases.MAX_CASE_BYTES
                 and cases._pin_valid(member["case_lock"], full=True, file=True) and member["case_lock"]["size"] == 0
                 and member["case_lock"]["sha256"] == hashlib.sha256(b"").hexdigest(),
                 "record_invalid", "Retention selected-member pins are malformed")
        case = cases._path(member["case_path"])
        case_receipt = _public_case(member["case"])
        _require(str(case) == member["case_path"] and case.parent == plan.parent and case not in {catalog, plan}
                 and str(case) not in paths and case_receipt["case_id"] not in identifiers and _same(case_receipt, member["case"]),
                 "record_invalid", "Retention membership is not uniquely ordered")
        paths.add(str(case))
        identifiers.add(case_receipt["case_id"])


def _observation_failure(state: str) -> dict[str, Any]:
    return {"state": state, "detail": None, "error_code": "workspace_restore_case_retention_case_" + state}


def _observe_member(member: dict[str, Any], deadline: float) -> dict[str, Any]:
    try:
        _deadline(deadline)
        case = Path(member["case_path"])
        if not _same(cases._directory(case), member["case_directory"]):
            return _observation_failure("changed")
        with cases._lock(case, deadline):
            raw, pins = catalogs._case_pins(case, deadline)
            if not _same(pins, {key: member[key] for key in ("case_directory", "case_record", "case_lock")}):
                return _observation_failure("changed")
            body = cases._read_case(case)
            if canonical(body).encode("utf-8") != raw or not _same(_public_case(cases._public_case(body)), member["case"]):
                return _observation_failure("changed")
            link = cases._read_link(case, body)
            original_evidence = cases._observe_evidence(body, deadline)
            _deadline(deadline)
            current_attempt = cases._observe_attempt(link, deadline)
            _deadline(deadline)
            detail = _public_inspection({"rules_version": cases.RULES_VERSION, "case": cases._public_case(body),
                                         "link": None if link is None else cases._public_link(link),
                                         "original_evidence": original_evidence, "current_attempt": current_attempt})
        _deadline(deadline)
        return {"state": "available", "detail": detail, "error_code": None}
    except (WorkbenchError, OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError):
        # Old observation helpers translate errors, including their expired
        # deadline. Check the shared limit again before retaining a row failure.
        _deadline(deadline)
        return _observation_failure("unavailable")


def _public(body: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION,
            **{key: body[key] for key in ("plan_id", "operation_id", "created_at", "policy", "after_index", "limit", "entries")},
            "catalog": body["source"]["catalog"], "advisory_only": True, "inspection_only": True,
            "domain_writes_allowed": False, "original_files_modified": False,
            "archival_authorized": False, "deletion_authorized": False,
            "old_authority_activated": False, "external_fencing_established": False}


def _read_plan(plan: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    _require(cases._names(plan, 2) == {PLAN_NAME, LOCK_NAME}, "record_invalid", "Retention controls are unsupported or incomplete")
    body = store_backup._read_json(plan / PLAN_NAME, MAX_PLAN_BYTES)
    _require(set(body) == PLAN_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["plan_id"], str) and PLAN_ID.fullmatch(body["plan_id"])
             and cases.jobs._operation(body["operation_id"]) and cases._timestamp(body["created_at"])
             and body["plan_path"] == str(plan) and cases._pin_valid(body["parent"]) and cases._pin_valid(body["plan_directory"])
             and isinstance(body["policy"], dict) and set(body["policy"]) == {"retain_seconds"}
             and _integer(body["policy"]["retain_seconds"], 0, MAX_RETAIN_SECONDS)
             and _integer(body["after_index"], 0, catalogs.MAX_CASES - 1) and _integer(body["limit"], 1, MAX_SELECTED_CASES)
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Retention record has unsupported immutable bindings")
    _source_valid(body["source"], plan, body["after_index"], body["limit"], deadline)
    public = _public_plan(_public(body))
    _require(_same(public["entries"], body["entries"])
             and len(body["entries"]) == len(body["source"]["members"])
             and all(entry["index"] == member["index"] and _same(entry["case"], member["case"])
                     for entry, member in zip(body["entries"], body["source"]["members"]))
             and _same(cases._directory(plan.parent), body["parent"])
             and _same(cases._directory(plan), body["plan_directory"]),
             "record_invalid", "Retention rows or control directories differ from their fixed bindings")
    _deadline(deadline)
    return body


def _replay(plan: Path, catalog: Path, operation_id: str, retain_seconds: int, after: int, limit: int, deadline: float) -> dict[str, Any]:
    with _lock(plan, deadline):
        body = _read_plan(plan, deadline)
        _require(body["operation_id"] == operation_id and body["source"]["catalog_path"] == str(catalog)
                 and body["policy"]["retain_seconds"] == retain_seconds and body["after_index"] == after and body["limit"] == limit,
                 "conflict", "Retention plan is already bound to another exact request")
        return _public_plan(_public(body))


def create_retention_plan(catalog_directory: str | Path, plan_directory: str | Path, *, operation_id: Any,
                          retain_seconds: Any, after_index: Any = 0, limit: Any = MAX_SELECTED_CASES) -> dict[str, Any]:
    """Save an advisory assessment; exact replay returns the historical result."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        catalog, plan = cases._path(catalog_directory), cases._path(plan_directory)
        _inputs(catalog, plan, operation_id, retain_seconds, after_index, limit)
        cases._directory(plan.parent)
        if os.path.lexists(plan):
            return _replay(plan, catalog, operation_id, retain_seconds, after_index, limit, deadline)
        preliminary = _capture_source(catalog, plan, after_index, limit, deadline)
        try:
            plan.mkdir(mode=0o700)
        except FileExistsError:
            return _replay(plan, catalog, operation_id, retain_seconds, after_index, limit, deadline)
        plan.chmod(0o700)
        with _lock(plan, deadline, create=True):
            source = _capture_source(catalog, plan, after_index, limit, deadline)
            _require(_same(source, preliminary), "source_changed", "Catalog source changed during retention reservation")
            created_at = time.time()
            _require(cases._timestamp(created_at), "io_failed", "Retention evaluation clock is unsupported")
            entries = []
            for member in source["members"]:
                _deadline(deadline)
                observation = _observe_member(member, deadline)
                _deadline(deadline)
                reasons = _expected_reasons(member["case"], observation, created_at, retain_seconds)
                entries.append({"index": member["index"], "case": member["case"], "observation": observation,
                                "disposition": "held" if reasons else "review_candidate", "reasons": reasons})
            body = {"schema_version": 1, "rules_version": RULES_VERSION, "plan_id": "wrrp_" + secrets.token_hex(16),
                    "operation_id": operation_id, "created_at": created_at, "plan_path": str(plan),
                    "parent": cases._directory(plan.parent), "plan_directory": cases._directory(plan), "source": source,
                    "policy": {"retain_seconds": retain_seconds}, "after_index": after_index, "limit": limit, "entries": entries}
            body["request_digest"] = digest(body)
            result = _public_plan(_public(body))
            try:
                obligations._shape(body)
            except WorkbenchError as error:
                raise WorkbenchError("workspace_restore_case_retention_limit_exceeded", "Retention JSON structure exceeds its supported bound") from error
            _require(len(canonical(body).encode("utf-8")) <= MAX_PLAN_BYTES, "limit_exceeded", "Retention record exceeds its byte bound")
            _deadline(deadline)
            store_backup._write_json(plan / PLAN_NAME, body, MAX_PLAN_BYTES)
            store_backup._fsync_directory(plan)
            store_backup._fsync_directory(plan.parent)
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_io_failed", "Retention assessment could not be recorded") from error


def inspect_retention_plan(plan_directory: str | Path) -> dict[str, Any]:
    """Read the stored assessment without opening any captured input path."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        plan = cases._path(plan_directory)
        with _lock(plan, deadline):
            return _public_plan(_public(_read_plan(plan, deadline)))
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_io_failed", "Stored retention assessment could not be read") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_"):
        return error
    if error.code.endswith("limit_exceeded"):
        return WorkbenchError("workspace_restore_case_retention_limit_exceeded", "Retention assessment exceeded a supported bound")
    if error.code.endswith("private_path_required"):
        return WorkbenchError("workspace_restore_case_retention_private_path_required", "Retention assessment requires private owner controls")
    if error.code.endswith("invalid_input"):
        return WorkbenchError("workspace_restore_case_retention_invalid_input", "Retention assessment input is unsupported")
    if error.code.endswith("incomplete"):
        return WorkbenchError("workspace_restore_case_retention_incomplete", "A required retention control is unavailable")
    return WorkbenchError("workspace_restore_case_retention_record_invalid", "Retention record or captured input cannot be validated")
