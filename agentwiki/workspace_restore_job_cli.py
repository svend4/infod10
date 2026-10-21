"""Commands for bounded continuation of private historical restore jobs.

Only fixed progress fields and the existing safe restore summary reach stdout.
The core owns filesystem validation; this command never initializes a Store or
forwards private plans, source paths, staging names or checksum pins.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

from .store import WorkbenchError
from .workspace_backup_cli import _public_summary as _backup_summary


_RULES_VERSION = "workspace-restore-job-1"
_FLAGS = {
    "inspection_only": True,
    "domain_writes_allowed": False,
    "old_authority_activated": False,
    "external_fencing_established": False,
}
_PUBLIC_FIELDS = {
    "rules_version", "job_id", "operation_id", "created_at", "workspace_id",
    "state", "copied_file_count", "total_file_count", "completed",
    "full_history_preserved", "result", *_FLAGS,
}
_STATES = {"prepared", "copying", "ready", "completed"}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
_BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,150}\Z")


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_job_summary_invalid",
            "Restore job API returned an unsupported public summary",
        )


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _address(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(prefix + r"_[0-9a-f]{32}", value) is not None


def _identifier(value: Any) -> bool:
    return (isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None
            and _BEARER.search(value) is None and _HASH.fullmatch(value) is None)


def _public_status(value: Any) -> dict[str, Any]:
    """Validate each public layer without exposing private job metadata."""
    _require(isinstance(value, dict) and set(value) == _PUBLIC_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION
             and _address(value["job_id"], "wrj")
             and _identifier(value["operation_id"])
             and _address(value["workspace_id"], "wsp")
             and type(value["created_at"]) in {int, float}
             and math.isfinite(value["created_at"])
             and 0 < value["created_at"] < 2**63
             and isinstance(value["state"], str) and value["state"] in _STATES
             and _integer(value["copied_file_count"])
             and _integer(value["total_file_count"])
             and 2 <= value["total_file_count"] <= 17
             and value["copied_file_count"] <= value["total_file_count"]
             and type(value["completed"]) is bool
             and type(value["full_history_preserved"]) is bool
             and all(type(value[key]) is bool and value[key] is expected
                     for key, expected in _FLAGS.items()))
    completed = value["completed"]
    copied, total, state = value["copied_file_count"], value["total_file_count"], value["state"]
    _require(completed is (state == "completed")
             and value["full_history_preserved"] is completed
             and (state != "prepared" or copied == 0)
             and (state != "copying" or 0 < copied < total)
             and (state not in {"ready", "completed"} or copied == total))
    result = None
    if completed:
        try:
            result = _backup_summary(value["result"], [], "restore")
        except WorkbenchError as error:
            raise WorkbenchError(
                "workspace_restore_job_summary_invalid",
                "Restore job result has an unsupported public restore summary",
            ) from error
        _require(result["workspace_id"] == value["workspace_id"]
                 and result["archive_count"] + 1 == total)
    else:
        _require(value["result"] is None)
    public = {key: value[key] for key in sorted(_PUBLIC_FIELDS)}
    public["result"] = result
    return public


def _file_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите целое число файлов от 1 до 16.") from error
    if not 1 <= limit <= 16:
        raise argparse.ArgumentTypeError("За один шаг можно перенести от 1 до 16 файлов.")
    return limit


def _job_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job", required=True, type=Path,
                        help="Частный каталог сохранённой операции восстановления.")


def register_restore_job_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register preparation recovery, bounded copying and verified progress."""
    parser = subparsers.add_parser(
        "workspace-restore", help="Пошагово восстановить полный комплект истории в карантине.",
        description=(
            "Prepare закрепляет исходный полный комплект, назначение и operation ID в частной операции. "
            "Каталоги job и destination должны быть соседями в одном частном родительском каталоге. "
            "Resume-prepare завершает подготовку metadata без исходного комплекта и копирования данных. "
            "Step переносит ограниченное число файлов; status показывает проверенный прогресс. "
            "Восстановленная база сохраняет карантин. Частные планы, пути и контрольные суммы не выводятся."
        ),
    )
    commands = parser.add_subparsers(dest="restore_job_command", required=True)
    prepare = commands.add_parser("prepare", help="Закрепить полный комплект и назначение новой операции.")
    prepare.add_argument("--bundle", required=True, type=Path,
                         help="Готовый частный workspace-backup комплект базы и всех архивов.")
    prepare.add_argument("--destination", required=True, type=Path,
                         help="Частное назначение; чужие существующие данные не заменяются.")
    _job_argument(prepare)
    prepare.add_argument("--operation-id", required=True,
                         help="Сохранённый ID точного запроса для его повтора и продолжения.")
    resume_prepare = commands.add_parser(
        "resume-prepare", help="Завершить собственную подготовку metadata без копирования данных.",
        description=(
            "Продолжить подготовку по сохранённому частному плану без исходного комплекта. "
            "Команда восстанавливает только предусмотренные metadata собственного назначения; "
            "DB и архивы переносятся отдельной командой step. Карантин сохраняется."
        ),
    )
    _job_argument(resume_prepare)
    step = commands.add_parser("step", help="Перенести следующий ограниченный набор файлов и сохранить прогресс.")
    _job_argument(step)
    step.add_argument("--limit", type=_file_limit, default=1,
                      help="Число новых файлов за шаг, 1–16; по умолчанию 1.")
    status = commands.add_parser("status", help="Прочитать проверенный прогресс или прежний завершённый результат.")
    _job_argument(status)


def run_restore_job_command(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch without SQL initialization or forwarding private core details."""
    action = args.restore_job_command
    if action not in {"prepare", "resume-prepare", "step", "status"}:
        raise WorkbenchError("unknown_restore_job_command", "Unknown restore job command")
    try:
        from . import workspace_restore_job

        if action == "prepare":
            result = workspace_restore_job.prepare_restore(
                args.bundle, args.destination, args.job, operation_id=args.operation_id,
            )
        elif action == "resume-prepare":
            result = workspace_restore_job.resume_preparation(args.job)
        elif action == "step":
            result = workspace_restore_job.step_restore(args.job, limit=args.limit)
        else:
            result = workspace_restore_job.restore_status(args.job)
        return _public_status(result)
    except WorkbenchError as error:
        code = (error.code if isinstance(error.code, str) and _ERROR_CODE.fullmatch(error.code)
                and error.code.startswith(("workspace_restore_job_", "workspace_backup_", "store_snapshot_",
                                           "store_transfer_", "source_transfer_", "snapshot_"))
                else "workspace_restore_job_command_failed")
        raise WorkbenchError(code, "Restore job command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_job_command_failed", "Restore job command failed") from error
