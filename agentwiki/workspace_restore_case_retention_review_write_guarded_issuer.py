"""Exact local issuance policy for fresh credential-required guarded jobs.

The issuer authorizes creation through this managed path. Its revocation stops
future issuance, while each native guarded job retains its independent holder
credential and revocation rules. Native Guard11 and Issuance16 are separate
durable records: failure after the former may leave a usable active guard.
Existing jobs and incomplete issuance controls are never adopted or repaired.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import hmac
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import time
from typing import Any, Iterator

from . import store_backup
from . import workspace_restore_case_retention_review_write_guarded as guarded
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_write_guarded_issuer_cli import (
    _public_issuer, _public_show, _public_issuance, _public_policy,
)


RULES_VERSION = "workspace-restore-case-retention-review-write-guarded-issuer-1"
ISSUER_NAME = ".agentwiki-restore-case-retention-review-write-guarded-issuer.json"
ISSUER_LOCK_NAME = ".agentwiki-restore-case-retention-review-write-guarded-issuer.lock"
REVOCATION_NAME = ".agentwiki-restore-case-retention-review-write-guarded-issuer.revoked.json"
ISSUANCE_NAME = ".agentwiki-restore-case-retention-review-write-guarded-issuance.json"
ISSUANCE_LOCK_NAME = ".agentwiki-restore-case-retention-review-write-guarded-issuance.lock"
MAX_ISSUER_BYTES = 64 * 1024
MAX_ISSUANCE_BYTES = 64 * 1024
MAX_REVOCATION_BYTES = 64 * 1024
MAX_PUBLIC_BYTES = 64 * 1024
DEADLINE_SECONDS = guarded.DEADLINE_SECONDS
ISSUER_ID = re.compile(r"wrrwgir_[0-9a-f]{32}\Z")
ISSUANCE_ID = re.compile(r"wrrwgis_[0-9a-f]{32}\Z")
FLAGS = {
    "advisory_only": True, "inspection_only": True, "domain_writes_allowed": False,
    "original_files_modified": False, "archival_authorized": False, "deletion_authorized": False,
    "actor_authenticated": False, "old_authority_activated": False, "external_fencing_established": False,
    "local_capability_only": True, "external_identity_verified": False, "issuance_policy_only": True,
    "existing_guards_revoked": False, "guarded_step_credential_required": True, "os_isolation_established": False,
}
ISSUER_FIELDS = {"schema_version", "rules_version", "issuer_id", "operation_id", "created_at", "issuer_path",
                 "parent", "issuer_directory", "administrator", "credential_hash", "policy", "request_digest"}
REVOCATION_FIELDS = {"schema_version", "rules_version", "issuer_id", "operation_id", "revoked_at",
                     "grant_digest", "request_digest"}
ISSUANCE_FIELDS = {"schema_version", "rules_version", "issuance_id", "operation_id", "created_at", "issuance_path",
                   "parent", "issuance_directory", "issuer_anchor", "issuer", "rule", "guard_anchor", "guard",
                   "plan_id", "request", "request_digest"}
REQUEST_FIELDS = {"plan_path", "review_path", "job_path", "principal", "actor", "verdict", "holder_credential_hash"}
ISSUER_ANCHOR_FIELDS = {"issuer_path", "issuer_directory", "issuer_record", "issuer_lock"}
GUARD_ANCHOR_FIELDS = {"job_path", "job_directory", "guard_record", "guard_lock"}
writes, reviews, cases = guarded.writes, guarded.reviews, guarded.cases
_PREFIX = "workspace_restore_case_retention_review_write_guarded_issuer_"
_ERRORS = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError(_PREFIX + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Guarded issuer exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _bounded(value: Any, maximum: int, deadline: float) -> None:
    guarded._bounded(value, maximum, deadline)
    _deadline(deadline)


def _fresh(not_before: float = 0) -> float:
    current = time.time()
    _require(cases._timestamp(current), "io_failed", "Guarded issuer clock is unsupported")
    _require(current >= not_before, "clock_regressed", "Guarded issuer clock precedes its captured basis")
    return current


def _names(location: Path, *, issuance: bool = False) -> set[str]:
    names = cases._names(location, 2 if issuance else 3)
    required = {ISSUANCE_NAME, ISSUANCE_LOCK_NAME} if issuance else {ISSUER_NAME, ISSUER_LOCK_NAME}
    allowed = required if issuance else required | {REVOCATION_NAME}
    _require(required <= names <= allowed, "incomplete", "Guarded issuer controls are incomplete or unsupported")
    return names


@contextmanager
def _lock(location: Path, deadline: float, *, issuance: bool = False, create: bool = False) -> Iterator[None]:
    cases._directory(location)
    name = ISSUANCE_LOCK_NAME if issuance else ISSUER_LOCK_NAME
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(location / name, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError(_PREFIX + "incomplete", "Guarded issuer has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Guarded issuer needs a private empty regular owner lock")
        original = cases._full(details)
        if create:
            os.fsync(descriptor)
        while True:
            _deadline(deadline)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(0.025)
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((location / name).lstat()),
                 "record_invalid", "Guarded issuer coordination changed while locking")
        yield
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((location / name).lstat()),
                 "record_invalid", "Guarded issuer coordination changed during its operation")
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _issuer(body: dict[str, Any]) -> dict[str, Any]:
    return _public_issuer({"rules_version": RULES_VERSION,
                           **{key: body[key] for key in ("issuer_id", "operation_id", "created_at", "administrator", "policy")},
                           **FLAGS})


def _show(body: dict[str, Any], revocation: dict[str, Any] | None) -> dict[str, Any]:
    public = None if revocation is None else {
        "rules_version": RULES_VERSION,
        **{key: revocation[key] for key in ("issuer_id", "operation_id", "revoked_at")}, "reason": "administrator_revoked",
    }
    return _public_show({"rules_version": RULES_VERSION, "issuer": _issuer(body),
                         "state": "active" if revocation is None else "revoked", "revocation": public, **FLAGS})


def _issuance(body: dict[str, Any]) -> dict[str, Any]:
    return _public_issuance({"rules_version": RULES_VERSION,
                             **{key: body[key] for key in ("issuance_id", "operation_id", "created_at", "issuer", "rule", "guard", "plan_id")},
                             "policy_authorized": True, **FLAGS})


def _hash(credential: Any) -> str:
    return guarded._credential_hash(credential)


def _authorize(body: dict[str, Any], credential: Any) -> None:
    _require(hmac.compare_digest(_hash(credential), body["credential_hash"]),
             "credential_invalid", "Local administrator bearer does not match this guarded issuer")


def _read_control(path: Path, maximum: int, deadline: float) -> dict[str, Any]:
    return guarded._read_control(path, maximum, deadline)


def _read_issuer(issuer: Path, deadline: float) -> dict[str, Any]:
    _names(issuer)
    body = _read_control(issuer / ISSUER_NAME, MAX_ISSUER_BYTES, deadline)
    _require(set(body) == ISSUER_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["issuer_id"], str)
             and ISSUER_ID.fullmatch(body["issuer_id"]) and cases.jobs._operation(body["operation_id"])
             and cases.jobs._operation(body["administrator"]) and cases._timestamp(body["created_at"])
             and body["issuer_path"] == str(issuer) and cases._pin_valid(body["parent"])
             and cases._pin_valid(body["issuer_directory"]) and isinstance(body["credential_hash"], str)
             and cases.jobs.HASH.fullmatch(body["credential_hash"]) and isinstance(body["request_digest"], str)
             and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Guarded issuer has unsupported immutable fields")
    _require(_same(cases._directory(issuer.parent), body["parent"])
             and _same(cases._directory(issuer), body["issuer_directory"]),
             "record_invalid", "Guarded issuer differs from its fixed directory identities")
    try:
        public = _issuer(body)
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "record_invalid", "Guarded issuer has an unsupported saved policy") from error
    _bounded(body, MAX_ISSUER_BYTES, deadline)
    _bounded(public, MAX_PUBLIC_BYTES, deadline)
    return body


def _read_revocation(issuer: Path, body: dict[str, Any], deadline: float) -> dict[str, Any] | None:
    if REVOCATION_NAME not in _names(issuer):
        return None
    record = _read_control(issuer / REVOCATION_NAME, MAX_REVOCATION_BYTES, deadline)
    _require(set(record) == REVOCATION_FIELDS and type(record["schema_version"]) is int and record["schema_version"] == 1
             and record["rules_version"] == RULES_VERSION and record["issuer_id"] == body["issuer_id"]
             and cases.jobs._operation(record["operation_id"]) and cases._timestamp(record["revoked_at"])
             and record["revoked_at"] >= body["created_at"] and record["grant_digest"] == body["request_digest"]
             and isinstance(record["request_digest"], str) and cases.jobs.HASH.fullmatch(record["request_digest"])
             and digest({key: value for key, value in record.items() if key != "request_digest"}) == record["request_digest"],
             "record_invalid", "Guarded issuer revocation differs from its immutable authority")
    _bounded(_show(body, record), MAX_PUBLIC_BYTES, deadline)
    return record


def _issuer_pins(issuer: Path, body: dict[str, Any], deadline: float) -> dict[str, Any]:
    raw, record = cases._bytes(issuer / ISSUER_NAME, MAX_ISSUER_BYTES, deadline)
    _, lock = cases._bytes(issuer / ISSUER_LOCK_NAME, 0, deadline)
    _require(raw == canonical(body).encode("utf-8"), "source_changed", "Guarded issuer changed while capturing its authority")
    return {"issuer_path": str(issuer), "issuer_directory": cases._directory(issuer),
            "issuer_record": record, "issuer_lock": lock}


def _guard_pins(job: Path, body: dict[str, Any], deadline: float) -> dict[str, Any]:
    raw, record = cases._bytes(job / guarded.INTENT_NAME, guarded.MAX_INTENT_BYTES, deadline)
    _, lock = cases._bytes(job / guarded.LOCK_NAME, 0, deadline)
    _require(raw == canonical(body).encode("utf-8"), "source_changed", "New native guard changed before its issuance receipt")
    return {"job_path": str(job), "job_directory": cases._directory(job), "guard_record": record, "guard_lock": lock}


def _empty_lock(pin: Any) -> bool:
    return (cases._pin_valid(pin, full=True, file=True) and pin["size"] == 0
            and pin["sha256"] == hashlib.sha256(b"").hexdigest())


def _anchor_valid(anchor: Any, *, issuer: bool) -> bool:
    fields = ISSUER_ANCHOR_FIELDS if issuer else GUARD_ANCHOR_FIELDS
    directory = "issuer_directory" if issuer else "job_directory"
    record = "issuer_record" if issuer else "guard_record"
    lock = "issuer_lock" if issuer else "guard_lock"
    maximum = MAX_ISSUER_BYTES if issuer else guarded.MAX_INTENT_BYTES
    return (isinstance(anchor, dict) and set(anchor) == fields
            and cases._pin_valid(anchor[directory]) and cases._pin_valid(anchor[record], full=True, file=True)
            and 1 < anchor[record]["size"] <= maximum and _empty_lock(anchor[lock]))


def _read_issuance(issuance: Path, deadline: float) -> dict[str, Any]:
    """Validate own receipt and opaque anchors without reading referenced paths."""
    _names(issuance, issuance=True)
    body = _read_control(issuance / ISSUANCE_NAME, MAX_ISSUANCE_BYTES, deadline)
    _require(set(body) == ISSUANCE_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["issuance_id"], str)
             and ISSUANCE_ID.fullmatch(body["issuance_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["issuance_path"] == str(issuance)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["issuance_directory"])
             and _anchor_valid(body["issuer_anchor"], issuer=True) and _anchor_valid(body["guard_anchor"], issuer=False)
             and isinstance(body["request"], dict) and set(body["request"]) == REQUEST_FIELDS
             and all(cases.jobs._operation(body["request"][key]) for key in ("principal", "actor"))
             and isinstance(body["request"]["verdict"], str) and body["request"]["verdict"] in reviews.VERDICTS
             and isinstance(body["request"]["holder_credential_hash"], str)
             and cases.jobs.HASH.fullmatch(body["request"]["holder_credential_hash"])
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Guarded issuance has unsupported immutable fields")
    issuer = cases._path(body["issuer_anchor"]["issuer_path"])
    job = cases._path(body["guard_anchor"]["job_path"])
    plan, review = (cases._path(body["request"][key]) for key in ("plan_path", "review_path"))
    paths = (issuer, issuance, plan, review, job)
    _require(str(issuer) == body["issuer_anchor"]["issuer_path"]
             and str(job) == body["guard_anchor"]["job_path"] == body["request"]["job_path"]
             and str(plan) == body["request"]["plan_path"] and str(review) == body["request"]["review_path"]
             and len(set(paths)) == 5 and all(path.parent == issuance.parent for path in paths)
             and _same(cases._directory(issuance.parent), body["parent"])
             and _same(cases._directory(issuance), body["issuance_directory"]),
             "record_invalid", "Guarded issuance differs from its own fixed sibling bindings")
    try:
        public = _issuance(body)
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "record_invalid", "Guarded issuance has unsupported saved public scope") from error
    _require(body["request"]["principal"] == public["guard"]["principal"]
             and all(body["request"][key] == public["guard"]["write"][key] for key in ("actor", "verdict")),
             "record_invalid", "Guarded issuance request differs from its captured native scope")
    _bounded(body, MAX_ISSUANCE_BYTES, deadline)
    _bounded(public, MAX_PUBLIC_BYTES, deadline)
    return body


def _create_replay(issuer: Path, *, operation_id: str, administrator: str, credential_hash: str,
                   policy: list[dict[str, Any]], deadline: float) -> dict[str, Any]:
    with _lock(issuer, deadline):
        body = _read_issuer(issuer, deadline)
        _read_revocation(issuer, body, deadline)
        _require(body["operation_id"] == operation_id and body["administrator"] == administrator
                 and hmac.compare_digest(body["credential_hash"], credential_hash) and _same(body["policy"], policy),
                 "conflict", "Guarded issuer is bound to another exact creation request")
        result = _issuer(body)
        _bounded(result, MAX_PUBLIC_BYTES, deadline)
        return result


def create_guarded_review_write_issuer(issuer_directory: str | Path, *, operation_id: Any, administrator: Any,
                                      credential: Any, policy: Any) -> dict[str, Any]:
    """Bootstrap one immutable owner-trusted local issuer and exact policy."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        issuer = cases._path(issuer_directory)
        _require(cases.jobs._operation(operation_id) and cases.jobs._operation(administrator),
                 "invalid_input", "Guarded issuer needs bounded operation and administrator identifiers")
        secret_hash = _hash(credential)
        _bounded(policy, MAX_ISSUER_BYTES, deadline)
        try:
            fixed_policy = _public_policy(policy)
        except WorkbenchError as error:
            raise WorkbenchError(_PREFIX + "invalid_input", "Guarded issuer needs a bounded unique exact scope policy") from error
        cases._directory(issuer.parent)
        generated = [issuer / name for name in (ISSUER_NAME, ISSUER_LOCK_NAME, REVOCATION_NAME)]
        _require(all(len(str(path).encode("utf-8")) <= 4096 for path in generated),
                 "invalid_input", "A generated guarded issuer path exceeds its bound")
        if os.path.lexists(issuer):
            return _create_replay(issuer, operation_id=operation_id, administrator=administrator,
                                  credential_hash=secret_hash, policy=fixed_policy, deadline=deadline)
        try:
            issuer.mkdir(mode=0o700)
        except FileExistsError:
            return _create_replay(issuer, operation_id=operation_id, administrator=administrator,
                                  credential_hash=secret_hash, policy=fixed_policy, deadline=deadline)
        issuer.chmod(0o700)
        with _lock(issuer, deadline, create=True):
            body = {"schema_version": 1, "rules_version": RULES_VERSION, "issuer_id": "wrrwgir_" + secrets.token_hex(16),
                    "operation_id": operation_id, "created_at": _fresh(), "issuer_path": str(issuer),
                    "parent": cases._directory(issuer.parent), "issuer_directory": cases._directory(issuer),
                    "administrator": administrator, "credential_hash": secret_hash, "policy": fixed_policy}
            body["request_digest"] = digest(body)
            _bounded(body, MAX_ISSUER_BYTES, deadline)
            _bounded(_issuer(body), MAX_PUBLIC_BYTES, deadline)
            store_backup._write_json(issuer / ISSUER_NAME, body, MAX_ISSUER_BYTES)
            store_backup._fsync_directory(issuer)
            store_backup._fsync_directory(issuer.parent)
            _names(issuer)
            result = _issuer(body)
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded issuer could not be created") from error


