"""Local issuer policy for creating a new exact review-write permit.

An administrator bearer authorizes the managed issuance path. A separate
immutable receipt records its policy match; it never makes old permit APIs
mandatory or authenticates a person. Native grant and issuance receipt have
an explicit unsupported gap, and incomplete bootstrap is never adopted.
"""

from __future__ import annotations

from contextlib import contextmanager
import fcntl
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
from . import workspace_restore_case_retention_review_write_permit as permits
from .store import WorkbenchError, canonical, digest
from .workspace_restore_case_retention_review_write_issuer_cli import (
    _public_issuer, _public_show, _public_issuance, _public_policy,
)


RULES_VERSION = "workspace-restore-case-retention-review-write-issuer-1"
ISSUER_NAME = ".agentwiki-restore-case-retention-review-write-issuer.json"
ISSUER_LOCK_NAME = ".agentwiki-restore-case-retention-review-write-issuer.lock"
REVOCATION_NAME = ".agentwiki-restore-case-retention-review-write-issuer.revoked.json"
ISSUANCE_NAME = ".agentwiki-restore-case-retention-review-write-issuance.json"
ISSUANCE_LOCK_NAME = ".agentwiki-restore-case-retention-review-write-issuance.lock"
MAX_ISSUER_BYTES = 64 * 1024
MAX_ISSUANCE_BYTES = 64 * 1024
MAX_REVOCATION_BYTES = 64 * 1024
MAX_PUBLIC_BYTES = 64 * 1024
DEADLINE_SECONDS = permits.DEADLINE_SECONDS
ISSUER_ID = re.compile(r"wrrwpir_[0-9a-f]{32}\Z")
ISSUANCE_ID = re.compile(r"wrrwpis_[0-9a-f]{32}\Z")
FLAGS = {
    "advisory_only": True, "inspection_only": True, "domain_writes_allowed": False,
    "original_files_modified": False, "archival_authorized": False, "deletion_authorized": False,
    "actor_authenticated": False, "old_authority_activated": False, "external_fencing_established": False,
    "worker_authenticated": False, "local_capability_only": True, "external_identity_verified": False,
    "issuance_policy_only": True, "existing_permits_revoked": False,
}
ISSUER_FIELDS = {"schema_version", "rules_version", "issuer_id", "operation_id", "created_at", "issuer_path",
                 "parent", "issuer_directory", "administrator", "credential_hash", "policy", "request_digest"}
REVOCATION_FIELDS = {"schema_version", "rules_version", "issuer_id", "operation_id", "revoked_at",
                     "grant_digest", "request_digest"}
ISSUANCE_FIELDS = {"schema_version", "rules_version", "issuance_id", "operation_id", "created_at", "issuance_path",
                   "parent", "issuance_directory", "issuer_anchor", "issuer", "rule", "permit_anchor", "permit",
                   "request", "request_digest"}
REQUEST_FIELDS = {"action_path", "permit_path", "principal", "holder_credential_hash"}
ISSUER_ANCHOR_FIELDS = {"issuer_path", "issuer_directory", "issuer_record", "issuer_lock"}
PERMIT_ANCHOR_FIELDS = {"permit_path", "permit_directory", "permit_record", "permit_lock"}
actions, cases = permits.actions, permits.cases
_ERRORS = (OSError, sqlite3.Error, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError)


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_" + code, message)


def _deadline(deadline: float) -> None:
    _require(time.monotonic() < deadline, "limit_exceeded", "Write issuer exceeded its common time bound")


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _bounded(value: Any, maximum: int, deadline: float) -> None:
    actions._bounded(value, maximum, deadline)
    _deadline(deadline)


