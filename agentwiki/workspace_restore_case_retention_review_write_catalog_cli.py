"""Strict public pages for explicitly selected immutable review-write jobs.

The fixed catalog names compact historical write descriptors. Each page carries
a separate dated observation of current write status, preserving changed or
unavailable members without advancing jobs or inspecting their old sources.
Public validation performs no filesystem or database reads.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError, canonical
from .workspace_restore_case_retention_review_write_cli import _public_status as _write_status
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-catalog-1"
_WRITE_RULES_VERSION = "workspace-restore-case-retention-review-write-1"
_VERDICTS = {"reviewed", "defer", "request_fresh_plan"}
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
    "membership_frozen": True,
    "order_frozen": True,
}
_CATALOG_FIELDS = {"rules_version", "catalog_id", "operation_id", "created_at", "write_count", *_FLAGS}
_WRITE_FIELDS = {"rules_version", "write_id", "operation_id", "created_at", "review_id", "actor", "verdict"}
_PAGE_FLAGS = {"source_inputs_checked": False, "jobs_advanced": False}
_PAGE_FIELDS = {"rules_version", "catalog", "checked_at", "after_index", "limit", "entries", "next_cursor", *_PAGE_FLAGS}
_ENTRY_FIELDS = {"index", "write", "current_write"}
_CURRENT_FIELDS = {"state", "status", "error_code"}
_CURSOR_FIELDS = {"catalog_id", "after_index"}
_CURRENT_ERRORS = {
    "available": None,
    "changed": "workspace_restore_case_retention_review_write_catalog_entry_changed",
    "unavailable": "workspace_restore_case_retention_review_write_catalog_entry_unavailable",
}
_ERROR_CODES = {
    "workspace_restore_case_retention_review_write_catalog_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete",
        "record_invalid", "unsupported_state", "source_changed", "entry_changed", "entry_unavailable",
        "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_catalog_summary_invalid",
            "Review write catalog API returned an unsupported public response",
        )


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any], expected: dict[str, bool]) -> bool:
    return all(type(value[key]) is bool and value[key] is flag for key, flag in expected.items())


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _public_write(value: Any) -> dict[str, Any]:
    """Validate the compact historical descriptor of one fixed write intent."""
    value = _object(value, _WRITE_FIELDS)
    _require(value["rules_version"] == _WRITE_RULES_VERSION
             and _address(value["write_id"], "wrrw") and _address(value["review_id"], "wrrv")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and _identifier(value["actor"]) and isinstance(value["verdict"], str)
             and value["verdict"] in _VERDICTS)
    return {key: value[key] for key in sorted(_WRITE_FIELDS)}


def _public_catalog(value: Any) -> dict[str, Any]:
    """Validate the immutable catalog receipt without private member paths."""
    value = _object(value, _CATALOG_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["catalog_id"], "wrrwcat")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and _integer(value["write_count"], 1, 256) and _flags(value, _FLAGS))
    return {key: value[key] for key in sorted(_CATALOG_FIELDS)}


def _public_current(value: Any, write: dict[str, Any]) -> dict[str, Any]:
    value = _object(value, _CURRENT_FIELDS)
    _require(isinstance(value["state"], str) and value["state"] in _CURRENT_ERRORS
             and value["error_code"] == _CURRENT_ERRORS[value["state"]])
    status = None
    if value["state"] == "available":
        try:
            status = _write_status(value["status"])
        except WorkbenchError as error:
            raise WorkbenchError(
                "workspace_restore_case_retention_review_write_catalog_summary_invalid",
                "Review write catalog has an unsupported current write status",
            ) from error
        observed = {
            **{key: status[key] for key in ("rules_version", "write_id", "operation_id", "created_at")},
            **{key: status["intended_review"][key] for key in ("review_id", "actor", "verdict")},
        }
        _require(_same(observed, write))
    else:
        _require(value["status"] is None)
    return {"state": value["state"], "status": status, "error_code": value["error_code"]}


def _public_entry(value: Any, expected_index: int) -> dict[str, Any]:
    value = _object(value, _ENTRY_FIELDS)
    _require(type(value["index"]) is int and value["index"] == expected_index)
    write = _public_write(value["write"])
    return {"index": value["index"], "write": write,
            "current_write": _public_current(value["current_write"], write)}


def _public_page(value: Any) -> dict[str, Any]:
    """Validate exact page bounds, cursor arithmetic and current identity pins."""
    value = _object(value, _PAGE_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _timestamp(value["checked_at"])
             and _flags(value, _PAGE_FLAGS))
    catalog = _public_catalog(value["catalog"])
    count, after, limit = catalog["write_count"], value["after_index"], value["limit"]
    _require(_integer(after, 0, count) and _integer(limit, 1, 16)
             and isinstance(value["entries"], list) and len(value["entries"]) == min(limit, count - after))
    entries = [_public_entry(item, after + offset + 1) for offset, item in enumerate(value["entries"])]
    _require(len({entry["write"]["write_id"] for entry in entries}) == len(entries)
             and len({entry["write"]["review_id"] for entry in entries}) == len(entries))
    end = after + len(entries)
    cursor = None
    if end < count:
        cursor = _object(value["next_cursor"], _CURSOR_FIELDS)
        _require(cursor["catalog_id"] == catalog["catalog_id"]
                 and type(cursor["after_index"]) is int and cursor["after_index"] == end)
        cursor = {"catalog_id": cursor["catalog_id"], "after_index": cursor["after_index"]}
    else:
        _require(value["next_cursor"] is None)
    return {"rules_version": _RULES_VERSION, "catalog": catalog, "checked_at": value["checked_at"],
            "after_index": after, "limit": limit, "entries": entries, "next_cursor": cursor, **_PAGE_FLAGS}


def _public_response(value: Any, action: str) -> dict[str, Any]:
    if action in {"create", "show"}:
        return _public_catalog(value)
    _require(action == "page")
    return _public_page(value)


def _catalog_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", required=True, type=Path,
                        help="Отдельный частный каталог явно выбранных заданий записи review.")


def register_restore_case_retention_review_write_catalog_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write-catalog",
        help="Закрепить явный состав write jobs и читать ограниченные страницы их состояний.",
        description=(
            "Create сохраняет явно переданные write jobs в указанном порядке. Show читает историческую "
            "квитанцию каталога. Page отдельно наблюдает текущие write status, сохраняя недоступные "
            "или изменённые записи. Исходные plan/catalog/case/job inputs не проверяются, step не вызывается. "
            "Аренды обработчиков и разрешения доменных действий не предоставляются."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_catalog_command", required=True)
    create = commands.add_parser("create", help="Сохранить неизменяемый перечень из 1–256 выбранных write jobs.")
    _catalog_argument(create)
    create.add_argument("--job", required=True, action="append", dest="jobs", type=Path,
                        help="Частный write job directory; повторяйте флаг в нужном порядке.")
    create.add_argument("--operation-id", required=True, help="ID точного запроса создания каталога.")
    show = commands.add_parser("show", help="Прочитать историческую квитанцию без обращения к write jobs.")
    _catalog_argument(show)
    page = commands.add_parser(
        "page", help="Наблюдать 1–16 выбранных write jobs без продвижения их фаз.",
        description=(
            "Для продолжения передайте оба поля next_cursor: --catalog-id и --after-index. "
            "Без обоих флагов чтение начинается с первой записи. Размер страницы можно менять; "
            "after-index, равный write_count, возвращает пустую страницу."
        ),
    )
    _catalog_argument(page)
    page.add_argument("--limit", type=int, default=16, help="Число записей, 1–16; по умолчанию 16.")
    page.add_argument("--catalog-id", help="ID каталога из next_cursor.catalog_id.")
    page.add_argument("--after-index", type=int, help="Последний прочитанный индекс из next_cursor.after_index.")


def run_restore_case_retention_review_write_catalog_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_catalog_command
    if action not in {"create", "show", "page"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_catalog_command", "Unknown review write catalog command")
    try:
        from . import workspace_restore_case_retention_review_write_catalog

        if action == "create":
            result = workspace_restore_case_retention_review_write_catalog.create_write_catalog(
                args.catalog, args.jobs, operation_id=args.operation_id,
            )
        elif action == "show":
            result = workspace_restore_case_retention_review_write_catalog.inspect_write_catalog(args.catalog)
        else:
            if (args.catalog_id is None) is not (args.after_index is None):
                raise WorkbenchError(
                    "workspace_restore_case_retention_review_write_catalog_invalid_input",
                    "Both review write catalog cursor fields are required",
                )
            cursor = None if args.catalog_id is None else {"catalog_id": args.catalog_id, "after_index": args.after_index}
            result = workspace_restore_case_retention_review_write_catalog.read_write_catalog_page(
                args.catalog, limit=args.limit, cursor=cursor,
            )
        return _public_response(result, action)
    except WorkbenchError as error:
        code = (error.code if isinstance(error.code, str) and error.code in _ERROR_CODES
                else "workspace_restore_case_retention_review_write_catalog_command_failed")
        raise WorkbenchError(code, "Review write catalog command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_catalog_command_failed", "Review write catalog command failed") from error
