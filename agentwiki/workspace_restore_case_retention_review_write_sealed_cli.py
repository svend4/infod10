"""Closed public receipts and commands for the sealed review-write family.

One receipt describes an immutable job plus captured issuance evidence. Pure
validators observe supplied JSON only; credentials enter through private files.
"""

from __future__ import annotations

import argparse
import hmac
import math
from pathlib import Path
import sqlite3
from typing import Any

from . import store_backup
from .store import WorkbenchError, canonical
from .workspace_restore_case_retention_review_write_cli import _public_status as _write_status
from .workspace_restore_case_retention_review_write_guarded_cli import _public_guard
from .workspace_restore_case_retention_review_write_guarded_issuer_cli import _public_issuer, _public_rule
from .workspace_restore_case_retention_review_write_permit_cli import (
    _prepare_private_credential as _prepare_permit_credential,
    _read_private_credential as _read_permit_credential,
)
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-sealed-1"
_PREFIX = "workspace_restore_case_retention_review_write_sealed_"
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
    "native_guarded_apis_rejected": True,
    "lease_enforced": False,
    "live_issuer_policy_enforced": False,
    "os_isolation_established": False,
    "credential_required_for_step": True,
    "sealed_issuance_required_for_step": True,
}
_RECEIPT_FIELDS = {
    "rules_version", "seal_id", "operation_id", "created_at", "issuer", "rule", "guard", "plan_id",
    "policy_authorized", *_FLAGS,
}
_ACCESS_FIELDS = {"rules_version", "seal", "access_state", "revocation", *_FLAGS}
_STATUS_FIELDS = {*_ACCESS_FIELDS, "write"}
_EXECUTION_FIELDS = {
    "rules_version", "seal", "observation", "credential_verified", "revocation_checked",
    "historical_effect_authenticated", *_FLAGS,
}
_OBSERVATION_FIELDS = {
    "rules_version", "seal_id", "operation_id", "expected_state", "target_state", "recorded_at",
    "outcome", "observed_state", "effect_performed",
}
_REVOCATION_FIELDS = {"rules_version", "seal_id", "operation_id", "revoked_at", "reason"}
_TARGETS = {"prepared": "ready", "ready": "linked", "linked": "completed"}
_ERROR_CODES = {
    _PREFIX + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete", "record_invalid",
        "source_changed", "credential_invalid", "revoked", "clock_regressed", "policy_denied", "phase_changed",
        "io_failed", "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Sealed review write API returned an unsupported public response")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _shape(value: Any) -> None:
    """Bound supplied JSON without reading any live filesystem or authority."""
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
                raise WorkbenchError(_PREFIX + "summary_invalid", "Sealed public keys must be UTF-8 strings") from error
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
                raise WorkbenchError(_PREFIX + "summary_invalid", "Sealed public values must be UTF-8 strings") from error
        else:
            _require(child is None or type(child) is bool)


def _bounded(value: Any, limit: int = 64 * 1024) -> Any:
    _require(len(canonical(value).encode("utf-8")) <= limit)
    return value


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items())


def _public_receipt(value: Any) -> dict[str, Any]:
    """Validate compact captured issuance without reconstructing its full seal."""
    _shape(value)
    value = _object(value, _RECEIPT_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["seal_id"], "wrrws")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and _address(value["plan_id"], "wrrp")
             and type(value["policy_authorized"]) is bool and value["policy_authorized"] is True and _flags(value))
    try:
        issuer, rule, guard = _public_issuer(value["issuer"]), _public_rule(value["rule"]), _public_guard(value["guard"])
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Sealed receipt has unsupported captured issuance scope") from error
    _require(value["operation_id"] == guard["operation_id"]
             and sum(canonical(rule) == canonical(candidate) for candidate in issuer["policy"]) == 1
             and rule["plan_id"] == value["plan_id"] and rule["principal"] == guard["principal"]
             and all(rule[key] == guard["write"][key] for key in ("actor", "verdict"))
             and issuer["created_at"] <= guard["created_at"] <= value["created_at"])
    return _bounded({"rules_version": _RULES_VERSION,
                     **{key: value[key] for key in ("seal_id", "operation_id", "created_at", "plan_id")},
                     "issuer": issuer, "rule": rule, "guard": guard, "policy_authorized": True, **_FLAGS})