def _names(location: Path, *, issuance: bool = False) -> set[str]:
    names = cases._names(location, 2 if issuance else 3)
    required = {ISSUANCE_NAME, ISSUANCE_LOCK_NAME} if issuance else {ISSUER_NAME, ISSUER_LOCK_NAME}
    allowed = required if issuance else required | {REVOCATION_NAME}
    _require(required <= names <= allowed, "incomplete", "Write issuer controls are incomplete or unsupported")
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
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_incomplete",
                             "Write issuer has no coordination control") from None
    try:
        details = os.fstat(descriptor)
        _require(stat.S_ISREG(details.st_mode) and details.st_uid == os.geteuid()
                 and stat.S_IMODE(details.st_mode) == 0o600 and details.st_nlink == 1 and details.st_size == 0,
                 "private_path_required", "Write issuer needs a private empty regular owner lock")
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
                 "record_invalid", "Write issuer coordination changed while locking")
        yield
        _require(original == cases._full(os.fstat(descriptor)) == cases._full((location / name).lstat()),
                 "record_invalid", "Write issuer coordination changed during its operation")
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
                             **{key: body[key] for key in ("issuance_id", "operation_id", "created_at", "issuer", "rule", "permit")},
                             "policy_authorized": True, **FLAGS})


def _hash(credential: Any) -> str:
    return permits._credential_hash(credential)


def _authorize(body: dict[str, Any], credential: Any) -> None:
    _require(hmac.compare_digest(_hash(credential), body["credential_hash"]),
             "credential_invalid", "Local issuer bearer does not match this authority")


def _read_issuer(issuer: Path, deadline: float) -> dict[str, Any]:
    _deadline(deadline)
    _names(issuer)
    body = store_backup._read_json(issuer / ISSUER_NAME, MAX_ISSUER_BYTES)
    _require(set(body) == ISSUER_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["issuer_id"], str)
             and ISSUER_ID.fullmatch(body["issuer_id"]) and cases.jobs._operation(body["operation_id"])
             and cases.jobs._operation(body["administrator"]) and cases._timestamp(body["created_at"])
             and body["issuer_path"] == str(issuer) and cases._pin_valid(body["parent"])
             and cases._pin_valid(body["issuer_directory"]) and isinstance(body["credential_hash"], str)
             and cases.jobs.HASH.fullmatch(body["credential_hash"]) and isinstance(body["request_digest"], str)
             and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Write issuer has unsupported immutable fields")
    _require(_same(cases._directory(issuer.parent), body["parent"])
             and _same(cases._directory(issuer), body["issuer_directory"]),
             "record_invalid", "Write issuer differs from its fixed directory identities")
    try:
        public = _issuer(body)
    except WorkbenchError as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_record_invalid",
                             "Write issuer has an unsupported saved policy") from error
    _bounded(body, MAX_ISSUER_BYTES, deadline)
    _bounded(public, MAX_PUBLIC_BYTES, deadline)
    return body


def _read_revocation(issuer: Path, body: dict[str, Any], deadline: float) -> dict[str, Any] | None:
    if REVOCATION_NAME not in _names(issuer):
        return None
    record = store_backup._read_json(issuer / REVOCATION_NAME, MAX_REVOCATION_BYTES)
    _require(set(record) == REVOCATION_FIELDS and type(record["schema_version"]) is int and record["schema_version"] == 1
             and record["rules_version"] == RULES_VERSION and record["issuer_id"] == body["issuer_id"]
             and cases.jobs._operation(record["operation_id"]) and cases._timestamp(record["revoked_at"])
             and record["revoked_at"] >= body["created_at"] and record["grant_digest"] == body["request_digest"]
             and isinstance(record["request_digest"], str) and cases.jobs.HASH.fullmatch(record["request_digest"])
             and digest({key: value for key, value in record.items() if key != "request_digest"}) == record["request_digest"],
             "record_invalid", "Write issuer revocation differs from its immutable authority")
    _bounded(record, MAX_REVOCATION_BYTES, deadline)
    return record


def _issuer_pins(issuer: Path, body: dict[str, Any], deadline: float) -> dict[str, Any]:
    raw, record = cases._bytes(issuer / ISSUER_NAME, MAX_ISSUER_BYTES, deadline)
    _, lock = cases._bytes(issuer / ISSUER_LOCK_NAME, 0, deadline)
    _require(raw == canonical(body).encode("utf-8"), "source_changed", "Write issuer changed while capturing its authority")
    return {"issuer_path": str(issuer), "issuer_directory": cases._directory(issuer),
            "issuer_record": record, "issuer_lock": lock}


