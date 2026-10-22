"""Commands and compact public records for lease-checked single-phase actions.

Preparation returns a fixed historical receipt. Show and execute return a
separate recorded-observation wrapper; replay never implies a current lease.
Validation performs no filesystem, database or current-job reads.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .store import WorkbenchError, canonical
from .workspace_restore_case_retention_review_write_catalog_cli import _public_write
from .workspace_restore_case_retention_review_write_leases_cli import _public_operation
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-action-1"
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
}
_TARGETS = {"prepared": "ready", "staging": "ready", "ready": "linked", "linked": "completed"}
_ACTION_FIELDS = {
    "rules_version", "action_id", "operation_id", "created_at", "write", "lease", "before_state", "target_state", *_FLAGS,
}
_WRAPPER_FIELDS = {"rules_version", "action", "state", "result", *_FLAGS}
_OBSERVATION_FIELDS = {
    "rules_version", "action_id", "recorded_at", "lease_checked_at", "outcome", "observed_state", "effect_performed",
}
_ERROR_CODES = {
    "workspace_restore_case_retention_review_write_action_" + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete", "record_invalid",
        "source_changed", "lease_changed", "lease_expired", "clock_regressed", "phase_changed", "unsupported_state",
        "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_action_summary_invalid",
            "Review write action API returned an unsupported public response",
        )


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items())


def _write(value: Any) -> dict[str, Any]:
    try:
        return _public_write(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_action_summary_invalid",
            "Review write action has an unsupported historical write descriptor",
        ) from error


def _lease(value: Any) -> dict[str, Any]:
    try:
        result = _public_operation(value)
    except WorkbenchError as error:
        raise WorkbenchError(
            "workspace_restore_case_retention_review_write_action_summary_invalid",
            "Review write action has an unsupported historical claim",
        ) from error
    _require(result["kind"] == "claim" and result["released_at"] is None)
    return result


def _public_action(value: Any) -> dict[str, Any]:
    """Validate an immutable prepared action, without asserting current use."""
    value = _object(value, _ACTION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["action_id"], "wrrwa")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and isinstance(value["before_state"], str) and value["before_state"] in _TARGETS
             and value["target_state"] == _TARGETS[value["before_state"]] and _flags(value))
    write, lease = _write(value["write"]), _lease(value["lease"])
    _require(canonical(write) == canonical(lease["write"])
             and lease["claimed_at"] <= value["created_at"] < lease["expires_at"])
    return {
        "rules_version": _RULES_VERSION,
        **{key: value[key] for key in ("action_id", "operation_id", "created_at")},
        "write": write, "lease": lease, "before_state": value["before_state"], "target_state": value["target_state"],
        **_FLAGS,
    }


def _public_observation(value: Any, action: dict[str, Any]) -> dict[str, Any]:
    """Validate one saved observation; an observed target need not be our effect."""
    value = _object(value, _OBSERVATION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and value["action_id"] == action["action_id"]
             and _timestamp(value["recorded_at"]) and _timestamp(value["lease_checked_at"])
             and action["created_at"] <= value["lease_checked_at"] <= value["recorded_at"]
             and action["lease"]["claimed_at"] <= value["lease_checked_at"] < action["lease"]["expires_at"]
             and isinstance(value["outcome"], str) and value["outcome"] in {"phase_performed", "expected_phase_observed"}
             and value["observed_state"] == action["target_state"]
             and type(value["effect_performed"]) is bool
             and value["effect_performed"] is (value["outcome"] == "phase_performed"))
    return {key: value[key] for key in sorted(_OBSERVATION_FIELDS)}


def _public_wrapper(value: Any) -> dict[str, Any]:
    """Keep prepared identity separate from its saved execution observation."""
    value = _object(value, _WRAPPER_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and isinstance(value["state"], str)
             and value["state"] in {"prepared", "recorded"} and _flags(value))
    action = _public_action(value["action"])
    result = None
    if value["state"] == "recorded":
        result = _public_observation(value["result"], action)
    else:
        _require(value["result"] is None)
    return {"rules_version": _RULES_VERSION, "action": action, "state": value["state"], "result": result, **_FLAGS}


def _public_response(value: Any, action: str) -> dict[str, Any]:
    _require(action in {"prepare", "show", "execute"})
    if action == "prepare":
        return _public_action(value)
    result = _public_wrapper(value)
    if action == "execute":
        _require(result["state"] == "recorded")
    return result


def _action_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--action", required=True, type=Path,
                        help="Отдельный частный каталог закреплённого действия над одной фазой write job.")


def register_restore_case_retention_review_write_action_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write-action",
        help="Закрепить одну фазу write job и выполнить её с проверкой captured lease.",
        description=(
            "Prepare закрепляет выбранную позицию ledger, точную аренду и ожидаемую фазу. Execute проверяет "
            "текущую аренду перед новым действием и сохраняет наблюдение ровно целевой фазы. Уже сохранённый "
            "результат возвращается исторически без нового шага. Show читает собственные controls. "
            "Прежний прямой step не требует этой аренды; worker самозаявленный, общего fencing и доменных прав нет."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_action_command", required=True)
    prepare = commands.add_parser("prepare", help="Закрепить отдельное действие ровно над одной ожидаемой фазой.")
    _action_argument(prepare)
    prepare.add_argument("--ledger", required=True, type=Path, help="Прежний частный ledger выбранного write catalog.")
    prepare.add_argument("--index", required=True, type=int, help="Явная позиция write job в captured catalog.")
    prepare.add_argument("--worker", required=True, help="Самозаявленный worker точной текущей аренды.")
    prepare.add_argument("--lease-id", required=True, help="Точный ID текущего claim.")
    prepare.add_argument("--generation", required=True, type=int, help="Точное поколение текущего claim.")
    prepare.add_argument("--operation-id", required=True, help="ID точного запроса подготовки этого действия.")
    prepare.add_argument("--expected-state", required=True, help="Ожидаемая исходная фаза: prepared, staging, ready либо linked.")
    show = commands.add_parser("show", help="Показать закреплённое действие и сохранённое наблюдение без исходных путей.")
    _action_argument(show)
    execute = commands.add_parser("execute", help="Достичь только закреплённой целевой фазы по действующей captured lease.")
    _action_argument(execute)


def run_restore_case_retention_review_write_action_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_action_command
    if action not in {"prepare", "show", "execute"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_action_command", "Unknown review write action command")
    try:
        from . import workspace_restore_case_retention_review_write_action

        if action == "prepare":
            result = workspace_restore_case_retention_review_write_action.prepare_review_write_action(
                args.action, args.ledger, index=args.index, worker=args.worker, lease_id=args.lease_id,
                generation=args.generation, operation_id=args.operation_id, expected_state=args.expected_state,
            )
        elif action == "show":
            result = workspace_restore_case_retention_review_write_action.inspect_review_write_action(args.action)
        else:
            result = workspace_restore_case_retention_review_write_action.execute_review_write_action(args.action)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = (error.code if isinstance(error.code, str) and error.code in _ERROR_CODES
                else "workspace_restore_case_retention_review_write_action_command_failed")
        raise WorkbenchError(code, "Review write action command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_restore_case_retention_review_write_action_command_failed", "Review write action command failed") from error