def _public_access(value: Any) -> dict[str, Any]:
    """Observe own revocation separately from the historical sealed receipt."""
    _shape(value)
    value = _object(value, _ACCESS_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and isinstance(value["access_state"], str)
             and value["access_state"] in {"active", "revoked"} and _flags(value))
    seal = _public_receipt(value["seal"])
    revocation = None
    if value["access_state"] == "revoked":
        revocation = _object(value["revocation"], _REVOCATION_FIELDS)
        _require(revocation["rules_version"] == _RULES_VERSION and revocation["seal_id"] == seal["seal_id"]
                 and _identifier(revocation["operation_id"]) and _timestamp(revocation["revoked_at"])
                 and revocation["revoked_at"] >= seal["created_at"] and revocation["reason"] == "holder_revoked")
        revocation = {key: revocation[key] for key in sorted(_REVOCATION_FIELDS)}
    else:
        _require(value["revocation"] is None)
    return _bounded({"rules_version": _RULES_VERSION, "seal": seal,
                     "access_state": value["access_state"], "revocation": revocation, **_FLAGS})


def _public_status(value: Any) -> dict[str, Any]:
    """Validate one live write observation against the immutable sealed scope."""
    _shape(value)
    value = _object(value, _STATUS_FIELDS)
    access = _public_access({key: value[key] for key in _ACCESS_FIELDS})
    try:
        write = _write_status(value["write"])
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Sealed status has an unsupported historical write") from error
    observed = {
        **{key: write[key] for key in ("rules_version", "write_id", "operation_id", "created_at")},
        **{key: write["intended_review"][key] for key in ("review_id", "actor", "verdict")},
    }
    _require(canonical(observed) == canonical(access["seal"]["guard"]["write"]))
    return _bounded({**access, "write": write}, 16 * 1024 * 1024)


def _public_observation(value: Any) -> dict[str, Any]:
    """Validate the historical target of one named sealed write phase."""
    _shape(value)
    value = _object(value, _OBSERVATION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["seal_id"], "wrrws")
             and _identifier(value["operation_id"]) and _timestamp(value["recorded_at"])
             and isinstance(value["expected_state"], str) and value["expected_state"] in _TARGETS
             and value["target_state"] == _TARGETS[value["expected_state"]]
             and value["observed_state"] == value["target_state"] and isinstance(value["outcome"], str)
             and value["outcome"] in {"phase_performed", "expected_phase_observed"}
             and type(value["effect_performed"]) is bool
             and value["effect_performed"] is (value["outcome"] == "phase_performed"))
    return _bounded({key: value[key] for key in sorted(_OBSERVATION_FIELDS)})


def _public_execution(value: Any) -> dict[str, Any]:
    """Validate a stable protected delivery without claiming past authorship."""
    _shape(value)
    value = _object(value, _EXECUTION_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _flags(value)
             and type(value["credential_verified"]) is bool and value["credential_verified"] is True
             and type(value["revocation_checked"]) is bool and value["revocation_checked"] is True
             and type(value["historical_effect_authenticated"]) is bool
             and value["historical_effect_authenticated"] is False)
    seal, observation = _public_receipt(value["seal"]), _public_observation(value["observation"])
    _require(observation["seal_id"] == seal["seal_id"] and observation["recorded_at"] >= seal["created_at"])
    return _bounded({"rules_version": _RULES_VERSION, "seal": seal, "observation": observation,
                     "credential_verified": True, "revocation_checked": True,
                     "historical_effect_authenticated": False, **_FLAGS})


_public_step = _public_execution


def _public_response(value: Any, action: str) -> dict[str, Any]:
    _require(action in {"issue", "receipt", "show", "status", "step", "revoke"})
    if action in {"issue", "receipt"}:
        return _public_receipt(value)
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
        raise WorkbenchError(_PREFIX + "private_path_required", "Sealed write credentials require private owner files") from error


def _credential_location(path: Path, directories: list[Path]) -> Path:
    location = store_backup._path(path)
    if (not directories or location.parent != directories[0].parent
            or any(directory.parent != location.parent or location == directory for directory in directories)):
        raise WorkbenchError(_PREFIX + "private_path_required", "Sealed credentials need separate private sibling files")
    return location


def _job_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--job", required=True, type=Path, help="Частный каталог sealed job с полной записью выдачи.")


