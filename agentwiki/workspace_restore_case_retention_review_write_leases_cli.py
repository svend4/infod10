"""Strict public receipts for local coordination of explicit write-catalog jobs.

Operation receipts are immutable historical facts. A replay does not renew a
lease; dated pages separately observe the current slot in this single ledger.
Public validation performs no filesystem or database reads.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError
from .workspace_restore_case_retention_review_write_catalog_cli import _public_catalog, _public_write
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-leases-1"
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
}
_LEDGER_FIELDS = {
    "rules_version", "ledger_id", "operation_id", "created_at", "catalog", "write_count", "max_operations", *_FLAGS,
}
_LEASE_FIELDS = {"lease_id", "generation", "worker", "lease_seconds", "claimed_at", "expires_at", "released_at"}
_OPERATION_FIELDS = {
    "rules_version", "ledger_id", "operation_id", "kind", "decided_at", "index", "write", *_LEASE_FIELDS, *_FLAGS,
}
_PAGE_FIELDS = {
    "rules_version", "ledger", "checked_at", "after_index", "limit", "entries", "next_cursor", "operation_count",
    "job_states_checked", *_FLAGS,
}
_ENTRY_FIELDS = {"index", "write", "current_lease", "state"}
_CURSOR_FIELDS = {"ledger_id", "after_index"}
_ERROR_CODES = {
    "workspace_restore_case_retention_review_write_leases_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete", "record_invalid",
        "source_changed", "busy", "clock_regressed", "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_leases_summary_invalid",
            "Review write lease API returned an unsupported public response",
        )


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items())


def _catalog(value: Any) -> dict[str, Any]:
    try:
        return _public_catalog(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_leases_summary_invalid",
            "Review write lease ledger has an unsupported historical catalog",
        ) from error


def _write(value: Any) -> dict[str, Any]:
    try:
        return _public_write(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_leases_summary_invalid",
            "Review write lease ledger has an unsupported historical write descriptor",
        ) from error


def _public_ledger(value: Any) -> dict[str, Any]:
    """Validate a fixed ledger identity and its historical catalog receipt."""
    value = _object(value, _LEDGER_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["ledger_id"], "wrrwlgr")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and _integer(value["write_count"], 1, 256)
             and type(value["max_operations"]) is int and value["max_operations"] == 1024 and _flags(value))
    catalog = _catalog(value["catalog"])
    _require(value["write_count"] == catalog["write_count"])
    return {"rules_version": _RULES_VERSION,
            **{key: value[key] for key in ("ledger_id", "operation_id", "created_at")}, "catalog": catalog,
            "write_count": value["write_count"], "max_operations": 1024, **_FLAGS}


def _public_lease(value: Any) -> dict[str, Any]:
    value = _object(value, _LEASE_FIELDS)
    _require(_address(value["lease_id"], "wrrwl") and _integer(value["generation"], 1, 1024)
             and _identifier(value["worker"]) and _integer(value["lease_seconds"], 1, 3600)
             and _timestamp(value["claimed_at"]) and _timestamp(value["expires_at"])
             and value["expires_at"] == value["claimed_at"] + value["lease_seconds"]
             and value["expires_at"] > value["claimed_at"])
    _require(value["released_at"] is None
             or (_timestamp(value["released_at"]) and value["released_at"] >= value["claimed_at"]))
    return {key: value[key] for key in sorted(_LEASE_FIELDS)}


def _public_operation(value: Any) -> dict[str, Any]:
    """Validate a historical claim or release, without asserting current use."""
    value = _object(value, _OPERATION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["ledger_id"], "wrrwlgr")
             and _identifier(value["operation_id"]) and isinstance(value["kind"], str)
             and value["kind"] in {"claim", "release"}
             and _timestamp(value["decided_at"]) and _integer(value["index"], 1, 256) and _flags(value))
    lease = _public_lease({key: value[key] for key in _LEASE_FIELDS})
    if value["kind"] == "claim":
        _require(value["released_at"] is None and value["decided_at"] == value["claimed_at"])
    else:
        _require(value["released_at"] is not None and value["decided_at"] == value["released_at"])
    return {"rules_version": _RULES_VERSION,
            **{key: value[key] for key in ("ledger_id", "operation_id", "kind", "decided_at", "index")},
            "write": _write(value["write"]), **lease, **_FLAGS}


def _public_entry(value: Any, expected_index: int, checked_at: int | float, operation_count: int) -> dict[str, Any]:
    value = _object(value, _ENTRY_FIELDS)
    _require(type(value["index"]) is int and value["index"] == expected_index
             and isinstance(value["state"], str) and value["state"] in {"unclaimed", "active", "expired", "released"})
    write = _write(value["write"])
    lease = None
    if value["state"] == "unclaimed":
        _require(value["current_lease"] is None)
    else:
        lease = _public_lease(value["current_lease"])
        _require(lease["generation"] <= operation_count)
        expected = ("released" if lease["released_at"] is not None else
                    "active" if checked_at < lease["expires_at"] else "expired")
        _require(value["state"] == expected)
    return {"index": value["index"], "write": write, "current_lease": lease, "state": value["state"]}


def _public_page(value: Any) -> dict[str, Any]:
    """Validate bounded single-ledger observations and cursor arithmetic."""
    value = _object(value, _PAGE_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _timestamp(value["checked_at"])
             and _integer(value["operation_count"], 0, 1024)
             and type(value["job_states_checked"]) is bool and value["job_states_checked"] is False and _flags(value))
    ledger = _public_ledger(value["ledger"])
    count, after, limit = ledger["write_count"], value["after_index"], value["limit"]
    _require(_integer(after, 0, count) and _integer(limit, 1, 16)
             and isinstance(value["entries"], list) and len(value["entries"]) == min(limit, count - after))
    entries = [_public_entry(item, after + offset + 1, value["checked_at"], value["operation_count"])
               for offset, item in enumerate(value["entries"])]
    leases = [entry["current_lease"] for entry in entries if entry["current_lease"] is not None]
    _require(len({entry["write"]["write_id"] for entry in entries}) == len(entries)
             and len({entry["write"]["review_id"] for entry in entries}) == len(entries)
             and len({lease["lease_id"] for lease in leases}) == len(leases))
    end = after + len(entries)
    cursor = None
    if end < count:
        cursor = _object(value["next_cursor"], _CURSOR_FIELDS)
        _require(cursor["ledger_id"] == ledger["ledger_id"]
                 and type(cursor["after_index"]) is int and cursor["after_index"] == end)
        cursor = {"ledger_id": cursor["ledger_id"], "after_index": cursor["after_index"]}
    else:
        _require(value["next_cursor"] is None)
    return {"rules_version": _RULES_VERSION, "ledger": ledger, "checked_at": value["checked_at"],
            "after_index": after, "limit": limit, "entries": entries, "next_cursor": cursor,
            "operation_count": value["operation_count"], "job_states_checked": False, **_FLAGS}


def _public_response(value: Any, action: str) -> dict[str, Any]:
    if action in {"create", "show"}:
        return _public_ledger(value)
    if action == "page":
        return _public_page(value)
    _require(action in {"claim", "release"})
    result = _public_operation(value)
    _require(result["kind"] == action)
    return result


def _ledger_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--ledger", required=True, type=Path,
                        help="Отдельный частный ledger координации выбранного write catalog.")


def register_restore_case_retention_review_write_leases_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write-leases",
        help="Координировать локальные аренды явно выбранных write jobs в одном ledger.",
        description=(
            "Create закрепляет прежний явный write catalog. Claim арендует выбранный индекс в этом ledger; "
            "release освобождает точно указанную последнюю аренду. Show читает историческую квитанцию, page "
            "наблюдает текущие слоты. Точный повтор operation ID возвращает прежний ответ без продления. "
            "Состояния jobs не читаются, step не вызывается и не защищается арендой. Worker самозаявленный; "
            "другие ledgers и прямой вызов прежнего write API остаются вне этой координации."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_leases_command", required=True)
    create = commands.add_parser("create", help="Закрепить явный каталог в новом частном ledger.")
    _ledger_argument(create)
    create.add_argument("--catalog", required=True, type=Path, help="Прежний неизменяемый write catalog.")
    create.add_argument("--operation-id", required=True, help="ID точного запроса создания ledger.")
    show = commands.add_parser("show", help="Прочитать квитанцию ledger без прежних исходных путей.")
    _ledger_argument(show)
    page = commands.add_parser("page", help="Наблюдать 1–16 локальных слотов без чтения или изменения jobs.")
    _ledger_argument(page)
    page.add_argument("--limit", type=int, default=16, help="Число записей, 1–16; по умолчанию 16.")
    page.add_argument("--ledger-id", help="ID ledger из next_cursor.ledger_id.")
    page.add_argument("--after-index", type=int, help="Последний прочитанный индекс из next_cursor.after_index.")
    claim = commands.add_parser("claim", help="Арендовать явно выбранный индекс в этом ledger.")
    release = commands.add_parser("release", help="Освободить точно указанную последнюю аренду, в том числе истёкшую.")
    for command in (claim, release):
        _ledger_argument(command)
        command.add_argument("--index", required=True, type=int, help="Позиция участника исходного каталога, от 1.")
        command.add_argument("--worker", required=True, help="Самозаявленный ID обработчика; не удостоверяет личность.")
        command.add_argument("--operation-id", required=True, help="ID точного запроса; повтор возвращает исторический ответ.")
    claim.add_argument("--lease-seconds", required=True, type=int, help="Срок локальной аренды, 1–3600 секунд.")
    release.add_argument("--lease-id", required=True, help="Точный lease_id последней аренды.")
    release.add_argument("--generation", required=True, type=int, help="Точное поколение последней аренды.")


def run_restore_case_retention_review_write_leases_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_leases_command
    if action not in {"create", "show", "page", "claim", "release"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_leases_command", "Unknown review write leases command")
    try:
        from . import workspace_restore_case_retention_review_write_leases

        if action == "create":
            result = workspace_restore_case_retention_review_write_leases.create_write_lease_ledger(
                args.ledger, args.catalog, operation_id=args.operation_id,
            )
        elif action == "show":
            result = workspace_restore_case_retention_review_write_leases.inspect_write_lease_ledger(args.ledger)
        elif action == "page":
            if (args.ledger_id is None) is not (args.after_index is None):
                raise WorkbenchError(
                    "workspace_restore_case_retention_review_write_leases_invalid_input",
                    "Both review write lease ledger cursor fields are required",
                )
            cursor = None if args.ledger_id is None else {"ledger_id": args.ledger_id, "after_index": args.after_index}
            result = workspace_restore_case_retention_review_write_leases.read_write_lease_page(
                args.ledger, limit=args.limit, cursor=cursor,
            )
        elif action == "claim":
            result = workspace_restore_case_retention_review_write_leases.claim_write_lease(
                args.ledger, index=args.index, worker=args.worker, lease_seconds=args.lease_seconds,
                operation_id=args.operation_id,
            )
        else:
            result = workspace_restore_case_retention_review_write_leases.release_write_lease(
                args.ledger, index=args.index, worker=args.worker, lease_id=args.lease_id, generation=args.generation,
                operation_id=args.operation_id,
            )
        return _public_response(result, action)
    except WorkbenchError as error:
        code = (error.code if isinstance(error.code, str) and error.code in _ERROR_CODES
                else "workspace_restore_case_retention_review_write_leases_command_failed")
        raise WorkbenchError(code, "Review write lease command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_leases_command_failed", "Review write lease command failed") from error
