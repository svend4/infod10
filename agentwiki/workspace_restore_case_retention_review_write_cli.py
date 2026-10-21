"""Commands and strict public receipts for bounded review-control writing.

The intended historical review is visible before publication. Only completed
status carries a result compatible with the existing immutable review reader.
Private intents, inode bindings, staging paths and checksums never reach stdout.
Public validation performs no filesystem or database reads.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError, canonical
from .workspace_restore_case_retention_review_cli import _public_review as _review
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-1"
_STATES = {"prepared", "staging", "ready", "linked", "completed"}
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
}
_PUBLIC_FIELDS = {
    "rules_version", "write_id", "operation_id", "created_at", "state", "completed",
    "intended_review", "result", *_FLAGS,
}
_ERROR_CODES = {
    "workspace_restore_case_retention_review_write_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete",
        "record_invalid", "unsupported_state", "source_changed", "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_summary_invalid",
            "Review write API returned an unsupported public response",
        )


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _public_review(value: Any) -> dict[str, Any]:
    try:
        return _review(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_summary_invalid",
            "Review write has an unsupported historical review snapshot",
        ) from error


def _public_status(value: Any) -> dict[str, Any]:
    """Validate intended history separately from completed review publication."""
    _require(isinstance(value, dict) and set(value) == _PUBLIC_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION
             and _address(value["write_id"], "wrrw")
             and _identifier(value["operation_id"])
             and _timestamp(value["created_at"])
             and isinstance(value["state"], str) and value["state"] in _STATES
             and type(value["completed"]) is bool
             and all(type(value[key]) is bool and value[key] is expected
                     for key, expected in _FLAGS.items()))
    intended = _public_review(value["intended_review"])
    _require(value["operation_id"] == intended["operation_id"]
             and _same(value["created_at"], intended["created_at"]))
    completed = value["state"] == "completed"
    _require(value["completed"] is completed)
    result = None
    if completed:
        result = _public_review(value["result"])
        _require(_same(result, intended))
    else:
        _require(value["result"] is None)
    return {
        "rules_version": _RULES_VERSION,
        **{key: value[key] for key in ("write_id", "operation_id", "created_at", "state")},
        "completed": completed, "intended_review": intended, "result": result, **_FLAGS,
    }


def _public_response(value: Any, action: str) -> dict[str, Any]:
    _require(action in {"prepare", "status", "step"})
    return _public_status(value)


def _job_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job", required=True, type=Path,
                        help="Отдельный частный каталог сохранённой операции записи review.")


def register_restore_case_retention_review_write_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write",
        help="Закрепить review и продолжить запись его управляющих файлов отдельным шагом.",
        description=(
            "Prepare сохраняет точное намерение записи исторического review и собственные файловые привязки. "
            "Status только наблюдает подготовленную операцию. Step завершает собственные управляющие файлы "
            "без исходного плана; completed возвращает совместимый исторический review. "
            "Участник самозаявленный, архивирование, удаление и доменные записи не разрешаются."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_command", required=True)
    prepare = commands.add_parser("prepare", help="Сохранить точное намерение записи целого исторического review.")
    prepare.add_argument("--plan", required=True, type=Path, help="Существующий частный retention plan directory.")
    prepare.add_argument("--review", required=True, type=Path, help="Новое частное назначение совместимого review.")
    _job_argument(prepare)
    prepare.add_argument("--actor", required=True, help="Самозаявленный ID участника; аутентификацией не является.")
    prepare.add_argument("--verdict", required=True, help="reviewed, defer либо request_fresh_plan; разрешений не выдаёт.")
    prepare.add_argument("--operation-id", required=True, help="ID точного запроса записи review.")
    status = commands.add_parser("status", help="Наблюдать состояние записи без изменения управляющих файлов.")
    _job_argument(status)
    step = commands.add_parser("step", help="Завершить собственную запись review по сохранённому намерению.")
    _job_argument(step)


def run_restore_case_retention_review_write_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_command
    if action not in {"prepare", "status", "step"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_command", "Unknown review write command")
    try:
        from . import workspace_restore_case_retention_review_write

        if action == "prepare":
            result = workspace_restore_case_retention_review_write.prepare_review_write(
                args.plan, args.review, args.job,
                operation_id=args.operation_id, actor=args.actor, verdict=args.verdict,
            )
        elif action == "status":
            result = workspace_restore_case_retention_review_write.inspect_review_write(args.job)
        else:
            result = workspace_restore_case_retention_review_write.step_review_write(args.job)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = (error.code if isinstance(error.code, str) and error.code in _ERROR_CODES
                else "workspace_restore_case_retention_review_write_command_failed")
        raise WorkbenchError(code, "Review write command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_command_failed", "Review write command failed") from error