def register_restore_case_retention_review_write_sealed_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write-sealed",
        help="Создать и выполнить review job, чья выдача и намерение сохранены одной записью.",
        description=(
            "Issue сохраняет полное намерение и свидетельство выдачи в одном sealed record. Step требует этот record, "
            "ключ держателя и отсутствие отзыва. Старые write и guarded API этот формат не выбирают. "
            "Отзыв родительского издателя закрывает новую выдачу, а holder отзывает свой job отдельно. "
            "Ошибка после сохранения полного seal может оставить действующее задание; откат и OS isolation не обеспечены."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_sealed_command", required=True)
    issue = commands.add_parser("issue", help="Создать свежий sealed job по точной политике прежнего издателя.")
    issue.add_argument("--issuer", required=True, type=Path, help="Существующий частный guarded issuer с Rule5 policy.")
    issue.add_argument("--plan", required=True, type=Path, help="Retention plan с точным ID из policy.")
    issue.add_argument("--review", required=True, type=Path, help="Новое частное назначение исторического review.")
    _job_argument(issue)
    issue.add_argument("--operation-id", required=True, help="ID точного запроса выдачи и вложенного намерения.")
    issue.add_argument("--principal", required=True, help="Самозаявленная локальная метка holder из policy.")
    issue.add_argument("--actor", required=True, help="Самозаявленный actor исторического review из policy.")
    issue.add_argument("--verdict", required=True, help="reviewed, defer либо request_fresh_plan из policy.")
    issue.add_argument("--issuer-credential-file", required=True, type=Path, help="Private .credential оператора издателя.")
    holder = issue.add_mutually_exclusive_group(required=True)
    holder.add_argument("--holder-credential-out", type=Path, help="Новый отдельный private .credential holder; не перезаписывается.")
    holder.add_argument("--prepared-holder-credential", type=Path, help="Сохранённый private holder credential для точного повтора.")
    for name, help_text in (
        ("receipt", "Читать собственную историческую выдачу без issuer, plan и live review."),
        ("show", "Наблюдать собственный seal, доступ и отзыв без live источников."),
        ("status", "Наблюдать текущую фазу sealed job и совместимого review."),
    ):
        command = commands.add_parser(name, help=help_text)
        _job_argument(command)
    step = commands.add_parser("step", help="Допустить одну точную фазу либо её исторический повтор ключом holder.")
    _job_argument(step)
    step.add_argument("--operation-id", required=True, help="Отдельный ID точного вызова этой фазы.")
    step.add_argument("--expected-state", required=True, choices=sorted(_TARGETS), help="Точная исходная фаза.")
    revoke = commands.add_parser("revoke", help="Ключом holder закрыть будущие шаги и защищённую выдачу cached results.")
    _job_argument(revoke)
    revoke.add_argument("--operation-id", required=True, help="ID точного необратимого отзыва holder.")
    for command in (step, revoke):
        command.add_argument("--credential-file", required=True, type=Path, help="Private .credential holder.")


def run_restore_case_retention_review_write_sealed_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_sealed_command
    if action not in {"issue", "receipt", "show", "status", "step", "revoke"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_sealed_command", "Unknown sealed review write command")
    try:
        from . import workspace_restore_case_retention_review_write_sealed as sealed

        job = store_backup._path(args.job)
        if action == "issue":
            directories = [store_backup._path(args.issuer), store_backup._path(args.plan),
                           store_backup._path(args.review), job]
            if len(set(directories)) != 4 or any(path.parent != job.parent for path in directories):
                raise WorkbenchError(_PREFIX + "invalid_input", "Sealed issuance needs four distinct sibling paths")
            administrator_path = _credential_location(args.issuer_credential_file, directories)
            holder_path = _credential_location(args.holder_credential_out or args.prepared_holder_credential, directories)
            if administrator_path == holder_path:
                raise WorkbenchError(_PREFIX + "invalid_input", "Issuer and holder require separate credential files")
            administrator = _credential(administrator_path)
            credential = _credential(holder_path, prepare=args.holder_credential_out is not None)
            if hmac.compare_digest(administrator, credential):
                raise WorkbenchError(_PREFIX + "invalid_input", "Issuer and holder require different local bearer values")
            result = sealed.issue_sealed_review_write(
                args.issuer, args.plan, args.review, args.job, operation_id=args.operation_id,
                actor=args.actor, verdict=args.verdict, principal=args.principal,
                issuer_credential=administrator, credential=credential,
            )
        elif action == "receipt":
            result = sealed.inspect_sealed_review_write_receipt(args.job)
        elif action == "show":
            result = sealed.inspect_sealed_review_write_access(args.job)
        elif action == "status":
            result = sealed.inspect_sealed_review_write(args.job)
        else:
            location = _credential_location(args.credential_file, [job])
            credential = _credential(location)
            if action == "step":
                result = sealed.step_sealed_review_write(
                    args.job, operation_id=args.operation_id, expected_state=args.expected_state, credential=credential,
                )
            else:
                result = sealed.revoke_sealed_review_write(args.job, operation_id=args.operation_id, credential=credential)
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else _PREFIX + "command_failed"
        raise WorkbenchError(code, "Sealed review write command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError(_PREFIX + "command_failed", "Sealed review write command failed") from error