def inspect_guarded_review_write_issuer(issuer_directory: str | Path) -> dict[str, Any]:
    """Observe own policy and revocation without reading native child jobs."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        issuer = cases._path(issuer_directory)
        with _lock(issuer, deadline):
            body = _read_issuer(issuer, deadline)
            result = _show(body, _read_revocation(issuer, body, deadline))
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded issuer could not be inspected") from error


def revoke_guarded_review_write_issuer(issuer_directory: str | Path, *, operation_id: Any,
                                      credential: Any) -> dict[str, Any]:
    """Stop future managed issuance; already created guards remain independent."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        issuer = cases._path(issuer_directory)
        _require(cases.jobs._operation(operation_id), "invalid_input", "Guarded issuer revoke needs a bounded operation")
        with _lock(issuer, deadline):
            body = _read_issuer(issuer, deadline)
            revocation = _read_revocation(issuer, body, deadline)
            _authorize(body, credential)
            if revocation is not None:
                _require(revocation["operation_id"] == operation_id, "conflict", "Guarded issuer revocation has another operation")
                result = _show(body, revocation)
                _bounded(result, MAX_PUBLIC_BYTES, deadline)
                return result
            record = {"schema_version": 1, "rules_version": RULES_VERSION, "issuer_id": body["issuer_id"],
                      "operation_id": operation_id, "revoked_at": _fresh(body["created_at"]), "grant_digest": body["request_digest"]}
            record["request_digest"] = digest(record)
            _bounded(record, MAX_REVOCATION_BYTES, deadline)
            _bounded(_show(body, record), MAX_PUBLIC_BYTES, deadline)
            store_backup._write_json(issuer / REVOCATION_NAME, record, MAX_REVOCATION_BYTES)
            store_backup._fsync_directory(issuer)
            result = _show(body, record)
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded issuer could not be revoked") from error


