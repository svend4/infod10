"""Immutable advisory reviews and separate observations of their current basis.

The review stores a complete public retention plan and enough private captured
bindings to verify its exact original representation offline. Reading or replaying
that review never opens its captured plan, catalog, case, job or database paths.
Revalidation is a separate bounded read; it grants no archival/deletion authority.
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
from . import workspace_restore_case_retention as retention
from . import workspace_restore_cases as cases
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_catalog_cli import _public_catalog
from .workspace_restore_case_retention_cli import _expected_reasons, _public_plan
from .workspace_restore_case_retention_review_cli import _expected_comparison, _public_review, _public_revalidation


RULES_VERSION = "workspace-restore-case-retention-review-1"
REVIEW_NAME = ".agentwiki-restore-case-retention-review.json"
LOCK_NAME = ".agentwiki-restore-case-retention-review.lock"
MAX_REVIEW_BYTES = 16 * 1024 * 1024
DEADLINE_SECONDS = cases.DEADLINE_SECONDS
REVIEW_ID = re.compile(r"wrrv_[0-9a-f]{32}\Z")
VERDICTS = {"reviewed", "defer", "request_fresh_plan"}
REVIEW_FIELDS = {
    "schema_version", "rules_version", "review_id", "operation_id", "created_at",
    "review_path", "parent", "review_directory", "actor", "verdict", "plan_anchor",
    "plan_snapshot", "plan_source", "request_digest",
}
ANCHOR_FIELDS = {"plan_path", "plan_directory", "plan_record", "plan_lock", "plan_request_digest"}
FLAGS = {
    "advisory_only": True,
    "inspection_only": True,
    "domain_writes_allowed": False,
    "original_files_modified": False,
    "archival_authorized": False,
    "deletion_authorized": False,
    "actor_authenticated": False,
    "old_authority_activated": False,
    "external_fencing_established": False,
}
_OBSERVATION_ERRORS = (WorkbenchError, OSError, sqlite3.Error, ValueError, TypeError,
                       KeyError, OverflowError, UnicodeError, RecursionError)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_review_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Review exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _inputs(plan: Path, review: Path, operation_id: Any, actor: Any, verdict: Any) -> None:
    _require(plan != review and plan.parent == review.parent and cases.jobs._operation(operation_id)
             and cases.jobs._operation(actor) and isinstance(verdict, str) and verdict in VERDICTS,
             "invalid_input", "Review needs distinct private siblings and supported self-declared identifiers and verdict")


@contextmanager
def _lock(review: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(review)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(review / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_retention_review_incomplete", "Review coordination control is unavailable") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Review coordination needs a private empty regular owner file")
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


def _capture_plan(plan: Path, review: Path, deadline: float) -> dict[str, Any]:
    """Capture validated plan metadata, with no case/job/database observations."""
    _deadline(deadline)
    with retention._lock(plan, deadline):
        body = retention._read_plan(plan, deadline)
        source = body["source"]
        _require(str(review) != source["catalog_path"]
                 and all(member["case_path"] != str(review) for member in source["members"]),
                 "invalid_input", "Review must be separate from the captured catalog and selected cases")
        raw, record = cases._bytes(plan / retention.PLAN_NAME, retention.MAX_PLAN_BYTES, deadline)
        _, lock = cases._bytes(plan / retention.LOCK_NAME, 0, deadline)
        directory = cases._directory(plan)
        _require(canonical(body).encode("utf-8") == raw and _same(directory, body["plan_directory"]),
                 "source_changed", "Retention controls changed during review capture")
        captured = {
            "plan_anchor": {"plan_path": str(plan), "plan_directory": directory,
                            "plan_record": record, "plan_lock": lock,
                            "plan_request_digest": body["request_digest"]},
            "plan_snapshot": _public_plan(retention._public(body)),
            "plan_source": source,
        }
    _deadline(deadline)
    return captured


def _reconstruct_plan(body: dict[str, Any]) -> dict[str, Any]:
    """Losslessly reconstruct the complete known retention private schema 1."""
    snapshot, anchor = body["plan_snapshot"], body["plan_anchor"]
    return {
        "schema_version": 1, "rules_version": retention.RULES_VERSION,
        "plan_id": snapshot["plan_id"], "operation_id": snapshot["operation_id"],
        "created_at": snapshot["created_at"], "plan_path": anchor["plan_path"],
        "parent": body["parent"], "plan_directory": anchor["plan_directory"],
        "source": body["plan_source"], "policy": snapshot["policy"],
        "after_index": snapshot["after_index"], "limit": snapshot["limit"],
        "entries": snapshot["entries"], "request_digest": anchor["plan_request_digest"],
    }


def _captured_valid(body: dict[str, Any], review: Path, deadline: float) -> None:
    """Validate captured descriptors and exact old bytes without live input reads."""
    anchor, snapshot = body["plan_anchor"], _public_plan(body["plan_snapshot"])
    _require(isinstance(anchor, dict) and set(anchor) == ANCHOR_FIELDS,
             "record_invalid", "Review plan anchor fields are malformed")
    plan = cases._path(anchor["plan_path"])
    _require(str(plan) == anchor["plan_path"] and plan != review and plan.parent == review.parent
             and _same(snapshot, body["plan_snapshot"])
             and cases._pin_valid(anchor["plan_directory"])
             and cases._pin_valid(anchor["plan_record"], full=True, file=True)
             and 1 < anchor["plan_record"]["size"] <= retention.MAX_PLAN_BYTES
             and cases._pin_valid(anchor["plan_lock"], full=True, file=True)
             and anchor["plan_lock"]["size"] == 0
             and anchor["plan_lock"]["sha256"] == hashlib.sha256(b"").hexdigest()
             and isinstance(anchor["plan_request_digest"], str)
             and cases.jobs.HASH.fullmatch(anchor["plan_request_digest"]),
             "record_invalid", "Review plan anchor has unsupported private bindings")
    source = body["plan_source"]
    retention._source_valid(source, plan, snapshot["after_index"], snapshot["limit"], deadline)
    _require(str(review) != source["catalog_path"]
             and all(member["case_path"] != str(review) for member in source["members"])
             and _same(snapshot["catalog"], source["catalog"])
             and len(snapshot["entries"]) == len(source["members"])
             and all(entry["index"] == member["index"] and _same(entry["case"], member["case"])
                     for entry, member in zip(snapshot["entries"], source["members"])),
             "record_invalid", "Review snapshot rows differ from their captured ordered membership")
    original = _reconstruct_plan(body)
    raw = canonical(original).encode("utf-8")
    _require(digest({key: value for key, value in original.items() if key != "request_digest"})
             == anchor["plan_request_digest"] and len(raw) == anchor["plan_record"]["size"]
             and hashlib.sha256(raw).hexdigest() == anchor["plan_record"]["sha256"],
             "record_invalid", "Review does not reconstruct its exact original retention record")
    _deadline(deadline)


def _public(body: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION,
            **{key: body[key] for key in ("review_id", "operation_id", "created_at", "actor", "verdict")},
            "plan": body["plan_snapshot"], **FLAGS}


def _read_review(review: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    _require(cases._names(review, 3) == {REVIEW_NAME, LOCK_NAME}, "incomplete", "Review controls are incomplete")
    body = store_backup._read_json(review / REVIEW_NAME, MAX_REVIEW_BYTES)
    _require(set(body) == REVIEW_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["review_id"], str)
             and REVIEW_ID.fullmatch(body["review_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["review_path"] == str(review)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["review_directory"])
             and cases.jobs._operation(body["actor"]) and isinstance(body["verdict"], str)
             and body["verdict"] in VERDICTS and isinstance(body["request_digest"], str)
             and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Review record has unsupported immutable bindings")
    _captured_valid(body, review, deadline)
    _public_review(_public(body))
    _require(_same(cases._directory(review.parent), body["parent"])
             and _same(cases._directory(review), body["review_directory"]),
             "record_invalid", "Review control directories differ from their fixed bindings")
    _deadline(deadline)
    return body


def _replay(plan: Path, review: Path, operation_id: str, actor: str, verdict: str, deadline: float) -> dict[str, Any]:
    with _lock(review, deadline):
        body = _read_review(review, deadline)
        _require(body["plan_anchor"]["plan_path"] == str(plan) and body["operation_id"] == operation_id
                 and body["actor"] == actor and body["verdict"] == verdict,
                 "conflict", "Review is already bound to another exact request")
        result = _public_review(_public(body))
        _deadline(deadline)
        return result


def record_review(plan_directory: str | Path, review_directory: str | Path, *, operation_id: Any,
                  actor: Any, verdict: Any) -> dict[str, Any]:
    """Record one self-declared advisory review of the complete stored plan."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        plan, review = cases._path(plan_directory), cases._path(review_directory)
        _inputs(plan, review, operation_id, actor, verdict)
        cases._directory(review.parent)
        if os.path.lexists(review):
            return _replay(plan, review, operation_id, actor, verdict, deadline)
        preliminary = _capture_plan(plan, review, deadline)
        try:
            review.mkdir(mode=0o700)
        except FileExistsError:
            return _replay(plan, review, operation_id, actor, verdict, deadline)
        review.chmod(0o700)
        with _lock(review, deadline, create=True):
            captured = _capture_plan(plan, review, deadline)
            _require(_same(captured, preliminary), "source_changed", "Retention source changed during review reservation")
            created_at = time.time()
            _require(cases._timestamp(created_at), "io_failed", "Review clock is unsupported")
            body = {"schema_version": 1, "rules_version": RULES_VERSION, "review_id": "wrrv_" + secrets.token_hex(16),
                    "operation_id": operation_id, "created_at": created_at, "review_path": str(review),
                    "parent": cases._directory(review.parent), "review_directory": cases._directory(review),
                    "actor": actor, "verdict": verdict, **captured}
            body["request_digest"] = digest(body)
            _captured_valid(body, review, deadline)
            result = _public_review(_public(body))
            try:
                obligations._shape(body)
            except WorkbenchError as error:
                raise WorkbenchError("workspace_restore_case_retention_review_limit_exceeded", "Review JSON structure exceeds its supported bound") from error
            _require(len(canonical(body).encode("utf-8")) <= MAX_REVIEW_BYTES, "limit_exceeded", "Review record exceeds its byte bound")
            _deadline(deadline)
            store_backup._write_json(review / REVIEW_NAME, body, MAX_REVIEW_BYTES)
            store_backup._fsync_directory(review)
            store_backup._fsync_directory(review.parent)
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_io_failed", "Review could not be recorded") from error


