"""Bounded local issuance policy and immutable historical permit receipts.

Public validators use only their arguments. Credentials remain in private
files and are never accepted as literal command-line values.
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
from .workspace_restore_case_retention_review_write_permit_cli import (
    _prepare_private_credential as _prepare_permit_credential,
    _public_grant,
    _read_private_credential as _read_permit_credential,
)
from .workspace_restore_job_cli import _address, _identifier


_RULES_VERSION = "workspace-restore-case-retention-review-write-issuer-1"
_PREFIX = "workspace_restore_case_retention_review_write_issuer_"
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
    "worker_authenticated": False,
    "local_capability_only": True,
    "external_identity_verified": False,
    "issuance_policy_only": True,
    "existing_permits_revoked": False,
}
_TARGETS = {"prepared": "ready", "staging": "ready", "ready": "linked", "linked": "completed"}
_RULE_FIELDS = {"rule_id", "principal", "worker", "ledger_id", "index", "before_state", "target_state"}
_ISSUER_FIELDS = {"rules_version", "issuer_id", "operation_id", "created_at", "administrator", "policy", *_FLAGS}
_SHOW_FIELDS = {"rules_version", "issuer", "state", "revocation", *_FLAGS}
_REVOCATION_FIELDS = {"rules_version", "issuer_id", "operation_id", "revoked_at", "reason"}
_ISSUANCE_FIELDS = {
    "rules_version", "issuance_id", "operation_id", "created_at", "issuer", "rule", "permit", "policy_authorized", *_FLAGS,
}
_ERROR_CODES = {
    _PREFIX + suffix for suffix in (
        "invalid_input", "private_path_required", "limit_exceeded", "conflict", "incomplete", "record_invalid",
        "source_changed", "credential_invalid", "revoked", "clock_regressed", "policy_denied", "io_failed",
        "summary_invalid",
    )
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Permit issuer API returned an unsupported public response")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _flags(value: dict[str, Any]) -> bool:
    return all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items())


def _public_rule(value: Any) -> dict[str, Any]:
    value = _object(value, _RULE_FIELDS)
    _require(all(_identifier(value[key]) for key in ("rule_id", "principal", "worker"))
             and _address(value["ledger_id"], "wrrwlgr")
             and type(value["index"]) is int and 1 <= value["index"] <= 256
             and isinstance(value["before_state"], str) and value["before_state"] in _TARGETS
             and value["target_state"] == _TARGETS[value["before_state"]])
    return {key: value[key] for key in sorted(_RULE_FIELDS)}


def _public_policy(value: Any) -> list[dict[str, Any]]:
    """Keep all ordered exact rules, rejecting duplicate IDs and scopes."""
    _require(isinstance(value, list) and 1 <= len(value) <= 16)
    policy = [_public_rule(rule) for rule in value]
    _require(len({rule["rule_id"] for rule in policy}) == len(policy)
             and len({canonical({key: child for key, child in rule.items() if key != "rule_id"})
                      for rule in policy}) == len(policy))
    return policy


def _public_issuer(value: Any) -> dict[str, Any]:
    value = _object(value, _ISSUER_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["issuer_id"], "wrrwpir")
             and _identifier(value["operation_id"]) and _identifier(value["administrator"])
             and _timestamp(value["created_at"]) and _flags(value))
    policy = _public_policy(value["policy"])
    result = {"rules_version": _RULES_VERSION,
              **{key: value[key] for key in ("issuer_id", "operation_id", "created_at", "administrator")},
              "policy": policy, **_FLAGS}
    _require(len(canonical(result).encode("utf-8")) <= 64 * 1024)
    return result


def _public_show(value: Any) -> dict[str, Any]:
    value = _object(value, _SHOW_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and isinstance(value["state"], str)
             and value["state"] in {"active", "revoked"} and _flags(value))
    issuer = _public_issuer(value["issuer"])
    revocation = None
    if value["state"] == "revoked":
        revocation = _object(value["revocation"], _REVOCATION_FIELDS)
        _require(revocation["rules_version"] == _RULES_VERSION and revocation["issuer_id"] == issuer["issuer_id"]
                 and _identifier(revocation["operation_id"]) and _timestamp(revocation["revoked_at"])
                 and revocation["revoked_at"] >= issuer["created_at"]
                 and revocation["reason"] == "administrator_revoked")
        revocation = {key: revocation[key] for key in sorted(_REVOCATION_FIELDS)}
    else:
        _require(value["revocation"] is None)
    result = {"rules_version": _RULES_VERSION, "issuer": issuer, "state": value["state"],
              "revocation": revocation, **_FLAGS}
    _require(len(canonical(result).encode("utf-8")) <= 64 * 1024)
    return result


def _public_issuance(value: Any) -> dict[str, Any]:
    """Validate historical policy-approved issuance without current permission."""
    value = _object(value, _ISSUANCE_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and _address(value["issuance_id"], "wrrwpis")
             and _identifier(value["operation_id"]) and _timestamp(value["created_at"])
             and type(value["policy_authorized"]) is bool and value["policy_authorized"] is True and _flags(value))
    issuer, rule = _public_issuer(value["issuer"]), _public_rule(value["rule"])
    try:
        permit = _public_grant(value["permit"])
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "summary_invalid", "Issuance has an unsupported historical permit") from error
    action, lease = permit["action"], permit["action"]["lease"]
    _require(value["operation_id"] == permit["operation_id"]
             and sum(canonical(rule) == canonical(candidate) for candidate in issuer["policy"]) == 1
             and rule["principal"] == permit["principal"]
             and all(rule[key] == lease[key] for key in ("worker", "ledger_id", "index"))
             and all(rule[key] == action[key] for key in ("before_state", "target_state"))
             and issuer["created_at"] <= permit["created_at"] <= value["created_at"])
    result = {"rules_version": _RULES_VERSION,
              **{key: value[key] for key in ("issuance_id", "operation_id", "created_at")},
              "issuer": issuer, "rule": rule, "permit": permit, "policy_authorized": True, **_FLAGS}
    _require(len(canonical(result).encode("utf-8")) <= 64 * 1024)
    return result


def _public_response(value: Any, action: str) -> dict[str, Any]:
    _require(action in {"create", "show", "revoke", "issue", "receipt"})
    if action == "create":
        return _public_issuer(value)
    if action in {"issue", "receipt"}:
        return _public_issuance(value)
    result = _public_show(value)
    if action == "revoke":
        _require(result["state"] == "revoked")
    return result


def _policy_arguments(rows: Any) -> list[dict[str, Any]]:
    try:
        if not isinstance(rows, list) or not 1 <= len(rows) <= 16:
            raise ValueError("Unsupported rule count")
        policy = []
        for row in rows:
            if not isinstance(row, list) or len(row) != 6:
                raise ValueError("Unsupported rule")
            rule_id, principal, worker, ledger_id, index, before = row
            policy.append({"rule_id": rule_id, "principal": principal, "worker": worker,
                           "ledger_id": ledger_id, "index": int(index), "before_state": before,
                           "target_state": _TARGETS.get(before)})
        return _public_policy(policy)
    except (WorkbenchError, ValueError, TypeError, OverflowError) as error:
        raise WorkbenchError(_PREFIX + "invalid_input", "Issuer policy requires bounded unique exact rules") from error


def _credential(path: Path, *, prepare: bool = False) -> str:
    try:
        return _prepare_permit_credential(path) if prepare else _read_permit_credential(path)
    except WorkbenchError as error:
        raise WorkbenchError(_PREFIX + "private_path_required", "Issuer credentials require private owner files") from error


def _credential_location(path: Path, directories: list[Path]) -> Path:
    location = store_backup._path(path)
    if (not directories or location.parent != directories[0].parent
            or any(directory.parent != location.parent or location == directory for directory in directories)):
        raise WorkbenchError(_PREFIX + "private_path_required", "Issuer credentials need separate private sibling files")
    return location


def _issuer_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--issuer", required=True, type=Path, help="Частный каталог издателя и его неизменяемой политики.")


def register_restore_case_retention_review_write_issuer_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "workspace-restore-case-retention-review-write-issuer",
        help="Выдать permit по точной локальной политике отдельного издателя.",
        description=(
            "Create закрепляет явные правила и отдельный ключ оператора. Issue проверяет ключ и точный scope "
            "перед новой выдачей; receipt читает сохранённую историческую квитанцию. Отзыв издателя блокирует "
            "новую выдачу, сохраняя точный replay и прежние permits. Старые API остаются доступны отдельно; "
            "личность человека, независимость моделей и смысловая истинность не устанавливаются."
        ),
    )
    commands = parser.add_subparsers(dest="restore_case_retention_review_write_issuer_command", required=True)
    create = commands.add_parser("create", help="Закрепить bounded политику и ключ отдельного оператора.")
    _issuer_argument(create)
    create.add_argument("--operation-id", required=True, help="ID точного создания издателя.")
    create.add_argument("--administrator", required=True, help="Самозаявленная локальная метка оператора.")
    credentials = create.add_mutually_exclusive_group(required=True)
    credentials.add_argument("--credential-out", type=Path, help="Новый private .credential; не перезаписывается.")
    credentials.add_argument("--prepared-credential", type=Path, help="Ранее сохранённый private credential для точного повтора.")
    create.add_argument("--rule", required=True, action="append", nargs=6,
                        metavar=("RULE_ID", "PRINCIPAL", "WORKER", "LEDGER_ID", "INDEX", "BEFORE_STATE"),
                        help="Одно из 1–16 точных правил; целевая фаза выводится из BEFORE_STATE.")
    show = commands.add_parser("show", help="Наблюдать собственную политику и отзыв без credentials.")
    _issuer_argument(show)
    revoke = commands.add_parser("revoke", help="Ключом оператора остановить будущую выдачу этого издателя.")
    _issuer_argument(revoke)
    revoke.add_argument("--operation-id", required=True, help="ID точного отзыва издателя.")
    revoke.add_argument("--credential-file", required=True, type=Path, help="Private .credential оператора.")
    issue = commands.add_parser("issue", help="Выдать новый native permit и сохранить отдельную immutable квитанцию.")
    _issuer_argument(issue)
    for name, help_text in (
        ("issuance", "Новый частный каталог квитанции этой выдачи."),
        ("permit", "Новый native permit; существующий не усыновляется."),
        ("action", "Точное существующее action."),
    ):
        issue.add_argument("--" + name, required=True, type=Path, help=help_text)
    issue.add_argument("--principal", required=True, help="Локальная метка держателя, совпадающая с policy rule.")
    issue.add_argument("--operation-id", required=True, help="ID точного запроса выдачи в выбранном issuance каталоге.")
    issue.add_argument("--issuer-credential-file", required=True, type=Path, help="Private .credential оператора издателя.")
    holder = issue.add_mutually_exclusive_group(required=True)
    holder.add_argument("--holder-credential-out", type=Path, help="Новый отдельный private .credential держателя.")
    holder.add_argument("--prepared-holder-credential", type=Path, help="Сохранённый private credential держателя для повтора.")
    receipt = commands.add_parser("receipt", help="Читать только собственную историческую квитанцию без live источников.")
    receipt.add_argument("--issuance", required=True, type=Path, help="Частный каталог завершённой выдачи.")


def run_restore_case_retention_review_write_issuer_command(args: argparse.Namespace) -> dict[str, Any]:
    action = args.restore_case_retention_review_write_issuer_command
    if action not in {"create", "show", "revoke", "issue", "receipt"}:
        raise WorkbenchError("unknown_restore_case_retention_review_write_issuer_command", "Unknown permit issuer command")
    try:
        from . import workspace_restore_case_retention_review_write_issuer as issuers

        if action == "receipt":
            result = issuers.inspect_review_write_permit_issuance(args.issuance)
        else:
            issuer_path = store_backup._path(args.issuer)
            if action == "create":
                policy = _policy_arguments(args.rule)
                location = _credential_location(args.credential_out or args.prepared_credential, [issuer_path])
                credential = _credential(location, prepare=args.credential_out is not None)
                result = issuers.create_review_write_permit_issuer(
                    args.issuer, operation_id=args.operation_id, administrator=args.administrator,
                    credential=credential, policy=policy,
                )
            elif action == "show":
                result = issuers.inspect_review_write_permit_issuer(args.issuer)
            elif action == "revoke":
                location = _credential_location(args.credential_file, [issuer_path])
                result = issuers.revoke_review_write_permit_issuer(
                    args.issuer, operation_id=args.operation_id, credential=_credential(location),
                )
            else:
                directories = [issuer_path, *[store_backup._path(path) for path in (args.issuance, args.permit, args.action)]]
                if len(set(directories)) != 4 or any(path.parent != issuer_path.parent for path in directories):
                    raise WorkbenchError(_PREFIX + "invalid_input", "Issuer, issuance, permit and action need distinct sibling paths")
                administrator_path = _credential_location(args.issuer_credential_file, directories)
                holder_path = _credential_location(args.holder_credential_out or args.prepared_holder_credential, directories)
                if administrator_path == holder_path:
                    raise WorkbenchError(_PREFIX + "invalid_input", "Issuer and holder require separate credentials")
                administrator = _credential(administrator_path)
                credential = _credential(holder_path, prepare=args.holder_credential_out is not None)
                if hmac.compare_digest(administrator, credential):
                    raise WorkbenchError(_PREFIX + "invalid_input", "Issuer and holder require different local bearer values")
                result = issuers.issue_review_write_permit(
                    args.issuer, args.issuance, args.permit, args.action, operation_id=args.operation_id,
                    principal=args.principal, issuer_credential=administrator, credential=credential,
                )
        return _public_response(result, action)
    except WorkbenchError as error:
        code = error.code if isinstance(error.code, str) and error.code in _ERROR_CODES else _PREFIX + "command_failed"
        raise WorkbenchError(code, "Permit issuer command could not complete") from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError,
            RecursionError, sqlite3.Error) as error:
        raise WorkbenchError(_PREFIX + "command_failed", "Permit issuer command failed") from error
