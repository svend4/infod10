"""Lease-checked, phase-bounded actions over an existing review write job.

An action captures opaque immutable bindings and one PRE/POST phase pair. New
execution requires the captured latest active lease while ledger/job/review
locks are held. A saved observation replays from own controls only. This module
neither writes lease history nor makes SQLite and file effects one transaction.
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

from . import store_backup
from . import workspace_restore_case_retention_review_write_leases as leases
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_write_action_cli import _public_action, _public_wrapper
from .workspace_restore_case_retention_review_write_catalog_cli import _public_catalog, _public_write
from .workspace_restore_case_retention_review_write_leases_cli import _public_operation


RULES_VERSION = "workspace-restore-case-retention-review-write-action-1"
INTENT_NAME = ".agentwiki-restore-case-retention-review-write-action.json"
LOCK_NAME = ".agentwiki-restore-case-retention-review-write-action.lock"
RESULT_NAME = ".agentwiki-restore-case-retention-review-write-action.result.json"
MAX_INTENT_BYTES = 64 * 1024
MAX_RESULT_BYTES = 64 * 1024
MAX_PUBLIC_BYTES = 64 * 1024
DEADLINE_SECONDS = leases.DEADLINE_SECONDS
ACTION_ID = re.compile(r"wrrwa_[0-9a-f]{32}\Z")
TARGETS = {"prepared": "ready", "staging": "ready", "ready": "linked", "linked": "completed"}
FLAGS = {**leases.FLAGS, "lease_required_for_new_execution": True}
INTENT_FIELDS = {"schema_version", "rules_version", "action_id", "operation_id", "created_at", "action_path",
                 "parent", "action_directory", "ledger_anchor", "catalog_anchor", "member", "lease",
                 "before_state", "target_state", "request_digest"}
LEDGER_ANCHOR_FIELDS = {"ledger_path", "ledger_directory", "ledger_manifest", "ledger_lock", "database_pin", "ledger_id"}
CATALOG_ANCHOR_FIELDS = {"catalog_path", "catalog_directory", "catalog_record", "catalog_lock", "catalog_request_digest", "catalog"}
RESULT_FIELDS = {"schema_version", "rules_version", "action_id", "intent_digest", "observation", "result_digest"}
catalogs, writes, cases = leases.catalogs, leases.catalogs.writes, leases.cases
_ERRORS = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_review_write_action_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Write action exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _bounded(value: Any, maximum: int, deadline: float) -> None:
    leases._bounded(value, maximum, deadline)
    _deadline(deadline)


def _names(action: Path) -> set[str]:
    names = cases._names(action, 3)
    _require({INTENT_NAME, LOCK_NAME} <= names <= {INTENT_NAME, LOCK_NAME, RESULT_NAME},
             "incomplete", "Write action has incomplete or unsupported controls")
    return names


@contextmanager
def _lock(action: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(action)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(action / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_retention_review_write_action_incomplete",
                             "Write action has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Write action needs a private empty regular owner lock")
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
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((action / LOCK_NAME).lstat()),
                 "record_invalid", "Write action coordination changed while locking")
        yield
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((action / LOCK_NAME).lstat()),
                 "record_invalid", "Write action coordination changed during its operation")
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _empty_lock(pin: Any) -> bool:
    return (cases._pin_valid(pin, full=True, file=True) and pin["size"] == 0
            and pin["sha256"] == hashlib.sha256(b"").hexdigest())


def _action(body: dict[str, Any]) -> dict[str, Any]:
    value = {"rules_version": RULES_VERSION,
             **{key: body[key] for key in ("action_id", "operation_id", "created_at", "before_state", "target_state")},
             "write": body["member"]["write"], "lease": body["lease"], **FLAGS}
    return _public_action(value)


def _wrapper(body: dict[str, Any], observation: Any = None) -> dict[str, Any]:
    return _public_wrapper({"rules_version": RULES_VERSION, "action": _action(body),
                            "state": "prepared" if observation is None else "recorded", "result": observation, **FLAGS})


def _read_intent(action: Path, deadline: float) -> dict[str, Any]:
    """Validate own metadata and opaque anchors without opening prior paths."""
    _deadline(deadline)
    _names(action)
    body = store_backup._read_json(action / INTENT_NAME, MAX_INTENT_BYTES)
    _require(set(body) == INTENT_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["action_id"], str)
             and ACTION_ID.fullmatch(body["action_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["action_path"] == str(action)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["action_directory"])
             and isinstance(body["before_state"], str) and body["before_state"] in TARGETS
             and body["target_state"] == TARGETS[body["before_state"]]
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Write action has unsupported immutable fields")
    ledger, catalog, member = body["ledger_anchor"], body["catalog_anchor"], body["member"]
    _require(isinstance(ledger, dict) and set(ledger) == LEDGER_ANCHOR_FIELDS
             and cases._pin_valid(ledger["ledger_directory"])
             and cases._pin_valid(ledger["ledger_manifest"], full=True, file=True)
             and 1 < ledger["ledger_manifest"]["size"] <= leases.MAX_MANIFEST_BYTES
             and _empty_lock(ledger["ledger_lock"]) and leases._database_pin_valid(ledger["database_pin"])
             and isinstance(ledger["ledger_id"], str) and leases.LEDGER_ID.fullmatch(ledger["ledger_id"]),
             "record_invalid", "Write action has unsupported ledger anchors")
    ledger_path = cases._path(ledger["ledger_path"])
    _require(str(ledger_path) == ledger["ledger_path"] and ledger_path.parent == action.parent and ledger_path != action,
             "record_invalid", "Write action has unsupported ledger path bindings")
    _require(isinstance(catalog, dict) and set(catalog) == CATALOG_ANCHOR_FIELDS
             and cases._pin_valid(catalog["catalog_directory"])
             and cases._pin_valid(catalog["catalog_record"], full=True, file=True)
             and 1 < catalog["catalog_record"]["size"] <= catalogs.MAX_CATALOG_BYTES
             and _empty_lock(catalog["catalog_lock"])
             and isinstance(catalog["catalog_request_digest"], str)
             and cases.jobs.HASH.fullmatch(catalog["catalog_request_digest"]),
             "record_invalid", "Write action has unsupported catalog anchors")
    catalog_path = cases._path(catalog["catalog_path"])
    receipt = _public_catalog(catalog["catalog"])
    _require(str(catalog_path) == catalog["catalog_path"] and catalog_path.parent == action.parent
             and catalog_path not in {action, ledger_path} and _same(receipt, catalog["catalog"]),
             "record_invalid", "Write action has unsupported catalog path or receipt")
    _require(isinstance(member, dict) and set(member) == catalogs.MEMBER_FIELDS
             and type(member["index"]) is int and 1 <= member["index"] <= receipt["write_count"]
             and cases._pin_valid(member["job_directory"])
             and cases._pin_valid(member["job_record"], full=True, file=True)
             and 1 < member["job_record"]["size"] <= writes.MAX_INTENT_BYTES and _empty_lock(member["job_lock"]),
             "record_invalid", "Write action has unsupported selected member pins")
    job = cases._path(member["job_path"])
    descriptor = _public_write(member["write"])
    claim = _public_operation(body["lease"])
    _require(str(job) == member["job_path"] and job.parent == action.parent
             and job not in {action, ledger_path, catalog_path} and _same(descriptor, member["write"])
             and _same(claim, body["lease"]) and claim["kind"] == "claim" and claim["released_at"] is None
             and claim["index"] == member["index"] and claim["ledger_id"] == ledger["ledger_id"]
             and _same(claim["write"], descriptor),
             "record_invalid", "Write action selected member differs from its historical claim")
    _require(_same(cases._directory(action.parent), body["parent"])
             and _same(cases._directory(action), body["action_directory"]),
             "record_invalid", "Write action differs from its own fixed directory identities")
    _bounded(body, MAX_INTENT_BYTES, deadline)
    _bounded(_action(body), MAX_PUBLIC_BYTES, deadline)
    return body


def _read_result(action: Path, body: dict[str, Any], deadline: float) -> dict[str, Any] | None:
    if RESULT_NAME not in _names(action):
        return None
    record = store_backup._read_json(action / RESULT_NAME, MAX_RESULT_BYTES)
    _require(set(record) == RESULT_FIELDS and type(record["schema_version"]) is int and record["schema_version"] == 1
             and record["rules_version"] == RULES_VERSION and record["action_id"] == body["action_id"]
             and record["intent_digest"] == body["request_digest"]
             and isinstance(record["result_digest"], str) and cases.jobs.HASH.fullmatch(record["result_digest"])
             and digest({key: value for key, value in record.items() if key != "result_digest"}) == record["result_digest"],
             "record_invalid", "Write action result differs from its fixed intent")
    value = _wrapper(body, record["observation"])
    _bounded(record, MAX_RESULT_BYTES, deadline)
    _bounded(value, MAX_PUBLIC_BYTES, deadline)
    return value


def _request(ledger: Path, action: Path, index: Any, worker: Any, lease_id: Any,
             generation: Any, operation_id: Any, expected_state: Any) -> None:
    _require(action != ledger and action.parent == ledger.parent and cases.jobs._operation(operation_id)
             and type(index) is int and 1 <= index <= catalogs.MAX_WRITES
             and cases.jobs._operation(worker) and isinstance(lease_id, str) and leases.LEASE_ID.fullmatch(lease_id)
             and type(generation) is int and 1 <= generation <= leases.MAX_OPERATIONS
             and isinstance(expected_state, str) and expected_state in TARGETS,
             "invalid_input", "Write action needs exact bounded lease and phase parameters")
    _require(all(len(str(action / name).encode("utf-8")) <= 4096 for name in (INTENT_NAME, LOCK_NAME, RESULT_NAME)),
             "invalid_input", "Generated write action path exceeds its supported bound")


def _claim(heads: dict[int, Any], operations: dict[str, Any], *, index: int, worker: str,
           lease_id: str, generation: int) -> dict[str, Any]:
    head = heads.get(index)
    _require(head is not None and head["released_at"] is None
             and head["lease_id"] == lease_id and head["generation"] == generation and head["worker"] == worker,
             "lease_changed", "Write action no longer names the latest unreleased lease")
    claims = [operation for _, operation in operations.values()
              if operation["kind"] == "claim" and operation["index"] == index and operation["lease_id"] == lease_id]
    _require(len(claims) == 1 and _same(leases._current(claims[0]), head),
             "record_invalid", "Write action lease does not identify its unique original claim")
    return claims[0]


def _fresh(claim: dict[str, Any], last_at: float, *, not_before: float = 0) -> float:
    checked_at = time.time()
    _require(cases._timestamp(checked_at), "io_failed", "Write action clock is unsupported")
    _require(checked_at >= max(last_at, not_before, claim["claimed_at"]),
             "clock_regressed", "Write action clock precedes its captured or current history")
    _require(checked_at < claim["expires_at"], "lease_expired", "Write action lease has expired before admission")
    return checked_at


def _ledger_pins(ledger: Path, deadline: float) -> dict[str, Any]:
    _, manifest = cases._bytes(ledger / leases.MANIFEST_NAME, leases.MAX_MANIFEST_BYTES, deadline)
    _, lock = cases._bytes(ledger / leases.LOCK_NAME, 0, deadline)
    return {"ledger_path": str(ledger), "ledger_directory": cases._directory(ledger),
            "ledger_manifest": manifest, "ledger_lock": lock, "database_pin": leases._database_pin(ledger)}


def _job_binding(member: dict[str, Any], deadline: float) -> None:
    job = Path(member["job_path"])
    _require(_same(cases._directory(job), member["job_directory"]),
             "record_invalid", "Write action selected job directory changed")
    _, record = cases._bytes(job / writes.INTENT_NAME, writes.MAX_INTENT_BYTES, deadline)
    _, lock = cases._bytes(job / writes.LOCK_NAME, 0, deadline)
    _require(_same(record, member["job_record"]) and _same(lock, member["job_lock"]),
             "record_invalid", "Write action selected job controls changed before parsing")


@contextmanager
def _capture(action: Path, ledger: Path, *, index: int, worker: str, lease_id: str, generation: int,
             expected_state: str, deadline: float) -> Iterator[tuple[dict[str, Any], float]]:
    """Keep ledger/job/review locks through the caller's final intent write."""
    with leases._lock(ledger, deadline):
        ledger_body = leases._read_manifest(ledger, deadline)
        _require(index <= len(ledger_body["source"]["members"]), "invalid_input", "Write action index is outside its ledger")
        with leases._connection(ledger, ledger_body, deadline, write=False) as connection:
            heads, operations, last_at = leases._history(connection, ledger_body, deadline)
        claim = _claim(heads, operations, index=index, worker=worker, lease_id=lease_id, generation=generation)
        ledger_anchor = {**_ledger_pins(ledger, deadline), "ledger_id": ledger_body["ledger_id"]}
        source = ledger_body["source"]
        catalog = Path(source["catalog_path"])
        _require(action not in {ledger, catalog}, "invalid_input", "Write action must be separate from its catalog and ledger")
        with catalogs._lock(catalog, deadline):
            _require(_same(cases._directory(catalog), source["catalog_directory"]),
                     "record_invalid", "Captured write catalog directory changed")
            _, record = cases._bytes(catalog / catalogs.CATALOG_NAME, catalogs.MAX_CATALOG_BYTES, deadline)
            _, lock = cases._bytes(catalog / catalogs.LOCK_NAME, 0, deadline)
            _require(_same(record, source["catalog_record"]) and _same(lock, source["catalog_lock"]),
                     "record_invalid", "Captured write catalog controls changed before parsing")
            catalog_body = catalogs._read_catalog(catalog, deadline)
            _require(all(member["job_path"] != str(action) for member in catalog_body["members"]),
                     "invalid_input", "Write action must be separate from every captured member job")
            _require(catalog_body["request_digest"] == source["catalog_request_digest"]
                     and _same(catalogs._catalog(catalog_body), source["catalog"])
                     and _same([{key: member[key] for key in ("index", "write")} for member in catalog_body["members"]], source["members"]),
                     "record_invalid", "Captured write catalog membership differs from its ledger")
            member = catalog_body["members"][index - 1]
            catalog_anchor = {key: source[key] for key in CATALOG_ANCHOR_FIELDS}
        job = Path(member["job_path"])
        _require(action != job, "invalid_input", "Write action must be separate from its selected job")
        with writes._lock(job, deadline):
            _job_binding(member, deadline)
            write_body = writes._read_intent(job, deadline)
            _require(_same(catalogs._write(write_body), member["write"]),
                     "record_invalid", "Selected write descriptor differs from captured membership")
            catalogs._separate(action, write_body)
            with writes.reviews._lock(Path(write_body["review_path"]), deadline):
                status = writes._status(write_body, deadline)
                _require(status["state"] == expected_state, "phase_changed", "Write action expected phase is no longer current")
                _fresh(claim, last_at)
                captured = {"ledger_anchor": ledger_anchor, "catalog_anchor": catalog_anchor,
                            "member": member, "lease": claim}
                _bounded(captured, MAX_INTENT_BYTES, deadline)
                yield captured, last_at
                _deadline(deadline)


