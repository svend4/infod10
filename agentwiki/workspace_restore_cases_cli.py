"""Historical blocked-case receipts and explicit associations to existing jobs.

Only fixed public receipts and separately labelled current observations reach
stdout. These commands do not prepare jobs, repair original files or activate
historical authority. Private evidence, paths and fingerprints stay in the core.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError
from .workspace_restore_job_cli import _address, _identifier, _public_status as _restore_status


_RULES_VERSION = "workspace-restore-case-1"
_FLAGS = {
    "inspection_only": True,
    "domain_writes_allowed": False,
    "original_files_modified": False,
    "old_authority_activated": False,
    "external_fencing_established": False,
}
_CASE_FLAGS = {**_FLAGS, "original_identity_reconstructed": False}
_LINK_FLAGS = {
    **_FLAGS, "operator_association": True,
    "fresh_creation_verified": False, "request_equivalence_verified": False,
}
_CASE_FIELDS = {
    "rules_version", "case_id", "operation_id", "created_at", "cause",
    "original_error_code", "original_identity", *_CASE_FLAGS,
}
_LINK_FIELDS = {
    "rules_version", "case_id", "link_id", "operation_id", "created_at",
    "attempt_status", *_LINK_FLAGS,
}
_INSPECTION_FIELDS = {"rules_version", "case", "link", "original_evidence", "current_attempt"}
_IDENTITY_FIELDS = {"job_id", "operation_id", "created_at", "workspace_id"}
_STATUS_IDENTITY = (*sorted(_IDENTITY_FIELDS), "total_file_count")
_CAUSE_ERRORS = {
    "plan_invalid": "workspace_restore_job_manifest_invalid",
    "ownership_unproven": "workspace_restore_job_conflict",
    "owner_invalid": "workspace_restore_job_manifest_invalid",
}
_EVIDENCE_ERRORS = {
    "unchanged": None,
    "changed": "workspace_restore_case_evidence_changed",
    "unavailable": "workspace_restore_case_evidence_unavailable",
}
_ATTEMPT_ERRORS = {
    "not_linked": None,
    "available": None,
    "changed": "workspace_restore_case_attempt_changed",
    "unavailable": "workspace_restore_case_attempt_unavailable",
}
_ERROR_CODES = {
    "workspace_restore_case_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict",
        "incomplete", "record_invalid", "unsupported_state", "evidence_changed",
        "evidence_unavailable", "attempt_changed", "attempt_unavailable", "io_failed",
        "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_summary_invalid",
            "Restore case API returned an unsupported public response",
        )


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any], expected: dict[str, bool]) -> bool:
    return all(type(value[key]) is bool and value[key] is flag for key, flag in expected.items())


def _public_identity(value: Any) -> dict[str, Any]:
    value = _object(value, _IDENTITY_FIELDS)
    _require(_address(value["job_id"], "wrj") and _identifier(value["operation_id"])
             and _timestamp(value["created_at"]) and _address(value["workspace_id"], "wsp"))
    return {key: value[key] for key in sorted(_IDENTITY_FIELDS)}


def _public_status(value: Any) -> dict[str, Any]:
    try:
        return _restore_status(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_summary_invalid",
            "Restore case response has an unsupported public job status",
        ) from error


def _public_case(value: Any) -> dict[str, Any]:
    """Validate an immutable historical receipt without inferring missing IDs."""
    value = _object(value, _CASE_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["case_id"], "wrc")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and isinstance(value["cause"], str) and value["cause"] in _CAUSE_ERRORS
             and value["original_error_code"] == _CAUSE_ERRORS[value["cause"]]
             and _flags(value, _CASE_FLAGS))
    identity = None
    if value["cause"] == "plan_invalid":
        _require(value["original_identity"] is None)
    else:
        identity = _public_identity(value["original_identity"])
    result = {key: value[key] for key in sorted(_CASE_FIELDS)}
    result["original_identity"] = identity
    return result


def _public_link(value: Any) -> dict[str, Any]:
    """Validate an operator association and the fixed job response it captured."""
    value = _object(value, _LINK_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["case_id"], "wrc")
             and _address(value["link_id"], "wrl") and _identifier(value["operation_id"])
             and _timestamp(value["created_at"]) and _flags(value, _LINK_FLAGS))
    result = {key: value[key] for key in sorted(_LINK_FIELDS)}
    result["attempt_status"] = _public_status(value["attempt_status"])
    return result


def _public_inspection(value: Any) -> dict[str, Any]:
    """Keep historical receipts separate from bounded current observations."""
    value = _object(value, _INSPECTION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION)
    case = _public_case(value["case"])
    link = None if value["link"] is None else _public_link(value["link"])
    if link is not None:
        _require(link["case_id"] == case["case_id"])
    evidence = _object(value["original_evidence"], {"state", "error_code"})
    _require(isinstance(evidence["state"], str) and evidence["state"] in _EVIDENCE_ERRORS
             and evidence["error_code"] == _EVIDENCE_ERRORS[evidence["state"]])
    attempt = _object(value["current_attempt"], {"state", "status", "error_code"})
    _require(isinstance(attempt["state"], str) and attempt["state"] in _ATTEMPT_ERRORS
             and attempt["error_code"] == _ATTEMPT_ERRORS[attempt["state"]]
             and (attempt["state"] == "not_linked") is (link is None))
    status = None
    if attempt["state"] == "available":
        status = _public_status(attempt["status"])
        _require(link is not None and all(status[key] == link["attempt_status"][key]
                                         for key in _STATUS_IDENTITY))
    else:
        _require(attempt["status"] is None)
    return {
        "rules_version": _RULES_VERSION, "case": case, "link": link,
        "original_evidence": {key: evidence[key] for key in sorted(evidence)},
        "current_attempt": {"state": attempt["state"], "status": status, "error_code": attempt["error_code"]},
    }


def _public_response(value: Any, action: str) -> dict[str, Any]:
    if action == "record":
        return _public_case(value)
    if action == "link-attempt":
        return _public_link(value)
    _require(action == "show")
    return _public_inspection(value)


def _case_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--case", required=True, type=Path,
                        help="Отдельный частный каталог исторической квитанции случая.")


def register_restore_cases_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case", help="Записать отказ восстановления и явную связь с существующим заданием.",
        description=(
            "Record сохраняет исторический факт поддерживаемого отказа. Link-attempt связывает случай "
            "с уже существующим заданием по указанию оператора. Show отдельно наблюдает текущие пути. "
            "Команды не создают задания восстановления, не ремонтируют исходные записи и не выдают полномочия. "
            "Частные пути, отпечатки и содержимое доказательств не выводятся."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_command", required=True)
    record = commands.add_parser("record", help="Сохранить историческую квитанцию поддерживаемого отказа.")
    record.add_argument("--job", required=True, type=Path, help="Частное исходное заблокированное задание.")
    _case_argument(record)
    record.add_argument("--operation-id", required=True, help="ID точного запроса записи случая.")
    show = commands.add_parser("show", help="Прочитать квитанции и отдельно текущие наблюдения.")
    _case_argument(show)
    link = commands.add_parser(
        "link-attempt", help="Записать одну связь с уже существующим заданием восстановления.",
        description=(
            "Связь задаётся оператором и не доказывает создание нового задания или равенство запросов. "
            "Обычный workspace-restore prepare выполняется отдельно. Исторический status в квитанции фиксирован."
        ),
    )
    _case_argument(link)
    link.add_argument("--job", required=True, type=Path, help="Уже существующее другое частное задание.")
    link.add_argument("--operation-id", required=True, help="ID точного запроса записи связи.")


def run_restore_cases_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_command
    if action not in {"record", "show", "link-attempt"}:
        raise WorkbenchError("unknown_restore_case_command", "Unknown restore case command")
    try:
        from . import workspace_restore_cases

        if action == "record":
            result = workspace_restore_cases.record_blocked_case(args.job, args.case, operation_id=args.operation_id)
        elif action == "link-attempt":
            result = workspace_restore_cases.link_attempt(args.case, args.job, operation_id=args.operation_id)
        else:
            result = workspace_restore_cases.inspect_case(args.case)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else "workspace_restore_case_command_failed"
        raise WorkbenchError(code, "Restore case command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_command_failed", "Restore case command failed") from error