def inspect_review(review_directory: str | Path) -> dict[str, Any]:
    """Read the historical review through its own controls only."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        review = cases._path(review_directory)
        with _lock(review, deadline):
            result = _public_review(_public(_read_review(review, deadline)))
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_io_failed", "Stored review could not be read") from error


def _binding(kind: str, state: str) -> dict[str, Any]:
    return {"state": state, "error_code": None if state == "unchanged" else
            "workspace_restore_case_retention_review_" + kind + "_" + state}


def _observe_plan(body: dict[str, Any], deadline: float) -> dict[str, Any]:
    anchor = body["plan_anchor"]
    plan = Path(anchor["plan_path"])
    try:
        _deadline(deadline)
        if not _same(cases._directory(plan), anchor["plan_directory"]):
            return _binding("plan", "changed")
        with retention._lock(plan, deadline):
            raw, record = cases._bytes(plan / retention.PLAN_NAME, retention.MAX_PLAN_BYTES, deadline)
            _, lock = cases._bytes(plan / retention.LOCK_NAME, 0, deadline)
            if not _same(cases._directory(plan), anchor["plan_directory"]) or not _same(record, anchor["plan_record"]) or not _same(lock, anchor["plan_lock"]):
                return _binding("plan", "changed")
            current = retention._read_plan(plan, deadline)
            if canonical(current).encode("utf-8") != raw or not _same(current, _reconstruct_plan(body)):
                return _binding("plan", "changed")
        _deadline(deadline)
        return _binding("plan", "unchanged")
    except _OBSERVATION_ERRORS:
        _deadline(deadline)
        return _binding("plan", "unavailable")


def _observe_catalog(body: dict[str, Any], deadline: float) -> dict[str, Any]:
    source, snapshot = body["plan_source"], body["plan_snapshot"]
    catalog = Path(source["catalog_path"])
    try:
        _deadline(deadline)
        if not _same(cases._directory(catalog), source["catalog_directory"]):
            return _binding("catalog", "changed")
        with catalogs._lock(catalog, deadline):
            raw, record = cases._bytes(catalog / catalogs.CATALOG_NAME, catalogs.MAX_CATALOG_BYTES, deadline)
            _, lock = cases._bytes(catalog / catalogs.LOCK_NAME, 0, deadline)
            if not _same(cases._directory(catalog), source["catalog_directory"]) or not _same(record, source["catalog_record"]) or not _same(lock, source["catalog_lock"]):
                return _binding("catalog", "changed")
            current = catalogs._read_catalog(catalog, deadline)
            if (canonical(current).encode("utf-8") != raw
                    or current["request_digest"] != source["catalog_request_digest"]
                    or not _same(_public_catalog(catalogs._public_catalog(current)), source["catalog"])
                    or not _same(current["members"][snapshot["after_index"]:snapshot["after_index"] + snapshot["limit"]], source["members"])):
                return _binding("catalog", "changed")
        _deadline(deadline)
        return _binding("catalog", "unchanged")
    except _OBSERVATION_ERRORS:
        _deadline(deadline)
        return _binding("catalog", "unavailable")


def revalidate_review(review_directory: str | Path) -> dict[str, Any]:
    """Observe current bindings/details without changing or activating the review."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        review = cases._path(review_directory)
        with _lock(review, deadline):
            body = _read_review(review, deadline)
        plan_binding = _observe_plan(body, deadline)
        catalog_binding = _observe_catalog(body, deadline)
        _deadline(deadline)
        checked_at = time.time()
        _require(cases._timestamp(checked_at), "io_failed", "Revalidation clock is unsupported")
        entries = []
        for previous, member in zip(body["plan_snapshot"]["entries"], body["plan_source"]["members"]):
            _deadline(deadline)
            observation = retention._observe_member(member, deadline)
            _deadline(deadline)
            reasons = _expected_reasons(member["case"], observation, checked_at, body["plan_snapshot"]["policy"]["retain_seconds"])
            current = {"index": member["index"], "case": member["case"], "observation": observation,
                       "disposition": "held" if reasons else "review_candidate", "reasons": reasons}
            entries.append({"current": current, **_expected_comparison(previous, current)})
        unchanged = (plan_binding["state"] == catalog_binding["state"] == "unchanged"
                     and all(entry["observations_equal"] and entry["classification_equal"] and entry["observations_verifiable"] for entry in entries))
        result = _public_revalidation({"rules_version": RULES_VERSION, "review": _public(body), "checked_at": checked_at,
                                       "plan_binding": plan_binding, "catalog_binding": catalog_binding, "entries": entries,
                                       "observed_basis_unchanged": unchanged, **FLAGS})
        _deadline(deadline)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_io_failed", "Current review basis could not be observed") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_review_"):
        return error
    if error.code.endswith("limit_exceeded"):
        return WorkbenchError("workspace_restore_case_retention_review_limit_exceeded", "Review exceeded a supported bound")
    if error.code.endswith("private_path_required"):
        return WorkbenchError("workspace_restore_case_retention_review_private_path_required", "Review requires private owner controls")
    if error.code.endswith("invalid_input"):
        return WorkbenchError("workspace_restore_case_retention_review_invalid_input", "Review input is unsupported")
    if error.code.endswith("incomplete"):
        return WorkbenchError("workspace_restore_case_retention_review_incomplete", "A required review control is unavailable")
    return WorkbenchError("workspace_restore_case_retention_review_record_invalid", "Review record or captured input cannot be validated")