def _prepare_replay(action: Path, ledger: Path, index: int, worker: str, lease_id: str,
                    generation: int, operation_id: str, expected_state: str, deadline: float) -> dict[str, Any]:
    with _lock(action, deadline):
        body = _read_intent(action, deadline)
        claim = body["lease"]
        _require(body["ledger_anchor"]["ledger_path"] == str(ledger) and body["member"]["index"] == index
                 and claim["worker"] == worker and claim["lease_id"] == lease_id and claim["generation"] == generation
                 and body["operation_id"] == operation_id and body["before_state"] == expected_state,
                 "conflict", "Write action is already bound to another exact request")
        _read_result(action, body, deadline)
        result = _action(body)
        _bounded(result, MAX_PUBLIC_BYTES, deadline)
        return result


def prepare_review_write_action(action_directory: str | Path, ledger_directory: str | Path, *, index: Any,
                                 worker: Any, lease_id: Any, generation: Any, operation_id: Any,
                                 expected_state: Any) -> dict[str, Any]:
    """Capture one exact phase and its active original lease, without advancing it."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        action, ledger = cases._path(action_directory), cases._path(ledger_directory)
        _request(ledger, action, index, worker, lease_id, generation, operation_id, expected_state)
        cases._directory(action.parent)
        if os.path.lexists(action):
            return _prepare_replay(action, ledger, index, worker, lease_id, generation, operation_id, expected_state, deadline)
        parameters = {"index": index, "worker": worker, "lease_id": lease_id, "generation": generation,
                      "expected_state": expected_state, "deadline": deadline}
        with _capture(action, ledger, **parameters) as (preliminary, _):
            pass
        try:
            action.mkdir(mode=0o700)
        except FileExistsError:
            return _prepare_replay(action, ledger, index, worker, lease_id, generation, operation_id, expected_state, deadline)
        action.chmod(0o700)
        with _lock(action, deadline, create=True):
            with _capture(action, ledger, **parameters) as (captured, last_at):
                _require(_same(captured, preliminary), "source_changed", "Write action bindings changed during reservation")
                body = {"schema_version": 1, "rules_version": RULES_VERSION, "action_id": "wrrwa_" + secrets.token_hex(16),
                        "operation_id": operation_id, "created_at": _fresh(captured["lease"], last_at),
                        "action_path": str(action), "parent": cases._directory(action.parent),
                        "action_directory": cases._directory(action), **captured,
                        "before_state": expected_state, "target_state": TARGETS[expected_state]}
                body["request_digest"] = digest(body)
                _bounded(body, MAX_INTENT_BYTES, deadline)
                result = _action(body)
                _bounded(result, MAX_PUBLIC_BYTES, deadline)
                store_backup._write_json(action / INTENT_NAME, body, MAX_INTENT_BYTES)
                store_backup._fsync_directory(action)
                store_backup._fsync_directory(action.parent)
                _names(action)
                _deadline(deadline)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_action_io_failed", "Write action could not be prepared") from error


def inspect_review_write_action(action_directory: str | Path) -> dict[str, Any]:
    """Read only own immutable intent and optional saved observation."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        action = cases._path(action_directory)
        with _lock(action, deadline):
            body = _read_intent(action, deadline)
            result = _read_result(action, body, deadline) or _wrapper(body)
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_action_io_failed", "Write action could not be inspected") from error