def _permit_pins(permit: Path, body: dict[str, Any], deadline: float) -> dict[str, Any]:
    raw, record = cases._bytes(permit / permits.GRANT_NAME, permits.MAX_GRANT_BYTES, deadline)
    _, lock = cases._bytes(permit / permits.LOCK_NAME, 0, deadline)
    _require(raw == canonical(body).encode("utf-8"), "source_changed", "New native permit changed before its issuance receipt")
    return {"permit_path": str(permit), "permit_directory": cases._directory(permit),
            "permit_record": record, "permit_lock": lock}


def _anchor_valid(anchor: Any, *, issuer: bool) -> bool:
    prefix = "issuer" if issuer else "permit"
    fields = ISSUER_ANCHOR_FIELDS if issuer else PERMIT_ANCHOR_FIELDS
    maximum = MAX_ISSUER_BYTES if issuer else permits.MAX_GRANT_BYTES
    return (isinstance(anchor, dict) and set(anchor) == fields
            and cases._pin_valid(anchor[prefix + "_directory"])
            and cases._pin_valid(anchor[prefix + "_record"], full=True, file=True)
            and 1 < anchor[prefix + "_record"]["size"] <= maximum
            and actions._empty_lock(anchor[prefix + "_lock"]))


def _read_issuance(issuance: Path, deadline: float) -> dict[str, Any]:
    """Read only this complete immutable receipt; source anchors stay opaque."""
    _deadline(deadline)
    _names(issuance, issuance=True)
    body = store_backup._read_json(issuance / ISSUANCE_NAME, MAX_ISSUANCE_BYTES)
    _require(set(body) == ISSUANCE_FIELDS and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION and isinstance(body["issuance_id"], str)
             and ISSUANCE_ID.fullmatch(body["issuance_id"]) and cases.jobs._operation(body["operation_id"])
             and cases._timestamp(body["created_at"]) and body["issuance_path"] == str(issuance)
             and cases._pin_valid(body["parent"]) and cases._pin_valid(body["issuance_directory"])
             and _anchor_valid(body["issuer_anchor"], issuer=True) and _anchor_valid(body["permit_anchor"], issuer=False)
             and isinstance(body["request"], dict) and set(body["request"]) == REQUEST_FIELDS
             and cases.jobs._operation(body["request"]["principal"])
             and isinstance(body["request"]["holder_credential_hash"], str)
             and cases.jobs.HASH.fullmatch(body["request"]["holder_credential_hash"])
             and isinstance(body["request_digest"], str) and cases.jobs.HASH.fullmatch(body["request_digest"])
             and digest({key: value for key, value in body.items() if key != "request_digest"}) == body["request_digest"],
             "record_invalid", "Write issuance has unsupported immutable fields")
    issuer = cases._path(body["issuer_anchor"]["issuer_path"])
    permit = cases._path(body["permit_anchor"]["permit_path"])
    action = cases._path(body["request"]["action_path"])
    _require(str(issuer) == body["issuer_anchor"]["issuer_path"]
             and str(permit) == body["permit_anchor"]["permit_path"] == body["request"]["permit_path"]
             and str(action) == body["request"]["action_path"]
             and len({issuer, issuance, permit, action}) == 4
             and all(path.parent == issuance.parent for path in (issuer, permit, action))
             and _same(cases._directory(issuance.parent), body["parent"])
             and _same(cases._directory(issuance), body["issuance_directory"]),
             "record_invalid", "Write issuance differs from its own fixed sibling bindings")
    try:
        public = _issuance(body)
    except WorkbenchError as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_record_invalid",
                             "Write issuance has unsupported saved public scope") from error
    _require(body["request"]["principal"] == public["permit"]["principal"],
             "record_invalid", "Write issuance request differs from its captured permit principal")
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
                 "conflict", "Write issuer is bound to another exact creation request")
        result = _issuer(body)
        _bounded(result, MAX_PUBLIC_BYTES, deadline)
        return result


