"""Credential-required review writes in a distinct owned control family.

These jobs never contain the legacy write intent or lock. Supported legacy
write APIs therefore cannot advance them. The filesystem owner, private
helpers and arbitrary syscalls remain outside this local capability boundary.
Saved authenticated deliveries describe historical observations, not a fresh
phase claim or authentication of the effect that produced that phase.
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

from . import governance, obligations, store_backup
from . import workspace_restore_case_retention_review_write as writes
from . import workspace_restore_case_retention_review_write_catalog as catalogs
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_cli import _public_review
from .workspace_restore_case_retention_review_write_cli import _public_status as _public_write_status
from .workspace_restore_case_retention_review_write_guarded_cli import (
    _public_access, _public_execution, _public_guard, _public_status,
)


RULES_VERSION = "workspace-restore-case-retention-review-write-guarded-1"
INTENT_NAME = ".agentwiki-restore-case-retention-review-write-guarded.json"
LOCK_NAME = ".agentwiki-restore-case-retention-review-write-guarded.lock"
REVOCATION_NAME = ".agentwiki-restore-case-retention-review-write-guarded.revoked.json"
STAGE_NAME = writes.STAGE_NAME
RESULT_NAMES = {
    state: ".agentwiki-restore-case-retention-review-write-guarded." + state + ".result.json"
    for state in ("prepared", "ready", "linked")
}
TARGETS = {"prepared": "ready", "ready": "linked", "linked": "completed"}
MAX_INTENT_BYTES = 16 * 1024 * 1024
MAX_STATUS_BYTES = 16 * 1024 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_REVOCATION_BYTES = 64 * 1024
MAX_PUBLIC_BYTES = 64 * 1024
DEADLINE_SECONDS = writes.DEADLINE_SECONDS
GUARD_ID = re.compile(r"wrrwg_[0-9a-f]{32}\Z")
FLAGS = {
    **writes.FLAGS,
    "local_capability_only": True,
    "external_identity_verified": False,
    "legacy_write_apis_rejected": True,
    "lease_enforced": False,
    "issuer_policy_enforced": False,
    "os_isolation_established": False,
    "credential_required_for_step": True,
}
INTENT_FIELDS = {
    "schema_version", "rules_version", "guard_id", "operation_id", "created_at",
    "job_path", "job_directory", "principal", "credential_hash", "write", "request_digest",
}
RESULT_FIELDS = {"schema_version", "rules_version", "guard_id", "intent_digest", "observation", "result_digest"}
OBSERVATION_FIELDS = {
    "rules_version", "guard_id", "operation_id", "expected_state", "target_state",
    "recorded_at", "outcome", "observed_state", "effect_performed",
}
REVOCATION_FIELDS = {
    "schema_version", "rules_version", "guard_id", "operation_id", "revoked_at", "grant_digest", "request_digest",
}
reviews, cases = writes.reviews, writes.cases
_ERRORS = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError)
_PREFIX = "workspace_restore_case_retention_review_write_guarded_"


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError(_PREFIX + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Guarded write exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _bounded(value: Any, maximum: int, deadline: float) -> None:
    _deadline(deadline)
    try:
        obligations._shape(value)
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "limit_exceeded", "Guarded write JSON structure exceeds its bound") from error
    _require(len(canonical(value).encode("utf-8")) <= maximum,
             "limit_exceeded", "Guarded write JSON exceeds its byte bound")
    _deadline(deadline)


def _credential_hash(credential: Any) -> str:
    _require(isinstance(credential, str) and governance.TOKEN.fullmatch(credential) is not None,
             "credential_invalid", "A valid local bearer credential is required")
    return hashlib.sha256(credential.encode("ascii")).hexdigest()


def _authorize(body: dict[str, Any], credential: Any) -> None:
    _require(hmac.compare_digest(_credential_hash(credential), body["credential_hash"]),
             "credential_invalid", "Local bearer credential does not match this guarded write")


def _names(job: Path) -> set[str]:
    names = cases._names(job, 7)
    allowed = {INTENT_NAME, LOCK_NAME, STAGE_NAME, REVOCATION_NAME, *RESULT_NAMES.values()}
    _require({INTENT_NAME, LOCK_NAME} <= names, "incomplete", "Guarded write has incomplete controls")
    _require(names <= allowed and writes.INTENT_NAME not in names and writes.LOCK_NAME not in names,
             "record_invalid", "Guarded write has unsupported controls")
    return names


@contextmanager
def _lock(job: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(job)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(job / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError(_PREFIX + "incomplete", "Guarded write has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Guarded write needs a private empty regular owner lock")
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
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((job / LOCK_NAME).lstat()),
                 "record_invalid", "Guarded coordination changed while locking")
        yield
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((job / LOCK_NAME).lstat()),
                 "record_invalid", "Guarded coordination changed during its operation")
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _read_control(path: Path, maximum: int, deadline: float) -> dict[str, Any]:
    """Read a bounded stable owner file before decoding its canonical JSON."""
    raw, _ = cases._bytes(path, maximum, deadline)
    try:
        body = obligations._strict_json(raw.decode("utf-8"))
    except (WorkbenchError, UnicodeError, ValueError, TypeError, RecursionError) as error:
        raise WorkbenchError(_PREFIX + "record_invalid", "Guarded control is not valid JSON") from error
    _bounded(body, maximum, deadline)
    _require(isinstance(body, dict) and raw == canonical(body).encode("utf-8"),
             "record_invalid", "Guarded control is not its canonical immutable record")
    return body


def _sync_review_lock(review: Path, deadline: float) -> None:
    """Durably reserve the newly created legacy review lock while holding it."""
    path = review / reviews.LOCK_NAME
    with store_backup._regular_file(path, maximum=0) as descriptor:
        original = cases._full(os.fstat(descriptor))
        _require(original["size"] == 0, "record_invalid", "Reserved review lock is not empty")
        _deadline(deadline)
        os.fsync(descriptor)
        _require(original == cases._full(os.fstat(descriptor)) == cases._full(path.lstat()),
                 "record_invalid", "Reserved review lock changed during synchronization")
    _deadline(deadline)


def _guard(body: dict[str, Any], deadline: float) -> dict[str, Any]:
    result = _public_guard({"rules_version": RULES_VERSION,
                            **{key: body[key] for key in ("guard_id", "operation_id", "created_at", "principal")},
                            "write": catalogs._write(body["write"]), **FLAGS})
    _bounded(result, MAX_PUBLIC_BYTES, deadline)
    return result


def _access(body: dict[str, Any], revocation: dict[str, Any] | None, deadline: float) -> dict[str, Any]:
    public = None if revocation is None else {
        "rules_version": RULES_VERSION,
        **{key: revocation[key] for key in ("guard_id", "operation_id", "revoked_at")},
        "reason": "holder_revoked",
    }
    result = _public_access({"rules_version": RULES_VERSION, "guard": _guard(body, deadline),
                             "access_state": "active" if revocation is None else "revoked",
                             "revocation": public, **FLAGS})
    _bounded(result, MAX_PUBLIC_BYTES, deadline)
    return result


def _execution(body: dict[str, Any], observation: dict[str, Any], deadline: float) -> dict[str, Any]:
    result = _public_execution({"rules_version": RULES_VERSION, "guard": _guard(body, deadline),
                                "observation": observation, "credential_verified": True,
                                "revocation_checked": True, "historical_effect_authenticated": False, **FLAGS})
    _bounded(result, MAX_PUBLIC_BYTES, deadline)
    return result


def _read_intent(job: Path, deadline: float) -> dict[str, Any]:
    _names(job)
    body = _read_control(job / INTENT_NAME, MAX_INTENT_BYTES, deadline)
    _require(set(body) == INTENT_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["guard_id"], str)
             and GUARD_ID.fullmatch(body["guard_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["job_path"] == str(job)
             and cases._pin_valid(body["job_directory"]) and cases.jobs._operation(body["principal"])
             and isinstance(body["credential_hash"], str) and cases.jobs.HASH.fullmatch(body["credential_hash"])
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Guarded write has unsupported immutable fields")
    nested = body["write"]
    writes._intent_valid(nested, job, deadline)
    _require(all(_same(body[key], nested[key]) for key in ("operation_id", "created_at", "job_path", "job_directory"))
             and body["created_at"] >= nested["review_record"]["plan_snapshot"]["created_at"],
             "record_invalid", "Guarded write differs from its captured write or plan clock")
    _guard(body, deadline)
    return body


def _read_revocation(job: Path, body: dict[str, Any], deadline: float) -> dict[str, Any] | None:
    if REVOCATION_NAME not in _names(job):
        return None
    record = _read_control(job / REVOCATION_NAME, MAX_REVOCATION_BYTES, deadline)
    _require(set(record) == REVOCATION_FIELDS and type(record["schema_version"]) is int and record["schema_version"] == 1
             and record["rules_version"] == RULES_VERSION and record["guard_id"] == body["guard_id"]
             and cases.jobs._operation(record["operation_id"]) and cases._timestamp(record["revoked_at"])
             and record["revoked_at"] >= body["created_at"] and record["grant_digest"] == body["request_digest"]
             and isinstance(record["request_digest"], str) and cases.jobs.HASH.fullmatch(record["request_digest"])
             and digest({key: value for key, value in record.items() if key != "request_digest"}) == record["request_digest"],
             "record_invalid", "Guarded revocation differs from its immutable grant")
    _access(body, record, deadline)
    return record


def _read_results(job: Path, body: dict[str, Any], deadline: float) -> dict[str, dict[str, Any]]:
    names, results, operations = _names(job), {}, set()
    last_at = body["created_at"]
    for expected_state, name in RESULT_NAMES.items():
        if name not in names:
            continue
        record = _read_control(job / name, MAX_RESULT_BYTES, deadline)
        _require(set(record) == RESULT_FIELDS and type(record["schema_version"]) is int and record["schema_version"] == 1
                 and record["rules_version"] == RULES_VERSION and record["guard_id"] == body["guard_id"]
                 and record["intent_digest"] == body["request_digest"]
                 and isinstance(record["result_digest"], str) and cases.jobs.HASH.fullmatch(record["result_digest"])
                 and digest({key: value for key, value in record.items() if key != "result_digest"}) == record["result_digest"],
                 "record_invalid", "Guarded result differs from its immutable intent")
        observation = record["observation"]
        target = TARGETS[expected_state]
        _require(isinstance(observation, dict) and set(observation) == OBSERVATION_FIELDS
                 and observation["rules_version"] == RULES_VERSION and observation["guard_id"] == body["guard_id"]
                 and cases.jobs._operation(observation["operation_id"])
                 and observation["expected_state"] == expected_state and observation["target_state"] == target
                 and observation["observed_state"] == target and cases._timestamp(observation["recorded_at"])
                 and observation["recorded_at"] >= last_at
                 and isinstance(observation["outcome"], str)
                 and observation["outcome"] in {"phase_performed", "expected_phase_observed"}
                 and type(observation["effect_performed"]) is bool
                 and observation["effect_performed"] == (observation["outcome"] == "phase_performed")
                 and observation["operation_id"] not in operations,
                 "record_invalid", "Guarded result has unsupported observation or operation bindings")
        _execution(body, observation, deadline)
        results[expected_state] = observation
        operations.add(observation["operation_id"])
        last_at = observation["recorded_at"]
    _deadline(deadline)
    return results


def _last_at(body: dict[str, Any], results: dict[str, dict[str, Any]]) -> float:
    return max([body["created_at"], *(value["recorded_at"] for value in results.values())])


def _revocation_clock(body: dict[str, Any], revocation: dict[str, Any] | None,
                      results: dict[str, dict[str, Any]]) -> None:
    _require(revocation is None or revocation["revoked_at"] >= _last_at(body, results),
             "record_invalid", "Guarded revocation precedes a saved observation")


def _fresh(not_before: float) -> float:
    current = time.time()
    _require(cases._timestamp(current), "io_failed", "Guarded write clock is unsupported")
    _require(current >= not_before, "clock_regressed", "Guarded write clock precedes its saved history")
    return current


def _status(body: dict[str, Any], deadline: float) -> dict[str, Any]:
    """Copy legacy inode checks while recognizing only the new job controls."""
    nested = body["write"]
    job, review = Path(body["job_path"]), Path(nested["review_path"])
    _require(_same(cases._directory(review), nested["review_record"]["review_directory"]),
             "conflict", "Reserved review directory differs from its captured identity")
    _, lock_pin = cases._bytes(review / reviews.LOCK_NAME, 0, deadline)
    _require(_same(lock_pin, nested["review_lock"]), "conflict", "Reserved review coordination changed")
    job_names, review_names = _names(job), cases._names(review, 2)
    _require({reviews.LOCK_NAME} <= review_names <= {reviews.LOCK_NAME, reviews.REVIEW_NAME},
             "conflict", "Reserved review directory has unsupported entries")
    stage, target = job / STAGE_NAME, review / reviews.REVIEW_NAME
    has_stage, has_target = STAGE_NAME in job_names, reviews.REVIEW_NAME in review_names
    expected = canonical(nested["review_record"]).encode("utf-8")
    if has_stage:
        stage_bytes, stage_details = writes._owned_bytes(stage, nested, expected, deadline, links=(1, 2))
        if has_target:
            target_bytes, target_details = writes._owned_bytes(target, nested, expected, deadline, links=(2,))
            _require(stage_details.st_nlink == target_details.st_nlink == 2 and stage_bytes == target_bytes == expected,
                     "conflict", "Linked review is not exactly the intended two-path inode")
            state = "linked"
        else:
            _require(stage_details.st_nlink == 1, "conflict", "Reserved staging has an unrecognized alias")
            state = "ready" if stage_bytes == expected else "staging" if stage_bytes else "prepared"
    else:
        _require(has_target, "conflict", "Guarded write has neither staging nor a completed review")
        target_bytes, _ = writes._owned_bytes(target, nested, expected, deadline)
        _require(target_bytes == expected and _same(reviews._read_review(review, deadline), nested["review_record"]),
                 "conflict", "Completed review differs from its exact intended history")
        state = "completed"
    intended = _public_review(reviews._public(nested["review_record"]))
    result = _public_write_status({"rules_version": writes.RULES_VERSION,
                                   **{key: nested[key] for key in ("write_id", "operation_id", "created_at")},
                                   "state": state, "completed": state == "completed", "intended_review": intended,
                                   "result": intended if state == "completed" else None, **writes.FLAGS})
    _bounded(result, MAX_STATUS_BYTES, deadline)
    return result


def _phase(body: dict[str, Any], state: str, deadline: float) -> None:
    """Perform one old file primitive with the new family's status checks."""
    nested = body["write"]
    job, review = Path(body["job_path"]), Path(nested["review_path"])
    if state in {"prepared", "staging"}:
        writes._fill_stage(nested, deadline)
    elif state == "ready":
        writes._sync_owned(job / STAGE_NAME, nested, deadline, links=(1,))
        _deadline(deadline)
        try:
            os.link(job / STAGE_NAME, review / reviews.REVIEW_NAME, follow_symlinks=False)
        except FileExistsError:
            raise WorkbenchError(_PREFIX + "conflict", "Guarded review refuses an occupied target") from None
        store_backup._fsync_directory(review)
        store_backup._fsync_directory(job)
    elif state == "linked":
        writes._sync_owned(review / reviews.REVIEW_NAME, nested, deadline, links=(2,))
        _deadline(deadline)
        store_backup._fsync_directory(review)
        _require(_status(body, deadline)["state"] == "linked", "conflict",
                 "Guarded alias changed before its proved staging removal")
        (job / STAGE_NAME).unlink()
        store_backup._fsync_directory(job)
        writes._sync_owned(review / reviews.REVIEW_NAME, nested, deadline, links=(1,))
        store_backup._fsync_directory(review)
    else:
        _require(False, "phase_changed", "Guarded write has no supported new phase")
    _deadline(deadline)


