"""Bounded metadata pages over explicitly selected historical restore cases.

Membership and order are fixed in a separate private catalog. Pages return the
pinned historical case receipt beside current case-control availability and an
optional historical link. They do not inspect original evidence or live jobs.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError
from .workspace_restore_cases_cli import _public_case as _case_receipt, _public_link as _link_receipt
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-catalog-1"
_FLAGS = {
    "membership_frozen": True,
    "order_frozen": True,
    "inspection_only": True,
    "domain_writes_allowed": False,
    "original_files_modified": False,
    "old_authority_activated": False,
    "external_fencing_established": False,
}
_CATALOG_FIELDS = {"rules_version", "catalog_id", "operation_id", "created_at", "case_count", *_FLAGS}
_PAGE_FLAGS = {"original_evidence_checked": False, "live_jobs_checked": False}
_PAGE_FIELDS = {"rules_version", "catalog", "after_index", "limit", "entries", "next_cursor", *_PAGE_FLAGS}
_ENTRY_FIELDS = {"index", "case", "current_case"}
_CURRENT_FIELDS = {"state", "link", "error_code"}
_CURSOR_FIELDS = {"catalog_id", "after_index"}
_CURRENT_ERRORS = {
    "available": None,
    "changed": "workspace_restore_case_catalog_entry_changed",
    "unavailable": "workspace_restore_case_catalog_entry_unavailable",
}
_ERROR_CODES = {
    "workspace_restore_case_catalog_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict",
        "incomplete", "record_invalid", "unsupported_state", "entry_changed",
        "entry_unavailable", "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_catalog_summary_invalid",
            "Restore case catalog API returned an unsupported public response",
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


def _public_case(value: Any) -> dict[str, Any]:
    try:
        return _case_receipt(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_catalog_summary_invalid",
            "Restore case catalog has an unsupported historical case receipt",
        ) from error


def _public_link(value: Any) -> dict[str, Any]:
    try:
        return _link_receipt(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_catalog_summary_invalid",
            "Restore case catalog has an unsupported historical link receipt",
        ) from error


def _public_catalog(value: Any) -> dict[str, Any]:
    """Validate a fixed historical catalog receipt without private membership."""
    value = _object(value, _CATALOG_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["catalog_id"], "wrcat")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and _integer(value["case_count"], 1, 256) and _flags(value, _FLAGS))
    return {key: value[key] for key in sorted(_CATALOG_FIELDS)}


def _public_entry(value: Any, expected_index: int) -> dict[str, Any]:
    value = _object(value, _ENTRY_FIELDS)
    _require(type(value["index"]) is int and value["index"] == expected_index)
    case = _public_case(value["case"])
    current = _object(value["current_case"], _CURRENT_FIELDS)
    _require(isinstance(current["state"], str) and current["state"] in _CURRENT_ERRORS
             and current["error_code"] == _CURRENT_ERRORS[current["state"]])
    link = None
    if current["state"] == "available":
        if current["link"] is not None:
            link = _public_link(current["link"])
            _require(link["case_id"] == case["case_id"])
    else:
        _require(current["link"] is None)
    return {"index": value["index"], "case": case,
            "current_case": {"state": current["state"], "link": link, "error_code": current["error_code"]}}


def _public_page(value: Any) -> dict[str, Any]:
    """Validate page order, cursor arithmetic and the metadata-only boundary."""
    value = _object(value, _PAGE_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _flags(value, _PAGE_FLAGS))
    catalog = _public_catalog(value["catalog"])
    count, after, limit = catalog["case_count"], value["after_index"], value["limit"]
    _require(_integer(after, 0, count) and _integer(limit, 1, 16)
             and isinstance(value["entries"], list) and len(value["entries"]) == min(limit, count - after))
    entries = [_public_entry(item, after + offset + 1) for offset, item in enumerate(value["entries"])]
    _require(len({entry["case"]["case_id"] for entry in entries}) == len(entries))
    last = after + len(entries)
    cursor = None
    if last < count:
        cursor = _object(value["next_cursor"], _CURSOR_FIELDS)
        _require(cursor["catalog_id"] == catalog["catalog_id"]
                 and type(cursor["after_index"]) is int and cursor["after_index"] == last)
        cursor = {"catalog_id": cursor["catalog_id"], "after_index": cursor["after_index"]}
    else:
        _require(value["next_cursor"] is None)
    return {"rules_version": _RULES_VERSION, "catalog": catalog, "after_index": after, "limit": limit,
            "entries": entries, "next_cursor": cursor, **_PAGE_FLAGS}


def _public_response(value: Any, action: str) -> dict[str, Any]:
    if action == "create":
        return _public_catalog(value)
    _require(action == "page")
    return _public_page(value)


def _catalog_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--catalog", required=True, type=Path,
                        help="Отдельный частный каталог зафиксированного перечня случаев.")


def register_restore_case_catalog_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-catalog", help="Зафиксировать явный перечень случаев и читать страницы метаданных.",
        description=(
            "Create сохраняет явно выбранные случаи в указанном порядке. Page возвращает исторические "
            "квитанции и текущую доступность case control records, включая историческую link receipt. "
            "Исходные свидетельства и текущее состояние jobs этой командой не проверяются. "
            "Частные пути, планы и контрольные суммы не выводятся."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_catalog_command", required=True)
    create = commands.add_parser("create", help="Сохранить неизменяемый перечень из 1–256 выбранных случаев.")
    _catalog_argument(create)
    create.add_argument("--case", required=True, action="append", dest="cases", type=Path,
                        help="Частный case directory; повторяйте флаг в нужном порядке.")
    create.add_argument("--operation-id", required=True, help="ID точного запроса создания каталога.")
    page = commands.add_parser(
        "page", help="Прочитать 1–16 записей с неизменяемым составом и текущими case metadata.",
        description=(
            "Для продолжения передайте оба поля next_cursor: --catalog-id и --after-index. "
            "Если оба флага отсутствуют, чтение начинается с первой записи. Размер страницы можно менять. "
            "After-index, равный case_count, возвращает пустую страницу."
        ),
    )
    _catalog_argument(page)
    page.add_argument("--limit", type=int, default=16, help="Число записей, 1–16; по умолчанию 16.")
    page.add_argument("--catalog-id", help="ID каталога из next_cursor.catalog_id.")
    page.add_argument("--after-index", type=int, help="Последний прочитанный индекс из next_cursor.after_index.")


def run_restore_case_catalog_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_catalog_command
    if action not in {"create", "page"}:
        raise WorkbenchError("unknown_restore_case_catalog_command", "Unknown restore case catalog command")
    try:
        from . import workspace_restore_case_catalog

        if action == "create":
            result = workspace_restore_case_catalog.create_catalog(args.catalog, args.cases, operation_id=args.operation_id)
        else:
            if (args.catalog_id is None) is not (args.after_index is None):
                raise WorkbenchError("workspace_restore_case_catalog_invalid_input", "Both catalog cursor fields are required")
            cursor = None if args.catalog_id is None else {"catalog_id": args.catalog_id, "after_index": args.after_index}
            result = workspace_restore_case_catalog.read_catalog_page(args.catalog, limit=args.limit, cursor=cursor)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else "workspace_restore_case_catalog_command_failed"
        raise WorkbenchError(code, "Restore case catalog command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_catalog_command_failed", "Restore case catalog command failed") from error