def create_review_write_permit_issuer(issuer_directory: str | Path, *, operation_id: Any, administrator: Any,
                                     credential: Any, policy: Any) -> dict[str, Any]:
    """Owner bootstrap one immutable local authority and explicit scope policy."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        issuer = cases._path(issuer_directory)
        _require(cases.jobs._operation(operation_id) and cases.jobs._operation(administrator),
                 "invalid_input", "Write issuer needs bounded operation and administrator identifiers")
        secret_hash = _hash(credential)
        try:
            fixed_policy = _public_policy(policy)
        except WorkbenchError as error:
            raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_invalid_input",
                                 "Write issuer needs a bounded unambiguous exact scope policy") from error
        _bounded(fixed_policy, MAX_ISSUER_BYTES, deadline)
        cases._directory(issuer.parent)
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
            created_at = time.time()
            _require(cases._timestamp(created_at), "io_failed", "Write issuer clock is unsupported")
            body = {"schema_version": 1, "rules_version": RULES_VERSION, "issuer_id": "wrrwpir_" + secrets.token_hex(16),
                    "operation_id": operation_id, "created_at": created_at, "issuer_path": str(issuer),
                    "parent": cases._directory(issuer.parent), "issuer_directory": cases._directory(issuer),
                    "administrator": administrator, "credential_hash": secret_hash, "policy": fixed_policy}
            body["request_digest"] = digest(body)
            _bounded(body, MAX_ISSUER_BYTES, deadline)
            result = _issuer(body)
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            store_backup._write_json(issuer / ISSUER_NAME, body, MAX_ISSUER_BYTES)
            store_backup._fsync_directory(issuer)
            store_backup._fsync_directory(issuer.parent)
            _names(issuer)
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_io_failed", "Write issuer could not be created") from error


def inspect_review_write_permit_issuer(issuer_directory: str | Path) -> dict[str, Any]:
    """Observe own immutable issuer and optional revocation, without children."""
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
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_io_failed", "Write issuer could not be inspected") from error


def revoke_review_write_permit_issuer(issuer_directory: str | Path, *, operation_id: Any, credential: Any) -> dict[str, Any]:
    """Stop future managed issuance; existing permits and exact history survive."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        _require(cases.jobs._operation(operation_id), "invalid_input", "Write issuer revocation needs a bounded operation ID")
        issuer = cases._path(issuer_directory)
        with _lock(issuer, deadline):
            body = _read_issuer(issuer, deadline)
            revocation = _read_revocation(issuer, body, deadline)
            _authorize(body, credential)
            if revocation is not None:
                _require(revocation["operation_id"] == operation_id,
                         "conflict", "Write issuer is already revoked by another exact operation")
                result = _show(body, revocation)
                _bounded(result, MAX_PUBLIC_BYTES, deadline)
                return result
            revoked_at = time.time()
            _require(cases._timestamp(revoked_at), "io_failed", "Write issuer revocation clock is unsupported")
            _require(revoked_at >= body["created_at"], "clock_regressed", "Write issuer revocation clock precedes its authority")
            record = {"schema_version": 1, "rules_version": RULES_VERSION, "issuer_id": body["issuer_id"],
                      "operation_id": operation_id, "revoked_at": revoked_at, "grant_digest": body["request_digest"]}
            record["request_digest"] = digest(record)
            _bounded(record, MAX_REVOCATION_BYTES, deadline)
            result = _show(body, record)
            _bounded(result, MAX_PUBLIC_BYTES, deadline)
            store_backup._write_json(issuer / REVOCATION_NAME, record, MAX_REVOCATION_BYTES)
            store_backup._fsync_directory(issuer)
            _deadline(deadline)
            return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_io_failed", "Write issuer could not be revoked") from error


