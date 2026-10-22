"""Local bearer permits scoped to one historical review-write action.

Bearer values and their digests stay in private credential/control files.
Public validation never reads filesystem, authority state or current jobs.
"""

from __future__ import annotations

import argparse
import math
import os
from pathlib import Path
import sqlite3
import stat
from typing import Any

from .governance_cli import _credential_value, prepare_credential
from .store import WorkbenchError, canonical
from . import store_backup
from .workspace_restore_case_retention_review_write_action_cli import _public_action, _public_wrapper
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-permit-1"
_PREFIX = "workspace_restore_case_retention_review_write_permit_"
_FLAGS = {
    "advisory_only": True,
    "inspection_only": True,
    "domain_writes_allowed": False,
    "original_files_modified": False,
    "archival_authorized": False,
    "deletion_authorized": False,
    "actor_authenticated": False,
    "old_authority_activated": False,
    "external_fencing_established": False,
    "coordination_only": True,
    "job_steps_enforced": False,
    "same_ledger_only": True,
    "worker_authenticated": False,
    "lease_required_for_new_execution": True,
    "local_capability_only": True,
    "external_identity_verified": False,
}
_GRANT_FIELDS = {"rules_version", "permit_id", "operation_id", "created_at", "principal", "action", *_FLAGS}
_SHOW_FIELDS = {"rules_version", "permit", "state", "revocation", *_FLAGS}
_REVOCATION_FIELDS = {"rules_version", "permit_id", "operation_id", "revoked_at", "reason"}
_EXECUTION_FIELDS = {
    "rules_version", "permit", "action_result", "credential_verified", "revocation_checked",
    "historical_effect_authenticated", *_FLAGS,
}
_ERROR_CODES = {
    _PREFIX + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete", "record_invalid",
        "source_changed", "credential_invalid", "revoked", "clock_regressed", "lease_changed", "lease_expired",
        "phase_changed", "unsupported_state", "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Review write permit API returned an unsupported public response")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items())


def _action(value: Any) -> dict[str, Any]:
    try:
        return _public_action(value)
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Permit has an unsupported historical action") from error


def _action_result(value: Any) -> dict[str, Any]:
    try:
        result = _public_wrapper(value)
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Permit has an unsupported historical action result") from error
    _require(result["state"] == "recorded")
    return result


def _public_grant(value: Any) -> dict[str, Any]:
    """Validate fixed capability scope, without asserting current permission."""
    value = _object(value, _GRANT_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["permit_id"], "wrrwpm")
             and _identifier(value["operation_id"]) and _identifier(value["principal"])
             and _timestamp(value["created_at"]) and _flags(value))
    action = _action(value["action"])
    _require(value["created_at"] >= action["created_at"])
    return {"rules_version": _RULES_VERSION,
            **{key: value[key] for key in ("permit_id", "operation_id", "created_at", "principal")},
            "action": action, **_FLAGS}


def _public_show(value: Any) -> dict[str, Any]:
    """Observe only own grant/revocation, without checking any credential."""
    value = _object(value, _SHOW_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and isinstance(value["state"], str)
             and value["state"] in {"active", "revoked"} and _flags(value))
    grant = _public_grant(value["permit"])
    revocation = None
    if value["state"] == "revoked":
        revocation = _object(value["revocation"], _REVOCATION_FIELDS)
        _require(revocation["rules_version"] == _RULES_VERSION and revocation["permit_id"] == grant["permit_id"]
                 and _identifier(revocation["operation_id"]) and _timestamp(revocation["revoked_at"])
                 and revocation["revoked_at"] >= grant["created_at"] and revocation["reason"] == "holder_revoked")
        revocation = {key: revocation[key] for key in sorted(_REVOCATION_FIELDS)}
    else:
        _require(value["revocation"] is None)
    return {"rules_version": _RULES_VERSION, "permit": grant, "state": value["state"],
            "revocation": revocation, **_FLAGS}


def _public_execution(value: Any) -> dict[str, Any]:
    """Keep current protected delivery separate from historical effect authorship."""
    value = _object(value, _EXECUTION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _flags(value)
             and type(value["credential_verified"]) is bool and value["credential_verified"] is True
             and type(value["revocation_checked"]) is bool and value["revocation_checked"] is True
             and type(value["historical_effect_authenticated"]) is bool
             and value["historical_effect_authenticated"] is False)
    grant, result = _public_grant(value["permit"]), _action_result(value["action_result"])
    _require(canonical(result["action"]) == canonical(grant["action"]))
    return {"rules_version": _RULES_VERSION, "permit": grant, "action_result": result,
            "credential_verified": True, "revocation_checked": True, "historical_effect_authenticated": False,
            **_FLAGS}


def _public_response(value: Any, action: str) -> dict[str, Any]:
    _require(action in {"issue", "show", "revoke", "execute"})
    if action == "issue":
        return _public_grant(value)
    if action == "execute":
        return _public_execution(value)
    result = _public_show(value)
    if action == "revoke":
        _require(result["state"] == "revoked")
    return result


def _credential_metadata(details: os.stat_result) -> tuple[int, ...]:
    return (details.st_dev, details.st_ino, details.st_uid, details.st_mode,
            details.st_nlink, details.st_size, details.st_mtime_ns, details.st_ctime_ns)


def _credential_path(path: str | Path, *, missing_final: bool = False) -> Path:
    store_backup._support()
    location = store_backup._path(path)
    if location.suffix != ".credential":
        raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential needs its private file suffix")
    store_backup._private_directory(location.parent)
    store_backup._no_symlinks(location, missing_final=missing_final)
    return location