def _capture(issuer: Path, issuance: Path, plan: Path, review: Path, job: Path,
             deadline: float) -> dict[str, Any]:
    captured = reviews._capture_plan(plan, review, deadline)
    for location in (issuer, issuance, job):
        writes._source_separate(captured, location)
    _deadline(deadline)
    return captured


def _match(body: dict[str, Any], principal: str, actor: str, verdict: str, plan_id: str) -> dict[str, Any]:
    matches = [rule for rule in body["policy"] if rule["principal"] == principal and rule["actor"] == actor
               and rule["verdict"] == verdict and rule["plan_id"] == plan_id]
    _require(len(matches) == 1, "policy_denied", "Guarded issuer policy does not allow this exact review scope")
    return matches[0]


@contextmanager
def _new_guard(issuer: Path, issuance: Path, plan: Path, review: Path, job: Path,
               issuer_body: dict[str, Any], preliminary: dict[str, Any], *, operation_id: str,
               principal: str, actor: str, verdict: str, credential_hash: str,
               deadline: float) -> Iterator[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]]:
    """Reserve only a fresh native guard and retain job/review locks after fsync.

    A caller failing after this yield may leave an active durable native guard.
    The separately sealed issuance receipt is intentionally not a transaction.
    """
    try:
        job.mkdir(mode=0o700)
    except FileExistsError:
        raise WorkbenchError(_PREFIX + "conflict", "Managed issuance cannot adopt an existing native job") from None
    job.chmod(0o700)
    with guarded._lock(job, deadline, create=True):
        try:
            review.mkdir(mode=0o700)
        except FileExistsError:
            raise WorkbenchError(_PREFIX + "conflict", "Managed review destination was occupied during reservation") from None
        review.chmod(0o700)
        with reviews._lock(review, deadline, create=True):
            guarded._sync_review_lock(review, deadline)
            captured = _capture(issuer, issuance, plan, review, job, deadline)
            _require(_same(captured, preliminary), "source_changed", "Plan changed during managed guarded reservation")
            rule = _match(issuer_body, principal, actor, verdict, captured["plan_snapshot"]["plan_id"])
            created_at = _fresh(max(issuer_body["created_at"], captured["plan_snapshot"]["created_at"]))
            record = {"schema_version": 1, "rules_version": reviews.RULES_VERSION,
                      "review_id": "wrrv_" + secrets.token_hex(16), "operation_id": operation_id,
                      "created_at": created_at, "review_path": str(review),
                      "parent": cases._directory(job.parent), "review_directory": cases._directory(review),
                      "actor": actor, "verdict": verdict, **captured}
            record["request_digest"] = digest(record)
            writes._record_valid(record, review, record["parent"], deadline)
            descriptor = os.open(job / guarded.STAGE_NAME,
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
            try:
                os.fchmod(descriptor, 0o600)
                os.fsync(descriptor)
                stage_pin = cases._static(os.fstat(descriptor))
            finally:
                os.close(descriptor)
            _, review_lock = cases._bytes(review / reviews.LOCK_NAME, 0, deadline)
            nested = {"schema_version": 1, "rules_version": writes.RULES_VERSION,
                      "write_id": "wrrw_" + secrets.token_hex(16), "operation_id": operation_id,
                      "created_at": created_at, "job_path": str(job), "parent": record["parent"],
                      "job_directory": cases._directory(job), "review_path": str(review),
                      "review_lock": review_lock, "review_record": record, "stage_pin": stage_pin}
            nested["request_digest"] = digest(nested)
            writes._intent_valid(nested, job, deadline)
            body = {"schema_version": 1, "rules_version": guarded.RULES_VERSION,
                    "guard_id": "wrrwg_" + secrets.token_hex(16), "operation_id": operation_id,
                    "created_at": created_at, "job_path": str(job), "job_directory": nested["job_directory"],
                    "principal": principal, "credential_hash": credential_hash, "write": nested}
            body["request_digest"] = digest(body)
            _bounded(body, guarded.MAX_INTENT_BYTES, deadline)
            public = guarded._guard(body, deadline)
            _deadline(deadline)
            store_backup._write_json(job / guarded.INTENT_NAME, body, guarded.MAX_INTENT_BYTES)
            store_backup._fsync_directory(job)
            store_backup._fsync_directory(review)
            store_backup._fsync_directory(job.parent)
            # Both native locks remain held while the caller seals Issuance16.
            _require(_same(guarded._read_intent(job, deadline), body), "source_changed",
                     "New native guard differs from its saved immutable record")
            yield body, public, rule
            _deadline(deadline)


def _issue_replay(issuer: Path, issuance: Path, issuer_body: dict[str, Any], *, operation_id: str,
                  request: dict[str, Any], deadline: float) -> dict[str, Any]:
    with _lock(issuance, deadline, issuance=True):
        body = _read_issuance(issuance, deadline)
        _require(body["operation_id"] == operation_id
                 and all(body["request"][key] == request[key] for key in REQUEST_FIELDS - {"holder_credential_hash"})
                 and hmac.compare_digest(body["request"]["holder_credential_hash"], request["holder_credential_hash"])
                 and _same(body["issuer_anchor"], _issuer_pins(issuer, issuer_body, deadline))
                 and _same(body["issuer"], _issuer(issuer_body)),
                 "conflict", "Guarded issuance is bound to another exact managed request")
        result = _issuance(body)
        _bounded(result, MAX_PUBLIC_BYTES, deadline)
        return result


def issue_guarded_review_write(issuer_directory: str | Path, issuance_directory: str | Path,
                               plan_directory: str | Path, review_directory: str | Path, job_directory: str | Path, *,
                               operation_id: Any, actor: Any, verdict: Any, principal: Any,
                               issuer_credential: Any, credential: Any) -> dict[str, Any]:
    """Create one fresh native guarded job and a separate exact policy receipt."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        issuer, issuance, plan, review, job = (cases._path(path) for path in
                                              (issuer_directory, issuance_directory, plan_directory,
                                               review_directory, job_directory))
        paths = (issuer, issuance, plan, review, job)
        writes._inputs(plan, review, job, operation_id, actor, verdict)
        _require(len(set(paths)) == 5 and all(path.parent == issuer.parent for path in paths)
                 and cases.jobs._operation(principal),
                 "invalid_input", "Managed guarded issuance needs five distinct siblings and bounded identifiers")
        generated = [issuance / name for name in (ISSUANCE_NAME, ISSUANCE_LOCK_NAME)]
        generated.extend(job / name for name in (guarded.INTENT_NAME, guarded.LOCK_NAME, guarded.REVOCATION_NAME,
                                               *guarded.RESULT_NAMES.values()))
        _require(all(len(str(path).encode("utf-8")) <= 4096 for path in generated),
                 "invalid_input", "A generated managed guarded path exceeds its bound")
        with _lock(issuer, deadline):
            issuer_body = _read_issuer(issuer, deadline)
            revocation = _read_revocation(issuer, issuer_body, deadline)
            _authorize(issuer_body, issuer_credential)
            holder_hash = _hash(credential)
            _require(not hmac.compare_digest(holder_hash, issuer_body["credential_hash"]),
                     "invalid_input", "Guarded issuer administrator and holder need separate local credentials")
            request = {"plan_path": str(plan), "review_path": str(review), "job_path": str(job),
                       "principal": principal, "actor": actor, "verdict": verdict, "holder_credential_hash": holder_hash}
            if os.path.lexists(issuance):
                return _issue_replay(issuer, issuance, issuer_body, operation_id=operation_id, request=request, deadline=deadline)
            _require(revocation is None, "revoked", "Guarded issuer has been revoked for future managed issuance")
            _require(not os.path.lexists(job) and not os.path.lexists(review),
                     "conflict", "New managed issuance cannot adopt an existing job or review")
            preliminary = _capture(issuer, issuance, plan, review, job, deadline)
            _match(issuer_body, principal, actor, verdict, preliminary["plan_snapshot"]["plan_id"])
            try:
                issuance.mkdir(mode=0o700)
            except FileExistsError:
                return _issue_replay(issuer, issuance, issuer_body, operation_id=operation_id, request=request, deadline=deadline)
            issuance.chmod(0o700)
            with _lock(issuance, deadline, issuance=True, create=True):
                with _new_guard(issuer, issuance, plan, review, job, issuer_body, preliminary,
                                operation_id=operation_id, principal=principal, actor=actor, verdict=verdict,
                                credential_hash=holder_hash, deadline=deadline) as (native, native_public, rule):
                    body = {"schema_version": 1, "rules_version": RULES_VERSION,
                            "issuance_id": "wrrwgis_" + secrets.token_hex(16), "operation_id": operation_id,
                            "created_at": _fresh(native["created_at"]), "issuance_path": str(issuance),
                            "parent": cases._directory(issuance.parent), "issuance_directory": cases._directory(issuance),
                            "issuer_anchor": _issuer_pins(issuer, issuer_body, deadline), "issuer": _issuer(issuer_body),
                            "rule": rule, "guard_anchor": _guard_pins(job, native, deadline), "guard": native_public,
                            "plan_id": native["write"]["review_record"]["plan_snapshot"]["plan_id"], "request": request}
                    body["request_digest"] = digest(body)
                    _bounded(body, MAX_ISSUANCE_BYTES, deadline)
                    _bounded(_issuance(body), MAX_PUBLIC_BYTES, deadline)
                    store_backup._write_json(issuance / ISSUANCE_NAME, body, MAX_ISSUANCE_BYTES)
                    store_backup._fsync_directory(issuance)
                    store_backup._fsync_directory(issuance.parent)
                    _require(_same(_read_issuance(issuance, deadline), body), "source_changed",
                             "Guarded issuance differs from its saved immutable record")
                    result = _issuance(body)
                    _bounded(result, MAX_PUBLIC_BYTES, deadline)
                    return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Managed guarded review write could not be issued") from error


def inspect_guarded_review_write_issuance(issuance_directory: str | Path) -> dict[str, Any]:
    """Read complete own historical receipt without issuer or live native job."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        issuance = cases._path(issuance_directory)
        with _lock(issuance, deadline, issuance=True):
            result = _issuance(_read_issuance(issuance, deadline))
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded issuance receipt could not be inspected") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith(_PREFIX):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict",
                   "credential_invalid", "revoked", "clock_regressed", "io_failed"):
        if error.code.endswith(suffix):
            return WorkbenchError(_PREFIX + suffix, "Guarded issuer exceeded a bound or has unsupported private bindings")
    return WorkbenchError(_PREFIX + "record_invalid", "Guarded issuer controls or historical receipt cannot be validated")