def _replay(plan: Path, review: Path, job: Path, operation_id: str, actor: str, verdict: str,
            principal: str, credential_hash: str, deadline: float) -> dict[str, Any]:
    with _lock(job, deadline):
        body = _read_intent(job, deadline)
        revocation = _read_revocation(job, body, deadline)
        results = _read_results(job, body, deadline)
        _revocation_clock(body, revocation, results)
        nested = body["write"]
        record = nested["review_record"]
        _require(body["operation_id"] == operation_id and body["principal"] == principal
                 and hmac.compare_digest(body["credential_hash"], credential_hash)
                 and nested["review_path"] == str(review) and record["plan_anchor"]["plan_path"] == str(plan)
                 and record["actor"] == actor and record["verdict"] == verdict,
                 "conflict", "Guarded write is bound to another exact request")
        return _guard(body, deadline)


def prepare_guarded_review_write(plan_directory: str | Path, review_directory: str | Path,
                                 job_directory: str | Path, *, operation_id: Any, actor: Any,
                                 verdict: Any, principal: Any, credential: Any) -> dict[str, Any]:
    """Reserve a fresh guarded family without creating legacy job controls."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        plan, review, job = (cases._path(path) for path in (plan_directory, review_directory, job_directory))
        writes._inputs(plan, review, job, operation_id, actor, verdict)
        _require(cases.jobs._operation(principal), "invalid_input", "Guarded principal must be a bounded identifier")
        credential_hash = _credential_hash(credential)
        generated = [job / name for name in (INTENT_NAME, LOCK_NAME, REVOCATION_NAME, *RESULT_NAMES.values())]
        _require(all(len(str(path).encode("utf-8")) <= 4096 for path in generated),
                 "invalid_input", "A generated guarded control path exceeds its bound")
        cases._directory(job.parent)
        if os.path.lexists(job):
            return _replay(plan, review, job, operation_id, actor, verdict, principal, credential_hash, deadline)
        preliminary = reviews._capture_plan(plan, review, deadline)
        writes._source_separate(preliminary, job)
        _require(not os.path.lexists(review), "conflict", "Guarded write needs a new unoccupied review directory")
        try:
            job.mkdir(mode=0o700)
        except FileExistsError:
            return _replay(plan, review, job, operation_id, actor, verdict, principal, credential_hash, deadline)
        job.chmod(0o700)
        with _lock(job, deadline, create=True):
            try:
                review.mkdir(mode=0o700)
            except FileExistsError:
                raise WorkbenchError(_PREFIX + "conflict", "Review destination was occupied during reservation") from None
            review.chmod(0o700)
            with reviews._lock(review, deadline, create=True):
                _sync_review_lock(review, deadline)
                captured = reviews._capture_plan(plan, review, deadline)
                writes._source_separate(captured, job)
                _require(_same(captured, preliminary), "source_changed", "Plan changed during guarded reservation")
                created_at = _fresh(captured["plan_snapshot"]["created_at"])
                record = {"schema_version": 1, "rules_version": reviews.RULES_VERSION,
                          "review_id": "wrrv_" + secrets.token_hex(16), "operation_id": operation_id,
                          "created_at": created_at, "review_path": str(review),
                          "parent": cases._directory(job.parent), "review_directory": cases._directory(review),
                          "actor": actor, "verdict": verdict, **captured}
                record["request_digest"] = digest(record)
                writes._record_valid(record, review, record["parent"], deadline)
                descriptor = os.open(job / STAGE_NAME,
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
                body = {"schema_version": 1, "rules_version": RULES_VERSION,
                        "guard_id": "wrrwg_" + secrets.token_hex(16), "operation_id": operation_id,
                        "created_at": created_at, "job_path": str(job), "job_directory": nested["job_directory"],
                        "principal": principal, "credential_hash": credential_hash, "write": nested}
                body["request_digest"] = digest(body)
                _bounded(body, MAX_INTENT_BYTES, deadline)
                result = _guard(body, deadline)
                _deadline(deadline)
                store_backup._write_json(job / INTENT_NAME, body, MAX_INTENT_BYTES)
                store_backup._fsync_directory(job)
                store_backup._fsync_directory(review)
                store_backup._fsync_directory(job.parent)
                _deadline(deadline)
                return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded write could not be prepared") from error


def inspect_guarded_review_write_access(job_directory: str | Path) -> dict[str, Any]:
    """Read own access controls without live review, plan or credential access."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        job = cases._path(job_directory)
        with _lock(job, deadline):
            body = _read_intent(job, deadline)
            revocation = _read_revocation(job, body, deadline)
            results = _read_results(job, body, deadline)
            _revocation_clock(body, revocation, results)
            return _access(body, revocation, deadline)
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded access could not be observed") from error