def _read_private_credential(path: str | Path) -> str:
    """Read a bounded owner-only single-link credential without path aliases."""
    try:
        location = _credential_path(path)
        descriptor = os.open(location, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK)
        try:
            before = os.fstat(descriptor)
            if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.geteuid()
                    or stat.S_IMODE(before.st_mode) != 0o600 or before.st_nlink != 1
                    or not 1 <= before.st_size <= 256):
                raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential needs a private owner file")
            data = bytearray()
            while len(data) <= 256:
                block = os.read(descriptor, 257 - len(data))
                if not block:
                    break
                data.extend(block)
            store_backup._no_symlinks(location)
            if (len(data) != before.st_size or _credential_metadata(before) != _credential_metadata(os.fstat(descriptor))
                    or _credential_metadata(before) != _credential_metadata(location.lstat())):
                raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential changed while reading")
            try:
                return _credential_value(bytes(data))
            except WorkbenchError as error:
                raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential file is unsupported") from error
        finally:
            os.close(descriptor)
    except WorkbenchError as error:
        if isinstance(error.code, str) and error.code.startswith(_PREFIX):
            raise
        raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential could not be read privately") from error
    except OSError as error:
        raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential could not be read privately") from error


def _prepare_private_credential(path: str | Path) -> str:
    """Preserve a newly generated credential before registering its permit."""
    try:
        location = _credential_path(path, missing_final=True)
        value = prepare_credential(location)
        store_backup._fsync_directory(location.parent)
        if _read_private_credential(location) != value:
            raise WorkbenchError(_PREFIX + "private_path_required", "Prepared permit credential changed")
        return value
    except WorkbenchError as error:
        if isinstance(error.code, str) and error.code.startswith(_PREFIX):
            raise
        raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential could not be prepared privately") from error
    except OSError as error:
        raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential could not be saved privately") from error


def _permit_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--permit", required=True, type=Path,
                        help="Отдельный частный каталог допуска к одному закреплённому action.")


def register_restore_case_retention_review_write_permit_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write-permit",
        help="Выдать и отозвать локальный bearer-допуск к одному write action.",
        description=(
            "Issue связывает один закрытый bearer credential с точным action и локальной меткой principal. "
            "Execute проверяет этот credential и отсутствие отзыва при каждом вызове, включая выдачу старого результата. "
            "Тот же bearer может отозвать собственный permit. Show читает только metadata. "
            "Прежние action execute и write step остаются вне этой проверки; личность человека и прошлое авторство не подтверждаются."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_permit_command", required=True)
    issue = commands.add_parser("issue", help="Закрепить bearer-допуск к точному существующему action.")
    _permit_argument(issue)
    issue.add_argument("--action", required=True, type=Path, help="Существующий action, выбранный явно.")
    issue.add_argument("--operation-id", required=True, help="ID точного запроса выдачи этого permit.")
    issue.add_argument("--principal", required=True, help="Локальная метка владельца допуска; не удостоверяет личность человека.")
    credentials = issue.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--credential-out", type=Path, help="Новый файл .credential/0600: сохранить секрет до выдачи допуска.")
    credentials.add_argument("--prepared-credential", type=Path, help="Прежний частный .credential для точного повтора выдачи.")
    show = commands.add_parser("show", help="Наблюдать собственные grant/revocation без credential и live action.")
    _permit_argument(show)
    revoke = commands.add_parser("revoke", help="Необратимо отозвать собственный permit тем же bearer credential.")
    _permit_argument(revoke)
    revoke.add_argument("--operation-id", required=True, help="ID точного отзыва; другой ID после отзыва вызывает конфликт.")
    execute = commands.add_parser("execute", help="Проверить действующий bearer и вернуть результат выбранного action.")
    _permit_argument(execute)
    for command in (revoke, execute):
        command.add_argument("--credential-file", required=True, type=Path,
                             help="Частный .credential/0600; сам секрет не передаётся аргументом.")


def run_restore_case_retention_review_write_permit_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_permit_command
    if action not in {"issue", "show", "revoke", "execute"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_permit_command", "Unknown review write permit command")
    try:
        from . import workspace_restore_case_retention_review_write_permit

        permit_path = store_backup._path(args.permit)
        if action == "issue":
            credential_path = args.credential_out or args.prepared_credential
            location = store_backup._path(credential_path)
            action_path = store_backup._path(args.action)
            if (location.parent != permit_path.parent or location in {action_path, permit_path}
                    or location.parent in {action_path, permit_path}):
                raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential must be separate from action controls")
            credential = (_prepare_private_credential(location) if args.credential_out is not None
                          else _read_private_credential(location))
            result = workspace_restore_case_retention_review_write_permit.issue_review_write_permit(
                args.permit, args.action, operation_id=args.operation_id, principal=args.principal, credential=credential,
            )
        elif action == "show":
            result = workspace_restore_case_retention_review_write_permit.inspect_review_write_permit(args.permit)
        else:
            location = store_backup._path(args.credential_file)
            if location.parent != permit_path.parent or location == permit_path:
                raise WorkbenchError(_PREFIX + "private_path_required", "Permit credential needs a private sibling path")
            credential = _read_private_credential(location)
            if action == "revoke":
                result = workspace_restore_case_retention_review_write_permit.revoke_review_write_permit(
                    args.permit, operation_id=args.operation_id, credential=credential,
                )
            else:
                result = workspace_restore_case_retention_review_write_permit.execute_review_write_permit(
                    args.permit, credential=credential,
                )
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else _PREFIX + "command_failed"
        raise WorkbenchError(code, "Review write permit command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError(_PREFIX + "command_failed", "Review write permit command failed") from error