@contextmanager
def _capture(issuer: Path, issuance: Path, permit: Path, action: Path, deadline: float) -> Iterator[dict[str, Any]]:
    with permits._capture(permit, action, deadline) as captured:
        body = actions._read_intent(action, deadline)
        excluded = {body["action_path"], body["ledger_anchor"]["ledger_path"],
                    body["catalog_anchor"]["catalog_path"], body["member"]["job_path"]}
        _require(not {str(issuer), str(issuance), str(permit)}.intersection(excluded),
                 "invalid_input", "Managed issuance must remain separate from all known action bindings")
        yield captured
        _deadline(deadline)


def _match(body: dict[str, Any], principal: str, action: dict[str, Any]) -> dict[str, Any]:
    matches = [rule for rule in body["policy"] if rule["principal"] == principal
               and all(rule[key] == action["lease"][key] for key in ("worker", "ledger_id", "index"))
               and all(rule[key] == action[key] for key in ("before_state", "target_state"))]
    _require(len(matches) == 1, "policy_denied", "Write issuer policy does not allow this exact action scope")
    return matches[0]


def _issue_replay(issuer: Path, issuance: Path, issuer_body: dict[str, Any], *, operation_id: str,
                  request: dict[str, Any], deadline: float) -> dict[str, Any]:
    with _lock(issuance, deadline, issuance=True):
        body = _read_issuance(issuance, deadline)
        _require(body["operation_id"] == operation_id
                 and all(body["request"][key] == request[key] for key in REQUEST_FIELDS - {"holder_credential_hash"})
                 and hmac.compare_digest(body["request"]["holder_credential_hash"], request["holder_credential_hash"])
                 and _same(body["issuer_anchor"], _issuer_pins(issuer, issuer_body, deadline))
                 and _same(body["issuer"], _issuer(issuer_body)),
                 "conflict", "Write issuance is bound to another exact managed request")
        result = _issuance(body)
        _bounded(result, MAX_PUBLIC_BYTES, deadline)
        return result