def inspect_guarded_review_write(job_directory: str | Path) -> dict[str, Any]:
    """Observe the current live phase under job then review coordination."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        job = cases._path(job_directory)
        with _lock(job, deadline):
            body = _read_intent(job, deadline)
            revocation = _read_revocation(job, body, deadline)
            results = _read_results(job, body, deadline)
            _revocation_clock(body, revocation, results)
            with reviews._lock(Path(body["write"]["review_path"]), deadline):
                result = _public_status({**_access(body, revocation, deadline), "write": _status(body, deadline)})
                _bounded(result, MAX_STATUS_BYTES, deadline)
                return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded write could not be observed") from error


def step_guarded_review_write(job_directory: str | Path, *, operation_id: Any,
                              expected_state: Any, credential: Any) -> dict[str, Any]:
    """Authenticate every delivery, then perform or observe one exact phase."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(cases.jobs._operation(operation_id) and isinstance(expected_state, str)
                 and expected_state in TARGETS, "invalid_input", "Guarded step needs a bounded operation and phase")
        job = cases._path(job_directory)
        with _lock(job, deadline):
            body = _read_intent(job, deadline)
            revocation = _read_revocation(job, body, deadline)
            _authorize(body, credential)
            _require(revocation is None, "revoked", "Guarded write capability has been revoked")
            # No result contents or live stage/review have been read before admission.
            results = _read_results(job, body, deadline)
            _require(all(value["operation_id"] != operation_id or state == expected_state
                         for state, value in results.items()),
                     "conflict", "Guarded operation is already bound to another phase")
            saved = results.get(expected_state)
            if saved is not None:
                _require(saved["operation_id"] == operation_id, "conflict", "Guarded phase already has another operation")
                return _execution(body, saved, deadline)
            target = TARGETS[expected_state]
            with reviews._lock(Path(body["write"]["review_path"]), deadline):
                current = _status(body, deadline)["state"]
                perform = current == expected_state or expected_state == "prepared" and current == "staging"
                _require(perform or current == target, "phase_changed", "Guarded phase is outside the requested PRE/POST pair")
                admitted_at = _fresh(_last_at(body, results))
                if perform:
                    _phase(body, current, deadline)
                observed = _status(body, deadline)["state"]
                _require(observed == target, "phase_changed", "Guarded write did not reach its exact requested target")
                recorded_at = _fresh(admitted_at)
                observation = {"rules_version": RULES_VERSION, "guard_id": body["guard_id"],
                               "operation_id": operation_id, "expected_state": expected_state,
                               "target_state": target, "recorded_at": recorded_at,
                               "outcome": "phase_performed" if perform else "expected_phase_observed",
                               "observed_state": observed, "effect_performed": bool(perform)}
                record = {"schema_version": 1, "rules_version": RULES_VERSION, "guard_id": body["guard_id"],
                          "intent_digest": body["request_digest"], "observation": observation}
                record["result_digest"] = digest(record)
                _bounded(record, MAX_RESULT_BYTES, deadline)
                _execution(body, observation, deadline)
                _deadline(deadline)
                store_backup._write_json(job / RESULT_NAMES[expected_state], record, MAX_RESULT_BYTES)
                store_backup._fsync_directory(job)
                return _execution(body, observation, deadline)
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded write phase could not complete") from error