def _phase(write_body: dict[str, Any], state: str, deadline: float) -> None:
    """Copy the old single-phase primitive while its existing locks are held."""
    job, review = Path(write_body["job_path"]), Path(write_body["review_path"])
    if state in {"prepared", "staging"}:
        writes._fill_stage(write_body, deadline)
    elif state == "ready":
        writes._sync_owned(job / writes.STAGE_NAME, write_body, deadline, links=(1,))
        _deadline(deadline)
        try:
            os.link(job / writes.STAGE_NAME, review / writes.reviews.REVIEW_NAME, follow_symlinks=False)
        except FileExistsError:
            raise WorkbenchError("workspace_restore_case_retention_review_write_action_conflict",
                                 "Write action refuses an occupied review target") from None
        store_backup._fsync_directory(review)
        store_backup._fsync_directory(job)
    elif state == "linked":
        writes._sync_owned(review / writes.reviews.REVIEW_NAME, write_body, deadline, links=(2,))
        _deadline(deadline)
        store_backup._fsync_directory(review)
        _require(writes._status(write_body, deadline)["state"] == "linked", "conflict",
                 "Write alias changed before its proved action removal")
        (job / writes.STAGE_NAME).unlink()
        store_backup._fsync_directory(job)
        writes._sync_owned(review / writes.reviews.REVIEW_NAME, write_body, deadline, links=(1,))
        store_backup._fsync_directory(review)
    else:
        _require(False, "phase_changed", "Write action has no supported new phase")
    _deadline(deadline)


