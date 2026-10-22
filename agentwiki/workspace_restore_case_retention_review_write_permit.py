"""Local bearer permits for delivery and execution of one pinned write action.

The private filesystem owner issues a capability; the same holder may execute
or revoke it. Every protected delivery authenticates before reading an action
result, including historical replay. Old action/write APIs remain outside this
optional guard. There is no authentication of a person or past effect.
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

from . import governance, store_backup
from . import workspace_restore_case_retention_review_write_action as actions
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_write_permit_cli import _public_grant, _public_show, _public_execution


RULES_VERSION = "workspace-restore-case-retention-review-write-permit-1"
GRANT_NAME = ".agentwiki-restore-case-retention-review-write-permit.json"
LOCK_NAME = ".agentwiki-restore-case-retention-review-write-permit.lock"
REVOCATION_NAME = ".agentwiki-restore-case-retention-review-write-permit.revoked.json"
MAX_GRANT_BYTES = 64 * 1024
MAX_REVOCATION_BYTES = 64 * 1024
MAX_PUBLIC_BYTES = 64 * 1024
DEADLINE_SECONDS = actions.DEADLINE_SECONDS
PERMIT_ID = re.compile(r"wrrwpm_[0-9a-f]{32}\Z")
FLAGS = {**actions.FLAGS, "local_capability_only": True, "external_identity_verified": False}
GRANT_FIELDS = {"schema_version", "rules_version", "permit_id", "operation_id", "created_at", "permit_path",
                "parent", "permit_directory", "principal", "credential_hash", "action_anchor", "action", "request_digest"}
ANCHOR_FIELDS = {"action_path", "action_directory", "action_intent", "action_lock"}
REVOCATION_FIELDS = {"schema_version", "rules_version", "permit_id", "operation_id", "revoked_at", "grant_digest", "request_digest"}
leases, writes, catalogs, cases = actions.leases, actions.writes, actions.catalogs, actions.cases
_ERRORS = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_review_write_permit_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Write permit exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _bounded(value: Any, maximum: int, deadline: float) -> None:
    actions._bounded(value, maximum, deadline)
    _deadline(deadline)


def _credential_hash(credential: Any) -> str:
    _require(isinstance(credential, str) and governance.TOKEN.fullmatch(credential) is not None,
             "credential_invalid", "A valid local bearer credential is required")
    return hashlib.sha256(credential.encode("ascii")).hexdigest()


def _authorize(body: dict[str, Any], credential: Any) -> None:
    _require(hmac.compare_digest(_credential_hash(credential), body["credential_hash"]),
             "credential_invalid", "Local bearer credential does not match this permit")


def _names(permit: Path) -> set[str]:
    names = cases._names(permit, 3)
    _require({GRANT_NAME, LOCK_NAME} <= names <= {GRANT_NAME, LOCK_NAME, REVOCATION_NAME},
             "incomplete", "Write permit has incomplete or unsupported controls")
    return names


@contextmanager
def _lock(permit: Path, deadline: float, *, create: bool = False) -> Iterator[None]:
    cases._directory(permit)
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
    if create:
        flags |= os.O_CREAT | os.O_EXCL
    try:
        descriptor = os.open(permit / LOCK_NAME, flags, 0o600)
    except FileNotFoundError:
        raise WorkbenchError("workspace_restore_case_retention_review_write_permit_incomplete",
                             "Write permit has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Write permit needs a private empty regular owner lock")
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
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((permit / LOCK_NAME).lstat()),
                 "record_invalid", "Write permit coordination changed while locking")
        yield
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((permit / LOCK_NAME).lstat()),
                 "record_invalid", "Write permit coordination changed during its operation")
    finally:
        fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _grant(body: dict[str, Any]) -> dict[str, Any]:
    return _public_grant({"rules_version": RULES_VERSION,
                          **{key: body[key] for key in ("permit_id", "operation_id", "created_at", "principal", "action")},
                          **FLAGS})


def _show(body: dict[str, Any], revocation: dict[str, Any] | None) -> dict[str, Any]:
    public = None if revocation is None else {
        "rules_version": RULES_VERSION,
        **{key: revocation[key] for key in ("permit_id", "operation_id", "revoked_at")}, "reason": "holder_revoked",
    }
    return _public_show({"rules_version": RULES_VERSION, "permit": _grant(body),
                         "state": "active" if revocation is None else "revoked", "revocation": public, **FLAGS})


def _execution(body: dict[str, Any], result: dict[str, Any], deadline: float) -> dict[str, Any]:
    value = _public_execution({"rules_version": RULES_VERSION, "permit": _grant(body), "action_result": result,
                               "credential_verified": True, "revocation_checked": True,
                               "historical_effect_authenticated": False, **FLAGS})
    _bounded(value, MAX_PUBLIC_BYTES, deadline)
    return value


def _read_grant(permit: Path, deadline: float) -> dict[str, Any]:
    """Validate only this permit's grant, identities and opaque action pins."""
    _deadline(deadline)
    _names(permit)
    body = store_backup._read_json(permit / GRANT_NAME, MAX_GRANT_BYTES)
    _require(set(body) == GRANT_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["permit_id"], str)
             and PERMIT_ID.fullmatch(body["permit_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["permit_path"] == str(permit)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["permit_directory"])
             and cases.jobs._operation(body["principal"]) and isinstance(body["credential_hash"], str)
             and cases.jobs.HASH.fullmatch(body["credential_hash"])
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Write permit has unsupported immutable fields")
    anchor = body["action_anchor"]
    _require(isinstance(anchor, dict) and set(anchor) == ANCHOR_FIELDS
             and cases._pin_valid(anchor["action_directory"])
             and cases._pin_valid(anchor["action_intent"], full=True, file=True)
             and 1 < anchor["action_intent"]["size"] <= actions.MAX_INTENT_BYTES
             and actions._empty_lock(anchor["action_lock"]),
             "record_invalid", "Write permit has unsupported private action anchors")
    action = cases._path(anchor["action_path"])
    _require(str(action) == anchor["action_path"] and action.parent == permit.parent and action != permit,
             "record_invalid", "Write permit has unsupported sibling action binding")
    _require(_same(cases._directory(permit.parent), body["parent"])
             and _same(cases._directory(permit), body["permit_directory"]),
             "record_invalid", "Write permit differs from its own fixed directory identities")
    _bounded(body, MAX_GRANT_BYTES, deadline)
    _bounded(_grant(body), MAX_PUBLIC_BYTES, deadline)
    return body


def _read_revocation(permit: Path, body: dict[str, Any], deadline: float) -> dict[str, Any] | None:
    if REVOCATION_NAME not in _names(permit):
        return None
    record = store_backup._read_json(permit / REVOCATION_NAME, MAX_REVOCATION_BYTES)
    _require(set(record) == REVOCATION_FIELDS and type(record["schema_version"]) is int and record["schema_version"] == 1
             and record["rules_version"] == RULES_VERSION and record["permit_id"] == body["permit_id"]
             and cases.jobs._operation(record["operation_id"]) and cases._timestamp(record["revoked_at"])
             and record["revoked_at"] >= body["created_at"] and record["grant_digest"] == body["request_digest"]
             and isinstance(record["request_digest"], str) and cases.jobs.HASH.fullmatch(record["request_digest"])
             and digest({key: value for key, value in record.items() if key != "request_digest"}) == record["request_digest"],
             "record_invalid", "Write permit revocation differs from its immutable grant")
    _bounded(record, MAX_REVOCATION_BYTES, deadline)
    _show(body, record)
    return record


def _action_pins(action: Path, deadline: float) -> dict[str, Any]:
    _, intent = cases._bytes(action / actions.INTENT_NAME, actions.MAX_INTENT_BYTES, deadline)
    _, lock = cases._bytes(action / actions.LOCK_NAME, 0, deadline)
    return {"action_path": str(action), "action_directory": cases._directory(action),
            "action_intent": intent, "action_lock": lock}


@contextmanager
def _capture(permit: Path, action: Path, deadline: float) -> Iterator[dict[str, Any]]:
    with actions._lock(action, deadline):
        body = actions._read_intent(action, deadline)
        actions._read_result(action, body, deadline)
        excluded = {body["action_path"], body["ledger_anchor"]["ledger_path"],
                    body["catalog_anchor"]["catalog_path"], body["member"]["job_path"]}
        _require(str(permit) not in excluded, "invalid_input", "Write permit must remain separate from known action bindings")
        anchor = _action_pins(action, deadline)
        raw, _ = cases._bytes(action / actions.INTENT_NAME, actions.MAX_INTENT_BYTES, deadline)
        _require(raw == canonical(body).encode("utf-8"),
                 "source_changed", "Write action changed while its permit scope was captured")
        captured = {"action_anchor": anchor, "action": actions._action(body)}
        _bounded(captured, MAX_GRANT_BYTES, deadline)
        yield captured
        _deadline(deadline)


def _issue_replay(permit: Path, action: Path, *, operation_id: str, principal: str,
                  credential_hash: str, deadline: float) -> dict[str, Any]:
    with _lock(permit, deadline):
        body = _read_grant(permit, deadline)
        _read_revocation(permit, body, deadline)
        _require(body["operation_id"] == operation_id and body["principal"] == principal
                 and body["action_anchor"]["action_path"] == str(action)
                 and hmac.compare_digest(body["credential_hash"], credential_hash),
                 "conflict", "Write permit is already bound to another exact issue request")
        result = _grant(body)
        _bounded(result, MAX_PUBLIC_BYTES, deadline)
        return result


def issue_review_write_permit(permit_directory: str | Path, action_directory: str | Path, *,
                               operation_id: Any, principal: Any, credential: Any) -> dict[str, Any]:
    """Capture one existing action and a local holder capability, without effect."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        permit, action = cases._path(permit_directory), cases._path(action_directory)
        _require(permit != action and permit.parent == action.parent and cases.jobs._operation(operation_id)
                 and cases.jobs._operation(principal),
                 "invalid_input", "Write permit needs separate sibling paths and bounded identifiers")
        secret_hash = _credential_hash(credential)
        cases._directory(permit.parent)
        if os.path.lexists(permit):
            return _issue_replay(permit, action, operation_id=operation_id, principal=principal,
                                 credential_hash=secret_hash, deadline=deadline)
        with _capture(permit, action, deadline) as preliminary:
            pass
        try:
            permit.mkdir(mode=0o700)
        except FileExistsError:
            return _issue_replay(permit, action, operation_id=operation_id, principal=principal,
                                 credential_hash=secret_hash, deadline=deadline)
        permit.chmod(0o700)
        with _lock(permit, deadline, create=True):
            with _capture(permit, action, deadline) as captured:
                _require(_same(captured, preliminary), "source_changed", "Write permit action bindings changed during reservation")
                created_at = time.time()
                _require(cases._timestamp(created_at), "io_failed", "Write permit clock is unsupported")
                _require(created_at >= captured["action"]["created_at"],
                         "clock_regressed", "Write permit clock precedes its historical action")
                body = {"schema_version": 1, "rules_version": RULES_VERSION, "permit_id": "wrrwpm_" + secrets.token_hex(16),
                        "operation_id": operation_id, "created_at": created_at, "permit_path": str(permit),
                        "parent": cases._directory(permit.parent), "permit_directory": cases._directory(permit),
                        "principal": principal, "credential_hash": secret_hash, **captured}
                body["request_digest"] = digest(body)
                _bounded(body, MAX_GRANT_BYTES, deadline)
                result = _grant(body)
                _bounded(result, MAX_PUBLIC_BYTES, deadline)
                store_backup._write_json(permit / GRANT_NAME, body, MAX_GRANT_BYTES)
                store_backup._fsync_directory(permit)
                store_backup._fsync_directory(permit.parent)
                _names(permit)
                _deadline(deadline)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_permit_io_failed", "Write permit could not be issued") from error


def inspect_review_write_permit(permit_directory: str | Path) -> dict[str, Any]:
    """Read only own grant and optional irreversible revocation metadata."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        permit = cases._path(permit_directory)
        with _lock(permit, deadline):
            body = _read_grant(permit, deadline)
            revocation = _read_revocation(permit, body, deadline)
            result = _show(body, revocation)
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_permit_io_failed", "Write permit could not be inspected") from error


def revoke_review_write_permit(permit_directory: str | Path, *, operation_id: Any, credential: Any) -> dict[str, Any]:
    """The matching local holder may irreversibly revoke this one permit."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(cases.jobs._operation(operation_id), "invalid_input", "Write permit revocation needs a bounded operation ID")
        permit = cases._path(permit_directory)
        with _lock(permit, deadline):
            body = _read_grant(permit, deadline)
            revocation = _read_revocation(permit, body, deadline)
            _authorize(body, credential)
            if revocation is not None:
                _require(revocation["operation_id"] == operation_id,
                         "conflict", "Write permit is already revoked by another exact operation")
                result = _show(body, revocation)
                _bounded(result, MAX_PUBLIC_BYTES, deadline)
                return result
            revoked_at = time.time()
            _require(cases._timestamp(revoked_at), "io_failed", "Write permit revocation clock is unsupported")
            _require(revoked_at >= body["created_at"], "clock_regressed", "Write permit revocation clock precedes its grant")
            record = {"schema_version": 1, "rules_version": RULES_VERSION, "permit_id": body["permit_id"],
                      "operation_id": operation_id, "revoked_at": revoked_at, "grant_digest": body["request_digest"]}
            record["request_digest"] = digest(record)
            _bounded(record, MAX_REVOCATION_BYTES, deadline)
            result = _show(body, record)
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            store_backup._write_json(permit / REVOCATION_NAME, record, MAX_REVOCATION_BYTES)
            store_backup._fsync_directory(permit)
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_permit_io_failed", "Write permit could not be revoked") from error


def _execute_action_locked(action: Path, body: dict[str, Any], grant: dict[str, Any], deadline: float) -> dict[str, Any]:
    """Use the old orchestration with the caller's deadline and action lock."""
    saved = actions._read_result(action, body, deadline)
    if saved is not None:
        return _execution(grant, saved, deadline)
    ledger = Path(body["ledger_anchor"]["ledger_path"])
    with leases._lock(ledger, deadline):
        anchor = body["ledger_anchor"]
        _require(_same(actions._ledger_pins(ledger, deadline), {key: anchor[key] for key in anchor if key != "ledger_id"}),
                 "record_invalid", "Write permit action ledger controls changed before parsing")
        ledger_body = leases._read_manifest(ledger, deadline)
        _require(ledger_body["ledger_id"] == anchor["ledger_id"]
                 and _same({key: ledger_body["source"][key] for key in actions.CATALOG_ANCHOR_FIELDS}, body["catalog_anchor"]),
                 "record_invalid", "Write permit action ledger source differs from its captured catalog")
        with leases._connection(ledger, ledger_body, deadline, write=False) as connection:
            heads, operations, last_at = leases._history(connection, ledger_body, deadline)
        original = body["lease"]
        claim = actions._claim(heads, operations, index=body["member"]["index"], worker=original["worker"],
                               lease_id=original["lease_id"], generation=original["generation"])
        _require(_same(claim, original), "lease_changed", "Write permit action lease differs from its original claim")
        member = body["member"]
        job = Path(member["job_path"])
        with writes._lock(job, deadline):
            actions._job_binding(member, deadline)
            write_body = writes._read_intent(job, deadline)
            _require(_same(catalogs._write(write_body), member["write"]),
                     "record_invalid", "Write permit action current descriptor differs from its pinned member")
            with writes.reviews._lock(Path(write_body["review_path"]), deadline):
                status = writes._status(write_body, deadline)
                state = status["state"]
                perform = state == body["before_state"] or body["before_state"] == "prepared" and state == "staging"
                _require(perform or state == body["target_state"], "phase_changed",
                         "Write permit action phase is outside its captured PRE/POST pair")
                checked_at = actions._fresh(claim, last_at, not_before=body["created_at"])
                if perform:
                    actions._phase(write_body, state, deadline)
                observed = writes._status(write_body, deadline)
                _require(observed["state"] == body["target_state"], "phase_changed",
                         "Write permit action did not observe its exact captured target phase")
                recorded_at = time.time()
                _require(cases._timestamp(recorded_at), "io_failed", "Write permit action result clock is unsupported")
                _require(recorded_at >= checked_at, "clock_regressed", "Write permit action result clock precedes admission")
                observation = {"rules_version": actions.RULES_VERSION, "action_id": body["action_id"],
                               "recorded_at": recorded_at, "lease_checked_at": checked_at,
                               "outcome": "phase_performed" if perform else "expected_phase_observed",
                               "observed_state": observed["state"], "effect_performed": bool(perform)}
                result = actions._wrapper(body, observation)
                record = {"schema_version": 1, "rules_version": actions.RULES_VERSION, "action_id": body["action_id"],
                          "intent_digest": body["request_digest"], "observation": observation}
                record["result_digest"] = digest(record)
                actions._bounded(record, actions.MAX_RESULT_BYTES, deadline)
                actions._bounded(result, actions.MAX_PUBLIC_BYTES, deadline)
                store_backup._write_json(action / actions.RESULT_NAME, record, actions.MAX_RESULT_BYTES)
                store_backup._fsync_directory(action)
                _deadline(deadline)
                return _execution(grant, result, deadline)


def execute_review_write_permit(permit_directory: str | Path, *, credential: Any) -> dict[str, Any]:
    """Authenticate current protected delivery before action or saved-result reads."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        permit = cases._path(permit_directory)
        with _lock(permit, deadline):
            body = _read_grant(permit, deadline)
            revocation = _read_revocation(permit, body, deadline)
            _authorize(body, credential)
            _require(revocation is None, "revoked", "Write permit has been irreversibly revoked")
            action = Path(body["action_anchor"]["action_path"])
            with actions._lock(action, deadline):
                _require(_same(_action_pins(action, deadline), body["action_anchor"]),
                         "record_invalid", "Write permit action controls changed before parsing")
                action_body = actions._read_intent(action, deadline)
                _require(_same(actions._action(action_body), body["action"]),
                         "record_invalid", "Write permit action differs from its exact historical scope")
                return _execute_action_locked(action, action_body, body, deadline)
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_permit_io_failed", "Protected write action could not complete") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_review_write_permit_"):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict",
                   "lease_changed", "lease_expired", "phase_changed", "clock_regressed", "io_failed"):
        if error.code.endswith(suffix):
            return WorkbenchError("workspace_restore_case_retention_review_write_permit_" + suffix,
                                  "Write permit exceeded a bound or has unsupported current bindings")
    return WorkbenchError("workspace_restore_case_retention_review_write_permit_record_invalid",
                          "Write permit controls or captured bindings cannot be validated")
