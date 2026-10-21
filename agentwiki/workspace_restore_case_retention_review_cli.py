"""Advisory review receipts and separate current-basis observations.

Historical review responses retain an exact saved retention snapshot. A dated
revalidation separately checks current bindings, observations and classification;
none of these responses authenticates an actor or authorizes archival/deletion.
Public validators perform no filesystem or database reads.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError, canonical
from .workspace_restore_case_retention_cli import _public_entry as _retention_entry, _public_plan as _retention_plan
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-1"
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
}
_REVIEW_FIELDS = {"rules_version", "review_id", "operation_id", "created_at", "actor", "verdict", "plan", *_FLAGS}
_REVALIDATION_FIELDS = {
    "rules_version", "review", "checked_at", "plan_binding", "catalog_binding", "entries",
    "observed_basis_unchanged", *_FLAGS,
}
_ENTRY_FIELDS = {"current", "observations_equal", "classification_equal", "observations_verifiable"}
_COMPARISON_FIELDS = _ENTRY_FIELDS - {"current"}
_BINDING_FIELDS = {"state", "error_code"}
_ERROR_CODES = {
    "workspace_restore_case_retention_review_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete",
        "record_invalid", "unsupported_state", "source_changed", "io_failed", "summary_invalid",
        "plan_changed", "plan_unavailable", "catalog_changed", "catalog_unavailable",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_summary_invalid",
            "Retention review API returned an unsupported public response",
        )


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is flag for key, flag in _FLAGS.items())


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def _public_plan(value: Any) -> dict[str, Any]:
    try:
        return _retention_plan(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_summary_invalid",
            "Review has an unsupported historical retention plan",
        ) from error


def _public_review(value: Any) -> dict[str, Any]:
    """Validate an immutable self-declared review of the complete saved plan."""
    value = _object(value, _REVIEW_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["review_id"], "wrrv")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and _identifier(value["actor"]) and isinstance(value["verdict"], str)
             and value["verdict"] in _VERDICTS and _flags(value))
    return {"rules_version": _RULES_VERSION,
            **{key: value[key] for key in ("review_id", "operation_id", "created_at", "actor", "verdict")},
            "plan": _public_plan(value["plan"]), **_FLAGS}


def _verifiable(observation: dict[str, Any]) -> bool:
    if observation["state"] != "available":
        return False
    detail = observation["detail"]
    return (detail["original_evidence"]["state"] == "unchanged"
            and detail["current_attempt"]["state"] in {"available", "not_linked"})


def _expected_comparison(previous_entry: dict[str, Any], current_entry: dict[str, Any]) -> dict[str, bool]:
    """Compare already validated rows without treating unavailable equality as proof."""
    return {
        "observations_equal": _same(previous_entry["observation"], current_entry["observation"]),
        "classification_equal": (previous_entry["disposition"] == current_entry["disposition"]
                                 and _same(previous_entry["reasons"], current_entry["reasons"])),
        "observations_verifiable": (_verifiable(previous_entry["observation"])
                                    and _verifiable(current_entry["observation"])),
    }


def _public_binding(value: Any, kind: str) -> dict[str, Any]:
    value = _object(value, _BINDING_FIELDS)
    _require(isinstance(value["state"], str) and value["state"] in {"unchanged", "changed", "unavailable"})
    expected = None if value["state"] == "unchanged" else "workspace_restore_case_retention_review_" + kind + "_" + value["state"]
    _require(value["error_code"] == expected)
    return {"state": value["state"], "error_code": value["error_code"]}


def _public_current(value: Any, previous: dict[str, Any], checked_at: int | float,
                    retain_seconds: int) -> dict[str, Any]:
    try:
        current = _retention_entry(value, previous["index"], checked_at, retain_seconds)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_summary_invalid",
            "Revalidation has an unsupported current retention assessment",
        ) from error
    _require(_same(current["case"], previous["case"]))
    return current


def _public_revalidation(value: Any) -> dict[str, Any]:
    """Validate dated current rows and independently derive every comparison."""
    value = _object(value, _REVALIDATION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _timestamp(value["checked_at"]) and _flags(value))
    review = _public_review(value["review"])
    plan = review["plan"]
    plan_binding = _public_binding(value["plan_binding"], "plan")
    catalog_binding = _public_binding(value["catalog_binding"], "catalog")
    _require(isinstance(value["entries"], list) and len(value["entries"]) == len(plan["entries"]))
    entries = []
    for entry, previous in zip(value["entries"], plan["entries"]):
        entry = _object(entry, _ENTRY_FIELDS)
        current = _public_current(entry["current"], previous, value["checked_at"], plan["policy"]["retain_seconds"])
        expected = _expected_comparison(previous, current)
        _require(all(type(entry[key]) is bool and entry[key] is expected[key] for key in _COMPARISON_FIELDS))
        entries.append({"current": current, **expected})
    unchanged = (plan_binding["state"] == catalog_binding["state"] == "unchanged"
                 and all(all(entry[key] for key in _COMPARISON_FIELDS) for entry in entries))
    _require(type(value["observed_basis_unchanged"]) is bool and value["observed_basis_unchanged"] is unchanged)
    return {"rules_version": _RULES_VERSION, "review": review, "checked_at": value["checked_at"],
            "plan_binding": plan_binding, "catalog_binding": catalog_binding, "entries": entries,
            "observed_basis_unchanged": unchanged, **_FLAGS}


def _public_response(value: Any, action: str) -> dict[str, Any]:
    if action in {"record", "show"}:
        return _public_review(value)
    _require(action == "revalidate")
    return _public_revalidation(value)


def _review_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--review", required=True, type=Path,
                        help="Отдельный частный каталог неизменяемой записи рассмотрения.")


def register_restore_case_retention_review_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review", help="Сохранить рассмотрение плана и отдельно проверить текущее основание.",
        description=(
            "Record сохраняет весь исторический retention plan и самозаявленные actor/verdict. "
            "Show читает прежнюю запись без текущих источников. Revalidate отдельно наблюдает нынешние "
            "привязки и случаи, пересчитывает причины по новому clock и показывает сравнение с историей. "
            "Автор не аутентифицируется; архивирование, удаление и применение старых полномочий не разрешаются."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_command", required=True)
    record = commands.add_parser("record", help="Сохранить одно совещательное рассмотрение всего прежнего плана.")
    record.add_argument("--plan", required=True, type=Path, help="Существующий частный retention plan directory.")
    _review_argument(record)
    record.add_argument("--actor", required=True, help="Самозаявленный ID участника; аутентификацией не является.")
    record.add_argument("--verdict", required=True, help="reviewed, defer либо request_fresh_plan; разрешений не выдаёт.")
    record.add_argument("--operation-id", required=True, help="ID точного запроса записи рассмотрения.")
    show = commands.add_parser("show", help="Прочитать историческое рассмотрение без исходного плана и текущих входов.")
    _review_argument(show)
    revalidate = commands.add_parser("revalidate", help="Отдельно наблюдать текущие привязки и сравнить сохранённое основание.")
    _review_argument(revalidate)


def run_restore_case_retention_review_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_command
    if action not in {"record", "show", "revalidate"}:
        raise WorkbenchError("unknown_restore_case_retention_review_command", "Unknown retention review command")
    try:
        from . import workspace_restore_case_retention_review

        if action == "record":
            result = workspace_restore_case_retention_review.record_review(
                args.plan, args.review, operation_id=args.operation_id, actor=args.actor, verdict=args.verdict,
            )
        elif action == "show":
            result = workspace_restore_case_retention_review.inspect_review(args.review)
        else:
            result = workspace_restore_case_retention_review.revalidate_review(args.review)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else "workspace_restore_case_retention_review_command_failed"
        raise WorkbenchError(code, "Retention review command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_command_failed", "Retention review command failed") from error