def revoke_guarded_review_write(job_directory: str | Path, *, operation_id: Any,
                                credential: Any) -> dict[str, Any]:
    """Revoke future protected deliveries, serialized behind an admitted step."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(cases.jobs._operation(operation_id), "invalid_input", "Guarded revoke needs a bounded operation")
        job = cases._path(job_directory)
        with _lock(job, deadline):
            body = _read_intent(job, deadline)
            revocation = _read_revocation(job, body, deadline)
            _authorize(body, credential)
            results = _read_results(job, body, deadline)
            _revocation_clock(body, revocation, results)
            if revocation is not None:
                _require(revocation["operation_id"] == operation_id, "conflict", "Guarded revocation has another operation")
                return _access(body, revocation, deadline)
            record = {"schema_version": 1, "rules_version": RULES_VERSION, "guard_id": body["guard_id"],
                      "operation_id": operation_id, "revoked_at": _fresh(_last_at(body, results)),
                      "grant_digest": body["request_digest"]}
            record["request_digest"] = digest(record)
            _bounded(record, MAX_REVOCATION_BYTES, deadline)
            _access(body, record, deadline)
            _deadline(deadline)
            store_backup._write_json(job / REVOCATION_NAME, record, MAX_REVOCATION_BYTES)
            store_backup._fsync_directory(job)
            return _access(body, record, deadline)
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError(_PREFIX + "io_failed", "Guarded write capability could not be revoked") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith(_PREFIX):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict"):
        if error.code.endswith(suffix):
            return WorkbenchError(_PREFIX + suffix, "Guarded write exceeded a bound or has unsupported private bindings")
    return WorkbenchError(_PREFIX + "record_invalid", "Guarded controls or captured historical record cannot be validated")
