"""Source write-fence commands with file credentials and safe public output.

The existing source is preflighted read-only, without constructing Store. A
private recovery plan and request fingerprint never become a command response.
The local source epoch is a fence identifier, not external fencing authority.
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
    _existing_database,
    _redact_text,
    _redact_value,
    _workspace_argument,
    read_credential,
)
from .store import WorkbenchError


_RULES_VERSION = "source-fence-1"
_RECEIPT_FLAGS = {
    "source_writes_blocked": True,
    "publication_blocked": True,
    "irreversible": True,
    "snapshot_activation_allowed": False,
    "external_fencing_established": False,
    "trusted_database_owner_can_bypass": True,
}
_RECEIPT_FIELDS = {
    "schema_version", "rules_version", "fence_id", "source_epoch", "operation_id",
    "plan_id", "administrator_grant_id", "created_at", "revision",
    "event_high_water_before", "event_count_before", "fence_event_seq",
    *_RECEIPT_FLAGS,
}
_STATUS_FIELDS = {
    "rules_version", "source_fenced", "source_writes_blocked",
    "publication_blocked", "irreversible", "receipt",
}
_OPERATION_ID = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
_BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError("source_fence_summary_invalid", "Source fence API returned an unsupported public response")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _address(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(prefix + r"_[0-9a-f]{32}", value) is not None


def _public_receipt(value: Any, *, replay: bool) -> dict[str, Any]:
    fields = _RECEIPT_FIELDS | ({"replayed"} if replay else set())
    value = _object(value, fields)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["rules_version"] == _RULES_VERSION
             and _address(value["fence_id"], "sfn")
             and _address(value["source_epoch"], "sfe")
             and _address(value["plan_id"], "rcp")
             and _address(value["administrator_grant_id"], "gvg")
             and isinstance(value["operation_id"], str)
             and _OPERATION_ID.fullmatch(value["operation_id"]) is not None
             and _BEARER.search(value["operation_id"]) is None
             and _HASH.fullmatch(value["operation_id"]) is None
             and type(value["created_at"]) in {int, float}
             and 0 < value["created_at"] < 2**63 and math.isfinite(value["created_at"])
             and all(_integer(value[key]) for key in (
                 "revision", "event_high_water_before", "event_count_before", "fence_event_seq"))
             and value["event_count_before"] <= value["event_high_water_before"] < value["fence_event_seq"]
             and all(type(value[key]) is bool and value[key] is expected
                     for key, expected in _RECEIPT_FLAGS.items()))
    if replay:
        _require(type(value["replayed"]) is bool)
    # Copy only the exact safe fields, rather than an API-private envelope.
    return {key: value[key] for key in sorted(fields)}


def _public_response(value: Any, action: str, secrets: list[str]) -> dict[str, Any]:
    """Validate every public layer before redacting known bearer values."""
    if action == "seal":
        return _redact_value(_public_receipt(value, replay=True), secrets)
    value = _object(value, _STATUS_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION and type(value["source_fenced"]) is bool)
    fenced = value["source_fenced"]
    _require(all(type(value[key]) is bool and value[key] is fenced
                 for key in ("source_writes_blocked", "publication_blocked", "irreversible")))
    if fenced:
        receipt = _public_receipt(value["receipt"], replay=False)
    else:
        _require(value["receipt"] is None)
        receipt = None
    result = {
        "rules_version": _RULES_VERSION, "source_fenced": fenced,
        "source_writes_blocked": fenced, "publication_blocked": fenced,
        "irreversible": fenced, "receipt": receipt,
    }
    return _redact_value(result, secrets)


def register_fence_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register an irreversible source stop and read-only status inspection."""
    parser = subparsers.add_parser(
        "store-fence", help="Постоянно остановить записи источника по свежему частному плану.",
        description=(
            "Seal проверяет текущий административный допуск и точное состояние частного плана "
            "под блокировкой записи SQLite, затем необратимо запрещает новые записи Store и publish. "
            "Точный повтор той же операции возвращает прежнюю квитанцию. "
            "Копии сохраняют карантин; локальный source epoch не является внешним fencing. "
            "Частный план и отпечатки запросов не выводятся."
        ),
    )
    commands = parser.add_subparsers(dest="fence_command", required=True)
    seal = commands.add_parser("seal", help="Необратимо остановить источник; точный повтор возвращает прежний результат.")
    _workspace_argument(seal)
    seal.add_argument("--plan", required=True, type=Path, help="Частный recovery-plan 0600 для этого источника.")
    _credential_argument(seal)
    seal.add_argument("--operation-id", required=True,
                      help="Ограниченный ID операции; сохраните его и исходный план для точного повтора.")
    status = commands.add_parser("status", help="Прочитать метку остановки существующего источника.")
    _workspace_argument(status)


def run_fence_command(args: argparse.Namespace) -> dict[str, Any]:
    """Run against an existing source without initialization or private output."""
    action = args.fence_command
    if action not in {"seal", "status"}:
        raise WorkbenchError("unknown_source_fence_command", "Unknown source fence command")
    secrets: list[str] = []
    try:
        from . import source_fence, store_backup

        credential = None
        if action == "seal":
            credential = read_credential(args.credential)
            secrets.append(credential)
        try:
            store_backup._no_symlinks(store_backup._path(args.workspace) / store_backup.DATABASE_NAME)
        except WorkbenchError as error:
            raise WorkbenchError("source_fence_source_invalid", "An existing source path without symlinks is required") from error
        database = _existing_database(args.workspace)
        if action == "seal":
            result = source_fence.seal_source(
                database, args.plan, credential=credential, operation_id=args.operation_id)
        else:
            result = source_fence.source_fence_status(database)
        return _public_response(result, action, secrets)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("source_fence_command_failed", "Source fence command failed") from error