def issue_review_write_permit(issuer_directory: str | Path, issuance_directory: str | Path,
                              permit_directory: str | Path, action_directory: str | Path, *, operation_id: Any,
                              principal: Any, issuer_credential: Any, credential: Any) -> dict[str, Any]:
    """Create one new native permit and seal an immutable policy match receipt."""
    deadline = time.monotonic() + DEADLINE_SECONDS
    try:
        store_backup._support()
        issuer, issuance, permit, action = (cases._path(path) for path in
                                           (issuer_directory, issuance_directory, permit_directory, action_directory))
        _require(len({issuer, issuance, permit, action}) == 4
                 and all(path.parent == issuer.parent for path in (issuance, permit, action))
                 and cases.jobs._operation(operation_id) and cases.jobs._operation(principal),
                 "invalid_input", "Managed issuance needs four distinct sibling paths and bounded identifiers")
        with _lock(issuer, deadline):
            issuer_body = _read_issuer(issuer, deadline)
            revocation = _read_revocation(issuer, issuer_body, deadline)
            _authorize(issuer_body, issuer_credential)
            holder_hash = _hash(credential)
            _require(not hmac.compare_digest(holder_hash, issuer_body["credential_hash"]),
                     "invalid_input", "Issuer and permit holder need separate local bearer credentials")
            request = {"action_path": str(action), "permit_path": str(permit), "principal": principal,
                       "holder_credential_hash": holder_hash}
            if os.path.lexists(issuance):
                return _issue_replay(issuer, issuance, issuer_body, operation_id=operation_id, request=request, deadline=deadline)
            _require(revocation is None, "revoked", "Write issuer has been revoked for future managed issuance")
            _require(not os.path.lexists(permit), "conflict", "New managed issuance cannot adopt an existing native permit")
            with _capture(issuer, issuance, permit, action, deadline) as preliminary:
                _match(issuer_body, principal, preliminary["action"])
            try:
                issuance.mkdir(mode=0o700)
            except FileExistsError:
                return _issue_replay(issuer, issuance, issuer_body, operation_id=operation_id, request=request, deadline=deadline)
            issuance.chmod(0o700)
            with _lock(issuance, deadline, issuance=True, create=True):
                try:
                    permit.mkdir(mode=0o700)
                except FileExistsError:
                    raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_conflict",
                                         "Native permit appeared during managed reservation") from None
                permit.chmod(0o700)
                with permits._lock(permit, deadline, create=True):
                    with _capture(issuer, issuance, permit, action, deadline) as captured:
                        _require(_same(captured, preliminary), "source_changed", "Managed action changed during reservation")
                        rule = _match(issuer_body, principal, captured["action"])
                        created_at = time.time()
                        _require(cases._timestamp(created_at), "io_failed", "Managed native permit clock is unsupported")
                        _require(created_at >= max(captured["action"]["created_at"], issuer_body["created_at"]),
                                 "clock_regressed", "Managed native permit clock precedes its authority or action")
                        native = {"schema_version": 1, "rules_version": permits.RULES_VERSION,
                                  "permit_id": "wrrwpm_" + secrets.token_hex(16), "operation_id": operation_id,
                                  "created_at": created_at, "permit_path": str(permit),
                                  "parent": cases._directory(permit.parent), "permit_directory": cases._directory(permit),
                                  "principal": principal, "credential_hash": holder_hash, **captured}
                        native["request_digest"] = digest(native)
                        _bounded(native, permits.MAX_GRANT_BYTES, deadline)
                        native_public = permits._grant(native)
                        _bounded(native_public, permits.MAX_PUBLIC_BYTES, deadline)
                        store_backup._write_json(permit / permits.GRANT_NAME, native, permits.MAX_GRANT_BYTES)
                        store_backup._fsync_directory(permit)
                        store_backup._fsync_directory(permit.parent)
                        permits._names(permit)
                        issued_at = time.time()
                        _require(cases._timestamp(issued_at), "io_failed", "Write issuance clock is unsupported")
                        _require(issued_at >= created_at, "clock_regressed", "Write issuance clock precedes its native permit")
                        body = {"schema_version": 1, "rules_version": RULES_VERSION,
                                "issuance_id": "wrrwpis_" + secrets.token_hex(16), "operation_id": operation_id,
                                "created_at": issued_at, "issuance_path": str(issuance),
                                "parent": cases._directory(issuance.parent), "issuance_directory": cases._directory(issuance),
                                "issuer_anchor": _issuer_pins(issuer, issuer_body, deadline), "issuer": _issuer(issuer_body),
                                "rule": rule, "permit_anchor": _permit_pins(permit, native, deadline),
                                "permit": native_public, "request": request}
                        body["request_digest"] = digest(body)
                        _bounded(body, MAX_ISSUANCE_BYTES, deadline)
                        result = _issuance(body)
                        _bounded(result, MAX_PUBLIC_BYTES, deadline)
                        store_backup._write_json(issuance / ISSUANCE_NAME, body, MAX_ISSUANCE_BYTES)
                        store_backup._fsync_directory(issuance)
                        store_backup._fsync_directory(issuance.parent)
                        _names(issuance, issuance=True)
                        _deadline(deadline)
                        return result
    except WorkbenchError as error:
        raise _translate(error) from error
    except _ERRORS as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_io_failed", "Managed write permit could not be issued") from error


def inspect_review_write_permit_issuance(issuance_directory: str | Path) -> dict[str, Any]:
    """Read complete own historical evidence without issuer, native or action."""
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
        raise WorkbenchError("workspace_restore_case_retention_review_write_issuer_io_failed", "Write issuance could not be inspected") from error


def _translate(error: WorkbenchError) -> WorkbenchError:
    if error.code.startswith("workspace_restore_case_retention_review_write_issuer_"):
        return error
    for suffix in ("limit_exceeded", "private_path_required", "invalid_input", "incomplete", "source_changed", "conflict",
                   "credential_invalid", "revoked", "clock_regressed", "io_failed"):
        if error.code.endswith(suffix):
            return WorkbenchError("workspace_restore_case_retention_review_write_issuer_" + suffix,
                                  "Write issuer exceeded a bound or has unsupported current bindings")
    return WorkbenchError("workspace_restore_case_retention_review_write_issuer_record_invalid",
                          "Write issuer controls or saved bindings cannot be validated")
