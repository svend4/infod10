"""Closed public receipts and commands for the guarded review-write family.

Public validation uses only supplied values. Bearer credentials are accepted
only through strict private sibling files and never appear in public receipts.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from . import store_backup
from .store import WorkbenchError, canonical
from .workspace_restore_case_retention_review_write_catalog_cli import _public_write as _write
from .workspace_restore_case_retention_review_write_cli import _public_status as _write_status
from .workspace_restore_case_retention_review_write_permit_cli import (
    _prepare_private_credential as _prepare_permit_credential,
    _read_private_credential as _read_permit_credential,
)
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-guarded-1"
_PREFIX = "workspace_restore_case_retention_review_write_guarded_"
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
    "local_capability_only": True,
    "external_identity_verified": False,
    "legacy_write_apis_rejected": True,
    "lease_enforced": False,
    "issuer_policy_enforced": False,
    "os_isolation_established": False,
    "credential_required_for_step": True,
}
_GUARD_FIELDS = {"rules_version", "guard_id", "operation_id", "created_at", "principal", "write", *_FLAGS}
_ACCESS_FIELDS = {"rules_version", "guard", "access_state", "revocation", *_FLAGS}
_STATUS_FIELDS = {*_ACCESS_FIELDS, "write"}
_EXECUTION_FIELDS = {
    "rules_version", "guard", "observation", "credential_verified", "revocation_checked",
    "historical_effect_authenticated", *_FLAGS,
}
_OBSERVATION_FIELDS = {
    "rules_version", "guard_id", "operation_id", "expected_state", "target_state",
    "recorded_at", "outcome", "observed_state", "effect_performed",
}
_REVOCATION_FIELDS = {"rules_version", "guard_id", "operation_id", "revoked_at", "reason"}
_TARGETS = {"prepared": "ready", "ready": "linked", "linked": "completed"}
_ERROR_CODES = {
    _PREFIX + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete",
        "record_invalid", "source_changed", "credential_invalid", "revoked", "clock_regressed",
        "phase_changed", "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Guarded review write API returned an unsupported public response")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _shape(value: Any) -> None:
    """Bound supplied JSON shape without touching any runtime source."""
    remaining = 200000
    pending = [(value, 0)]
    while pending:
        child, depth = pending.pop()
        remaining -= 1
        _require(remaining >= 0 and depth <= 64)
        if isinstance(child, dict):
            _require(all(isinstance(key, str) for key in child))
            try:
                for key in child:
                    key.encode("utf-8")
            except UnicodeError as error:
                raise WorkbenchError(_PREFIX + "summary_invalid", "Guarded public keys must be UTF-8 strings") from error
            pending.extend((nested, depth + 1) for nested in child.values())
        elif isinstance(child, list):
            pending.extend((nested, depth + 1) for nested in child)
        elif type(child) is int:
            _require(child.bit_length() <= 256)
        elif type(child) is float:
            _require(math.isfinite(child))
        elif type(child) is str:
            try:
                child.encode("utf-8")
            except UnicodeError as error:
                raise WorkbenchError(_PREFIX + "summary_invalid", "Guarded public values must be UTF-8 strings") from error
        else:
            _require(child is None or type(child) is bool)


def _bounded(value: dict[str, Any], limit: int = 64 * 1024) -> dict[str, Any]:
    _require(len(canonical(value).encode("utf-8")) <= limit)
    return value


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is flag for key, flag in _FLAGS.items())


def _public_guard(value: Any) -> dict[str, Any]:
    """Validate one immutable local guard and its compact historical write."""
    _shape(value)
    value = _object(value, _GUARD_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["guard_id"], "wrrwg")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and _identifier(value["principal"]) and _flags(value))
    try:
        write = _write(value["write"])
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Guard has an unsupported historical write descriptor") from error
    _require(value["operation_id"] == write["operation_id"]
             and canonical(value["created_at"]) == canonical(write["created_at"]))
    return _bounded({"rules_version": _RULES_VERSION,
                     **{key: value[key] for key in ("guard_id", "operation_id", "created_at", "principal")},
                     "write": write, **_FLAGS})


def _public_access(value: Any) -> dict[str, Any]:
    """Validate own metadata independently of a live review or job phase."""
    _shape(value)
    value = _object(value, _ACCESS_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and isinstance(value["access_state"], str)
             and value["access_state"] in {"active", "revoked"} and _flags(value))
    guard = _public_guard(value["guard"])
    revocation = None
    if value["access_state"] == "revoked":
        revocation = _object(value["revocation"], _REVOCATION_FIELDS)
        _require(revocation["rules_version"] == _RULES_VERSION
                 and revocation["guard_id"] == guard["guard_id"]
                 and _identifier(revocation["operation_id"]) and _timestamp(revocation["revoked_at"])
                 and revocation["revoked_at"] >= guard["created_at"]
                 and revocation["reason"] == "holder_revoked")
        revocation = {key: revocation[key] for key in sorted(_REVOCATION_FIELDS)}
    else:
        _require(value["revocation"] is None)
    return _bounded({"rules_version": _RULES_VERSION, "guard": guard,
                     "access_state": value["access_state"], "revocation": revocation, **_FLAGS})


def _public_status(value: Any) -> dict[str, Any]:
    """Validate a live observation while preserving historical guard binding."""
    _shape(value)
    value = _object(value, _STATUS_FIELDS)
    access = _public_access({key: value[key] for key in _ACCESS_FIELDS})
    try:
        write = _write_status(value["write"])
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Guarded status has an unsupported historical write") from error
    observed = {
        **{key: write[key] for key in ("rules_version", "write_id", "operation_id", "created_at")},
        **{key: write["intended_review"][key] for key in ("review_id", "actor", "verdict")},
    }
    _require(canonical(observed) == canonical(access["guard"]["write"]))
    return _bounded({**access, "write": write}, 16 * 1024 * 1024)


def _public_observation(value: Any) -> dict[str, Any]:
    """Validate the historical target of exactly one named guarded phase."""
    _shape(value)
    value = _object(value, _OBSERVATION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["guard_id"], "wrrwg")
             and _identifier(value["operation_id"]) and _timestamp(value["recorded_at"])
             and isinstance(value["expected_state"], str) and value["expected_state"] in _TARGETS
             and value["target_state"] == _TARGETS[value["expected_state"]]
             and value["observed_state"] == value["target_state"]
             and isinstance(value["outcome"], str)
             and value["outcome"] in {"phase_performed", "expected_phase_observed"}
             and type(value["effect_performed"]) is bool
             and value["effect_performed"] is (value["outcome"] == "phase_performed"))
    return _bounded({key: value[key] for key in sorted(_OBSERVATION_FIELDS)})


def _public_execution(value: Any) -> dict[str, Any]:
    """Validate a protected step receipt without claiming past authorship."""
    _shape(value)
    value = _object(value, _EXECUTION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _flags(value)
             and type(value["credential_verified"]) is bool and value["credential_verified"] is True
             and type(value["revocation_checked"]) is bool and value["revocation_checked"] is True
             and type(value["historical_effect_authenticated"]) is bool
             and value["historical_effect_authenticated"] is False)
    guard, observation = _public_guard(value["guard"]), _public_observation(value["observation"])
    _require(observation["guard_id"] == guard["guard_id"]
             and observation["recorded_at"] >= guard["created_at"])
    return _bounded({"rules_version": _RULES_VERSION, "guard": guard, "observation": observation,
                     "credential_verified": True, "revocation_checked": True,
                     "historical_effect_authenticated": False, **_FLAGS})


_public_step = _public_execution


def _public_response(value: Any, action: str) -> dict[str, Any]:
    _require(action in {"prepare", "show", "status", "step", "revoke"})
    if action == "prepare":
        return _public_guard(value)
    if action == "status":
        return _public_status(value)
    if action == "step":
        return _public_execution(value)
    result = _public_access(value)
    if action == "revoke":
        _require(result["access_state"] == "revoked")
    return result


def _credential(path: Path, *, prepare: bool = False) -> str:
    try:
        return _prepare_permit_credential(path) if prepare else _read_permit_credential(path)
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "private_path_required", "Guard credentials require private owner files") from error


def _credential_location(path: Path, directories: list[Path]) -> Path:
    location = store_backup._path(path)
    if (not directories or location.parent != directories[0].parent
            or any(directory.parent != location.parent or location == directory for directory in directories)):
        raise WorkbenchError(_PREFIX + "private_path_required", "Guard credentials need separate private sibling files")
    return location


def _job_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job", required=True, type=Path, help="Отдельный частный каталог guarded записи review.")


def register_restore_case_retention_review_write_guarded_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write-guarded",
        help="Подготовить отдельную запись review с обязательным локальным credential для каждого шага.",
        description=(
            "Prepare создаёт новый формат job с отдельным bearer credential. Step проверяет credential и отзыв "
            "при каждом вызове, включая точный replay. Show читает собственные metadata; status наблюдает live review. "
            "Старые write API не принимают этот формат. Старые lease и issuer policy здесь не применяются; "
            "личность участника, прошлое авторство и изоляция от владельца файлов не устанавливаются."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_guarded_command", required=True)
    prepare = commands.add_parser("prepare", help="Закрепить новое guarded намерение записи исторического review.")
    prepare.add_argument("--plan", required=True, type=Path, help="Существующий частный retention plan directory.")
    prepare.add_argument("--review", required=True, type=Path, help="Новое частное назначение совместимого review.")
    _job_argument(prepare)
    prepare.add_argument("--operation-id", required=True, help="ID точного запроса подготовки.")
    prepare.add_argument("--actor", required=True, help="Самозаявленный участник исторического review.")
    prepare.add_argument("--verdict", required=True, help="reviewed, defer либо request_fresh_plan.")
    prepare.add_argument("--principal", required=True, help="Локальная метка держателя; не удостоверяет личность.")
    credentials = prepare.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--credential-out", type=Path, help="Новый private .credential/0600; не перезаписывается.")
    credentials.add_argument("--prepared-credential", type=Path, help="Сохранённый private credential для точного replay.")
    show = commands.add_parser("show", help="Наблюдать собственный guard и отзыв без live источников.")
    _job_argument(show)
    status = commands.add_parser("status", help="Наблюдать текущую фазу job и review без credential.")
    _job_argument(status)
    step = commands.add_parser("step", help="Credential держателя допускает один точно названный шаг либо его replay.")
    _job_argument(step)
    step.add_argument("--operation-id", required=True, help="Отдельный ID точного вызова этой фазы.")
    step.add_argument("--expected-state", required=True, choices=sorted(_TARGETS), help="Точная исходная фаза.")
    revoke = commands.add_parser("revoke", help="Тем же credential остановить будущие шаги и выдачу сохранённых результатов.")
    _job_argument(revoke)
    revoke.add_argument("--operation-id", required=True, help="ID точного необратимого отзыва.")
    for command in (step, revoke):
        command.add_argument("--credential-file", required=True, type=Path, help="Private .credential/0600 держателя.")


def run_restore_case_retention_review_write_guarded_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_guarded_command
    if action not in {"prepare", "show", "status", "step", "revoke"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_guarded_command", "Unknown guarded review write command")
    try:
        from . import workspace_restore_case_retention_review_write_guarded as guarded

        job = store_backup._path(args.job)
        if action == "prepare":
            directories = [job, store_backup._path(args.plan), store_backup._path(args.review)]
            if len(set(directories)) != 3:
                raise WorkbenchError(_PREFIX + "invalid_input", "Guarded plan, review and job need distinct sibling paths")
            location = _credential_location(args.credential_out or args.prepared_credential, directories)
            credential = _credential(location, prepare=args.credential_out is not None)
            result = guarded.prepare_guarded_review_write(
                args.plan, args.review, args.job, operation_id=args.operation_id,
                actor=args.actor, verdict=args.verdict, principal=args.principal, credential=credential,
            )
        elif action == "show":
            result = guarded.inspect_guarded_review_write_access(args.job)
        elif action == "status":
            result = guarded.inspect_guarded_review_write(args.job)
        else:
            location = _credential_location(args.credential_file, [job])
            credential = _credential(location)
            if action == "step":
                result = guarded.step_guarded_review_write(
                    args.job, operation_id=args.operation_id, expected_state=args.expected_state, credential=credential,
                )
            else:
                result = guarded.revoke_guarded_review_write(args.job, operation_id=args.operation_id, credential=credential)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else _PREFIX + "command_failed"
        raise WorkbenchError(code, "Guarded review write command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError(_PREFIX + "command_failed", "Guarded review write command failed") from error
