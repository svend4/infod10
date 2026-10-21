"""Immutable advisory retention snapshots over a bounded catalog range.

The response preserves historical case receipts and captured detail. Candidate
classification supports later review; every response keeps archival and deletion
unauthorized. Public validation never opens a catalog, case, job, or database.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError, canonical
from .workspace_restore_case_catalog_cli import _public_catalog as _catalog_receipt
from .workspace_restore_cases_cli import _public_case as _case_receipt, _public_inspection as _case_detail
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-1"
_MAX_RETAIN_SECONDS = 315360000
_FLAGS = {
    "advisory_only": True,
    "inspection_only": True,
    "domain_writes_allowed": False,
    "original_files_modified": False,
    "archival_authorized": False,
    "deletion_authorized": False,
    "old_authority_activated": False,
    "external_fencing_established": False,
}
_PLAN_FIELDS = {
    "rules_version", "plan_id", "operation_id", "created_at", "catalog", "policy",
    "after_index", "limit", "entries", *_FLAGS,
}
_ENTRY_FIELDS = {"index", "case", "observation", "disposition", "reasons"}
_OBSERVATION_FIELDS = {"state", "detail", "error_code"}
_OBSERVATION_ERRORS = {
    "available": None,
    "changed": "workspace_restore_case_retention_case_changed",
    "unavailable": "workspace_restore_case_retention_case_unavailable",
}
_ERROR_CODES = {
    "workspace_restore_case_retention_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict",
        "incomplete", "record_invalid", "unsupported_state", "source_changed", "case_changed",
        "case_unavailable", "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_retention_summary_invalid",
            "Retention API returned an unsupported public snapshot",
        )


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _integer(value: Any, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is flag for key, flag in _FLAGS.items())


def _public_catalog(value: Any) -> dict[str, Any]:
    try:
        return _catalog_receipt(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_summary_invalid",
            "Retention snapshot has an unsupported historical catalog receipt",
        ) from error


def _public_case(value: Any) -> dict[str, Any]:
    try:
        return _case_receipt(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_summary_invalid",
            "Retention snapshot has an unsupported historical case receipt",
        ) from error


def _public_detail(value: Any) -> dict[str, Any]:
    try:
        return _case_detail(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_summary_invalid",
            "Retention snapshot has unsupported captured case detail",
        ) from error


def _expected_reasons(case: dict[str, Any], observation: dict[str, Any], created_at: int | float,
                      retain_seconds: int) -> list[str]:
    """Classify already validated public inputs without mutation or I/O."""
    reasons: list[str] = []
    if case["created_at"] > created_at - retain_seconds:
        reasons.append("retention_period_not_elapsed")
    if case["original_identity"] is None:
        reasons.append("original_identity_unknown")
    if observation["state"] != "available":
        reasons.append("case_" + observation["state"])
        return reasons
    detail = observation["detail"]
    evidence = detail["original_evidence"]["state"]
    if evidence != "unchanged":
        reasons.append("original_evidence_" + evidence)
    attempt = detail["current_attempt"]
    if attempt["state"] != "available":
        reasons.append("attempt_" + attempt["state"])
    elif attempt["status"]["completed"] is not True:
        reasons.append("attempt_not_completed")
    return reasons


def _public_observation(value: Any, case: dict[str, Any]) -> dict[str, Any]:
    value = _object(value, _OBSERVATION_FIELDS)
    _require(isinstance(value["state"], str) and value["state"] in _OBSERVATION_ERRORS
             and value["error_code"] == _OBSERVATION_ERRORS[value["state"]])
    detail = None
    if value["state"] == "available":
        detail = _public_detail(value["detail"])
        _require(canonical(detail["case"]) == canonical(case))
    else:
        _require(value["detail"] is None)
    return {"state": value["state"], "detail": detail, "error_code": value["error_code"]}


def _public_entry(value: Any, expected_index: int, created_at: int | float,
                  retain_seconds: int) -> dict[str, Any]:
    value = _object(value, _ENTRY_FIELDS)
    _require(type(value["index"]) is int and value["index"] == expected_index)
    case = _public_case(value["case"])
    observation = _public_observation(value["observation"], case)
    reasons = _expected_reasons(case, observation, created_at, retain_seconds)
    disposition = "held" if reasons else "review_candidate"
    _require(isinstance(value["reasons"], list) and value["reasons"] == reasons
             and isinstance(value["disposition"], str) and value["disposition"] == disposition)
    return {"index": value["index"], "case": case, "observation": observation,
            "disposition": disposition, "reasons": reasons}


def _public_plan(value: Any) -> dict[str, Any]:
    """Validate the historical range, captured detail and advisory classification."""
    value = _object(value, _PLAN_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["plan_id"], "wrrp")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"]) and _flags(value))
    catalog = _public_catalog(value["catalog"])
    policy = _object(value["policy"], {"retain_seconds"})
    retain_seconds = policy["retain_seconds"]
    after, limit = value["after_index"], value["limit"]
    _require(_integer(retain_seconds, 0, _MAX_RETAIN_SECONDS)
             and _integer(after, 0, catalog["case_count"] - 1) and _integer(limit, 1, 16)
             and isinstance(value["entries"], list)
             and len(value["entries"]) == min(limit, catalog["case_count"] - after))
    entries = [_public_entry(entry, after + offset + 1, value["created_at"], retain_seconds)
               for offset, entry in enumerate(value["entries"])]
    _require(len({entry["case"]["case_id"] for entry in entries}) == len(entries))
    return {"rules_version": _RULES_VERSION,
            **{key: value[key] for key in ("plan_id", "operation_id", "created_at")},
            "catalog": catalog, "policy": {"retain_seconds": retain_seconds}, "after_index": after,
            "limit": limit, "entries": entries, **_FLAGS}


def _public_response(value: Any, action: str) -> dict[str, Any]:
    _require(action in {"plan", "show"})
    return _public_plan(value)


def _plan_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--plan", required=True, type=Path,
                        help="Отдельный частный каталог неизменяемого плана удержания.")


def register_restore_case_retention_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention", help="Сохранить совещательный план удержания выбранных случаев.",
        description=(
            "Plan сохраняет исторический снимок ограниченного диапазона каталога, срока удержания и "
            "доступной диагностики случаев. Review_candidate требует отдельного рассмотрения. "
            "План не разрешает архивирование или удаление, не меняет исходные файлы и не выдаёт полномочия. "
            "Show читает сохранённый снимок; текущие источники повторно не проверяются."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_command", required=True)
    plan = commands.add_parser("plan", help="Сохранить совещательный снимок 1–16 случаев из явного каталога.")
    plan.add_argument("--catalog", required=True, type=Path, help="Существующий частный каталог случаев.")
    _plan_argument(plan)
    plan.add_argument("--retain-seconds", required=True, type=int, help="Срок удержания, целое число 0–315360000 секунд.")
    plan.add_argument("--operation-id", required=True, help="ID точного запроса создания плана.")
    plan.add_argument("--after-index", type=int, default=0, help="Начальная позиция диапазона; по умолчанию 0.")
    plan.add_argument("--limit", type=int, default=16, help="Число случаев, 1–16; по умолчанию 16.")
    show = commands.add_parser("show", help="Прочитать неизменяемый исторический план без текущих источников.")
    _plan_argument(show)


def run_restore_case_retention_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_command
    if action not in {"plan", "show"}:
        raise WorkbenchError("unknown_restore_case_retention_command", "Unknown restore case retention command")
    try:
        from . import workspace_restore_case_retention

        if action == "plan":
            result = workspace_restore_case_retention.create_retention_plan(
                args.catalog, args.plan, operation_id=args.operation_id, retain_seconds=args.retain_seconds,
                after_index=args.after_index, limit=args.limit,
            )
        else:
            result = workspace_restore_case_retention.inspect_retention_plan(args.plan)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else "workspace_restore_case_retention_command_failed"
        raise WorkbenchError(code, "Retention command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_retention_command_failed", "Retention command failed") from error
