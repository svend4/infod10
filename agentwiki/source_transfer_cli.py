"""Create a fresh workspace from one fenced source with safe public output.

Credentials are read only from private files. Source and destination paths are
preflighted without Store initialization; private origin metadata, manifests,
denylist entries and request fingerprints never become a command response.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

from .governance_cli import (
    _credential_argument,
    _redact_text,
    _redact_value,
    _workspace_argument,
    read_credential,
)
from .store import WorkbenchError


_RULES_VERSION = "source-transfer-1"
_FLAGS = {
    "old_authority_transferred": False,
    "dependencies_preserved": True,
    "external_fencing_established": False,
}
_RECEIPT_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "operation_id",
    "created_at", "source_fence_id", "source_epoch", "source_revision",
    "source_event_high_water_before", "transfer_event_seq", "artifact_count",
    "source_count", "new_administrator_id", *_FLAGS,
}
_DESTINATION_FIELDS = {
    "workspace_id", "revision", "artifact_count", "source_count", "governance_enabled",
    "authorship_enabled", "content_enabled", "continuity_enabled",
}
_REPORT_FIELDS = {"rules_version", "receipt", "destination", "replayed", "resumed", *_FLAGS}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
_BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError("source_transfer_summary_invalid", "Source transfer API returned an unsupported public response")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _integer(value: Any, *, positive: bool = False) -> bool:
    return type(value) is int and (1 if positive else 0) <= value < 2**63


def _address(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(prefix + r"_[0-9a-f]{32}", value) is not None


def _identifier(value: Any) -> bool:
    return (isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None
            and _BEARER.search(value) is None and _HASH.fullmatch(value) is None)


def _public_receipt(value: Any) -> dict[str, Any]:
    value = _object(value, _RECEIPT_FIELDS)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["rules_version"] == _RULES_VERSION
             and _address(value["transfer_id"], "sft")
             and _address(value["workspace_id"], "wsp")
             and _address(value["source_fence_id"], "sfn")
             and _address(value["source_epoch"], "sfe")
             and _identifier(value["operation_id"])
             and _identifier(value["new_administrator_id"])
             and type(value["created_at"]) in {int, float}
             and 0 < value["created_at"] < 2**63 and math.isfinite(value["created_at"])
             and _integer(value["source_revision"], positive=True)
             and _integer(value["artifact_count"], positive=True)
             and all(_integer(value[key]) for key in (
                 "source_event_high_water_before", "transfer_event_seq", "source_count"))
             and value["source_event_high_water_before"] < value["transfer_event_seq"]
             and all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items()))
    return {key: value[key] for key in sorted(_RECEIPT_FIELDS)}


def _public_response(value: Any, action: str, secrets: list[str]) -> dict[str, Any]:
    """Validate each public layer without forwarding a private API envelope."""
    value = _object(value, _REPORT_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION
             and type(value["replayed"]) is bool and type(value["resumed"]) is bool
             and all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items()))
    if action == "status":
        _require(value["replayed"] is True and value["resumed"] is False)
    receipt = _public_receipt(value["receipt"])
    destination = _object(value["destination"], _DESTINATION_FIELDS)
    _require(destination["workspace_id"] == receipt["workspace_id"]
             and _integer(destination["revision"], positive=True)
             and destination["revision"] == receipt["source_revision"]
             and _integer(destination["artifact_count"], positive=True)
             and destination["artifact_count"] == receipt["artifact_count"]
             and _integer(destination["source_count"])
             and destination["source_count"] == receipt["source_count"]
             and all(type(destination[key]) is bool for key in (
                 "governance_enabled", "authorship_enabled", "content_enabled", "continuity_enabled"))
             and destination["governance_enabled"] is True)
    result = {
        "rules_version": _RULES_VERSION, "receipt": receipt,
        "destination": {key: destination[key] for key in sorted(_DESTINATION_FIELDS)},
        "replayed": value["replayed"], "resumed": value["resumed"], **_FLAGS,
    }
    return _redact_value(result, secrets)


def register_transfer_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register fresh-authority transfer and read-only completion status."""
    parser = subparsers.add_parser(
        "store-transfer", help="Создать новую рабочую среду из остановленного источника.",
        description=(
            "Create закрепляет одно назначение остановленного источника, сохраняет частную историю "
            "и создаёт новую базу с принятым содержанием и сохранёнными зависимостями. "
            "Новый администратор использует отдельный credential; старые допуски, задачи и решения "
            "не переносятся в активные таблицы. Частные manifests, отпечатки и denylist не выводятся."
        ),
    )
    commands = parser.add_subparsers(dest="transfer_command", required=True)
    create = commands.add_parser("create", help="Перенести принятое содержание с новыми полномочиями.")
    _workspace_argument(create)
    _credential_argument(create)
    create.add_argument("--destination", required=True, type=Path,
                        help="Одно частное назначение переноса; чужие существующие данные не заменяются.")
    create.add_argument("--new-administrator", required=True,
                        help="Локальный ID нового администратора назначения.")
    create.add_argument("--new-credential", required=True, type=Path,
                        help="Существующий частный файл нового admin credential; секрет не печатается.")
    create.add_argument("--operation-id", required=True,
                        help="Сохранённый ID точного запроса для повтора или продолжения переноса.")
    status = commands.add_parser("status", help="Прочитать сохранённый факт завершённого переноса.")
    _workspace_argument(status)


def run_transfer_command(args: argparse.Namespace) -> dict[str, Any]:
    """Use existing private inputs without initializing either workspace."""
    action = args.transfer_command
    if action not in {"create", "status"}:
        raise WorkbenchError("unknown_source_transfer_command", "Unknown source transfer command")
    secrets: list[str] = []
    try:
        from . import source_transfer, store_backup

        if action == "create":
            credential = read_credential(args.credential)
            secrets.append(credential)
            new_credential = read_credential(args.new_credential)
            secrets.append(new_credential)
            try:
                database = store_backup._path(args.workspace) / store_backup.DATABASE_NAME
                store_backup._no_symlinks(database)
                # The core performs bounded schema/revision checks. A second
                # SQL preflight here would load revision text before its guard.
                with store_backup._regular_file(
                    database, private=False, maximum=store_backup.MAX_DATABASE_BYTES,
                    missing="source_transfer_source_invalid",
                ):
                    pass
            except WorkbenchError as error:
                code = ("source_transfer_limit_exceeded" if error.code == "snapshot_limit_exceeded"
                        else "source_transfer_source_invalid")
                raise WorkbenchError(code, "An existing supported source file without symlinks is required") from error
            result = source_transfer.transfer_source(
                database, args.destination, credential=credential,
                new_administrator_id=args.new_administrator, new_credential=new_credential,
                operation_id=args.operation_id)
        else:
            result = source_transfer.transfer_status(args.workspace)
        return _public_response(result, action, secrets)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("source_transfer_command_failed", "Source transfer command failed") from error