def execute_review_write_action(action_directory: str | Path) -> dict[str, Any]:
    """Apply only the captured phase, or save an explicit exact-POST observation."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        action = cases._path(action_directory)
        with _lock(action, deadline):
            body = _read_intent(action, deadline)
            saved = _read_result(action, body, deadline)
            if saved is not None:
                return saved
            ledger = Path(body["ledger_anchor"]["ledger_path"])
            with leases._lock(ledger, deadline):
                anchor = body["ledger_anchor"]
                _require(_same(_ledger_pins(ledger, deadline), {key: anchor[key] for key in anchor if key != "ledger_id"}),
                         "record_invalid", "Write action ledger controls changed before parsing")
                ledger_body = leases._read_manifest(ledger, deadline)
                _require(ledger_body["ledger_id"] == anchor["ledger_id"]
                         and _same({key: ledger_body["source"][key] for key in CATALOG_ANCHOR_FIELDS}, body["catalog_anchor"]),
                         "record_invalid", "Write action ledger source differs from its captured catalog")
                with leases._connection(ledger, ledger_body, deadline, write=False) as connection:
                    heads, operations, last_at = leases._history(connection, ledger_body, deadline)
                original = body["lease"]
                claim = _claim(heads, operations, index=body["member"]["index"], worker=original["worker"],
                               lease_id=original["lease_id"], generation=original["generation"])
                _require(_same(claim, original), "lease_changed", "Write action lease differs from its original claim")
                member = body["member"]
                job = Path(member["job_path"])
                with writes._lock(job, deadline):
                    _job_binding(member, deadline)
                    write_body = writes._read_intent(job, deadline)
                    _require(_same(catalogs._write(write_body), member["write"]),
                             "record_invalid", "Write action current descriptor differs from its pinned member")
                    with writes.reviews._lock(Path(write_body["review_path"]), deadline):
                        status = writes._status(write_body, deadline)
                        state = status["state"]
                        perform = state == body["before_state"] or body["before_state"] == "prepared" and state == "staging"
                        _require(perform or state == body["target_state"], "phase_changed",
                                 "Write action current phase is outside its captured PRE/POST pair")
                        checked_at = _fresh(claim, last_at, not_before=body["created_at"])
                        if perform:
                            _phase(write_body, state, deadline)
                        observed = writes._status(write_body, deadline)
                        _require(observed["state"] == body["target_state"], "phase_changed",
                                 "Write action did not observe its exact captured target phase")
                        recorded_at = time.time()
                        _require(cases._timestamp(recorded_at), "io_failed", "Write action result clock is unsupported")
                        _require(recorded_at >= checked_at, "clock_regressed", "Write action result clock precedes admission")
                        observation = {"rules_version": RULES_VERSION, "action_id": body["action_id"],
                                       "recorded_at": recorded_at, "lease_checked_at": checked_at,
                                       "outcome": "phase_performed" if perform else "expected_phase_observed",
                                       "observed_state": observed["state"], "effect_performed": bool(perform)}
                        result = _wrapper(body, observation)
                        record = {"schema_version": 1, "rules_version": RULES_VERSION, "action_id": body["action_id"],
                                  "intent_digest": body["request_digest"], "observation": observation}
                        record["result_digest"] = digest(record)
                        _bounded(record, MAX_RESULT_BYTES, deadline)
                        _bounded(result, MAX_PUBLIC_BYTES, deadline)
                        store_backup._write_json(action / RESULT_NAME, record, MAX_RESULT_BYTES)
                        store_backup._fsync_directory(action)
                        _deadline(deadline)
                        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_action_io_failed", "Write action execution could not complete") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_review_write_action_"):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict"):
        if error.code.endswith(suffix):
            return WorkbenchError("workspace_restore_case_retention_review_write_action_" + suffix,
                                  "Write action exceeded a bound or has unsupported private bindings")
    return WorkbenchError("workspace_restore_case_retention_review_write_action_record_invalid",
                          "Write action controls or captured bindings cannot be validated")
