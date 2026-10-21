"""Commands for complete private workspace history and quarantined restores.

The source preflight performs filesystem checks without constructing Store or
reading unbounded SQL values. Only fixed public counters and transfer receipts
reach the command response; manifests, paths and credential hashes stay private.
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


_RULES_VERSION = "workspace-backup-1"
_FLAGS = {
    "inspection_only": True,
    "domain_writes_allowed": False,
    "full_history_preserved": True,
    "old_authority_activated": False,
    "external_fencing_established": False,
}
_PUBLIC_FIELDS = {
    "rules_version", "kind", "workspace_backup_id", "created_at", "workspace_id",
    "revision", "event_high_water", "event_count", "table_counts", "archive_count",
    "receipts", "integrity", "foreign_keys", *_FLAGS,
}
_RECEIPT_FLAGS = {
    "dependencies_preserved": True,
    "old_authority_transferred": False,
    "external_fencing_established": False,
}
_RECEIPT_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "operation_id",
    "created_at", "source_fence_id", "source_epoch", "source_revision",
    "source_event_high_water_before", "transfer_event_seq", "artifact_count",
    "source_count", "new_administrator_id", *_RECEIPT_FLAGS,
}
_TABLE_NAMES = {
    "meta", "sources", "artifacts", "tasks", "proposals", "proposal_components",
    "reviews", "revisions", "events", "governance_settings", "governance_policies",
    "governance_grants", "governance_revocations", "governance_decisions",
    "governance_heads", "governance_operations", "governance_receipts",
    "authorship_settings", "authorship_leases", "proposal_authorship",
    "content_settings", "content_policies", "content_packets",
    "review_continuity_settings", "review_continuity_transitions",
    "review_history_audits", "review_history_audit_checkpoints",
    "review_history_audit_heads", "review_history_audit_operations",
}
_IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
_BEARER = re.compile(r"awg_[A-Za-z0-9_-]{43}")
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError("workspace_backup_summary_invalid", "Workspace backup API returned an unsupported public summary")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _integer(value: Any, *, positive: bool = False) -> bool:
    return type(value) is int and (1 if positive else 0) <= value < 2**63


def _timestamp(value: Any) -> bool:
    return type(value) in {int, float} and 0 < value < 2**63 and math.isfinite(value)


def _address(value: Any, prefix: str) -> bool:
    return isinstance(value, str) and re.fullmatch(prefix + r"_[0-9a-f]{32}", value) is not None


def _identifier(value: Any) -> bool:
    return (isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None
            and _BEARER.search(value) is None and _HASH.fullmatch(value) is None)


def _public_receipt(value: Any) -> dict[str, Any]:
    value = _object(value, _RECEIPT_FIELDS)
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1
             and value["rules_version"] == "source-transfer-1"
             and _address(value["transfer_id"], "sft")
             and _address(value["workspace_id"], "wsp")
             and _address(value["source_fence_id"], "sfn")
             and _address(value["source_epoch"], "sfe")
             and _identifier(value["operation_id"])
             and _identifier(value["new_administrator_id"])
             and _timestamp(value["created_at"])
             and _integer(value["source_revision"], positive=True)
             and _integer(value["artifact_count"], positive=True)
             and all(_integer(value[key]) for key in (
                 "source_count", "source_event_high_water_before", "transfer_event_seq"))
             and value["source_event_high_water_before"] < value["transfer_event_seq"]
             and all(type(value[key]) is bool and value[key] is expected
                     for key, expected in _RECEIPT_FLAGS.items()))
    return {key: value[key] for key in sorted(_RECEIPT_FIELDS)}


def _public_summary(value: Any, secrets: list[str], action: str) -> dict[str, Any]:
    """Expose an exact safe summary, including only bounded public receipts."""
    value = _object(value, _PUBLIC_FIELDS)
    _require(value["rules_version"] == _RULES_VERSION
             and isinstance(value["kind"], str) and value["kind"] in {"backup", "restore"}
             and (action == "inspect" or value["kind"] == ("backup" if action == "create" else "restore"))
             and _address(value["workspace_backup_id"], "wbk")
             and _address(value["workspace_id"], "wsp")
             and _timestamp(value["created_at"])
             and _integer(value["revision"], positive=True)
             and _integer(value["event_count"])
             and _integer(value["event_high_water"])
             and value["event_count"] <= value["event_high_water"]
             and value["integrity"] == value["foreign_keys"] == "ok"
             and all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items()))
    tables = _object(value["table_counts"], _TABLE_NAMES)
    _require(all(_integer(count) for count in tables.values()) and tables["events"] == value["event_count"]
             and _integer(value["archive_count"], positive=True) and value["archive_count"] <= 16
             and isinstance(value["receipts"], list) and len(value["receipts"]) == value["archive_count"])
    receipts = [_public_receipt(receipt) for receipt in value["receipts"]]
    _require(len({receipt["transfer_id"] for receipt in receipts}) == len(receipts)
             and len({receipt["workspace_id"] for receipt in receipts}) == len(receipts)
             and receipts[-1]["workspace_id"] == value["workspace_id"]
             and all(receipt["source_revision"] <= value["revision"] for receipt in receipts))
    summary = {key: value[key] for key in sorted(_PUBLIC_FIELDS)}
    summary["table_counts"] = {key: tables[key] for key in sorted(_TABLE_NAMES)}
    summary["receipts"] = receipts
    return _redact_value(summary, secrets)


def _destination_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--destination", required=True, type=Path,
                        help="Новый частный каталог; существующие данные и незавершённые остатки не заменяются.")


def _bundle_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle", required=True, type=Path,
                        help="Готовый частный комплект базы и всех архивов её происхождения.")


def register_workspace_backup_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register complete private backup, inspection and quarantined restoration."""
    parser = subparsers.add_parser(
        "workspace-backup", help="Сохранить рабочую базу вместе со всей цепочкой архивов.",
        description=(
            "Полный частный комплект содержит согласованный снимок рабочей базы и все архивы предков. "
            "Create требует действующего администратора импортированной среды. Restore создаёт "
            "карантинную копию только для чтения истории; прежние полномочия не активируются. "
            "Каталоги имеют права 0700, файлы 0600. Хеши, частные пути и manifests не выводятся."
        ),
    )
    commands = parser.add_subparsers(dest="workspace_backup_command", required=True)
    create = commands.add_parser("create", help="Создать полный частный комплект действующей импортированной среды.")
    _workspace_argument(create)
    _credential_argument(create)
    _destination_argument(create)
    inspect = commands.add_parser("inspect", help="Проверить полный комплект и вывести безопасную сводку.")
    _bundle_argument(inspect)
    restore = commands.add_parser("restore", help="Восстановить полный комплект в новый каталог для чтения истории.")
    _bundle_argument(restore)
    _destination_argument(restore)


def run_workspace_backup_command(args: argparse.Namespace) -> dict[str, Any]:
    """Dispatch without source initialization or disclosure of private metadata."""
    action = args.workspace_backup_command
    if action not in {"create", "inspect", "restore"}:
        raise WorkbenchError("unknown_workspace_backup_command", "Unknown workspace backup command")
    secrets: list[str] = []
    try:
        from . import store_backup, workspace_backup

        if action == "create":
            credential = read_credential(args.credential)
            secrets.append(credential)
            try:
                database = store_backup._path(args.workspace) / store_backup.DATABASE_NAME
                store_backup._no_symlinks(database)
                # Revision/schema reads belong to the bounded core validation.
                with store_backup._regular_file(
                    database, private=False, maximum=store_backup.MAX_DATABASE_BYTES,
                    missing="workspace_backup_source_invalid",
                ):
                    pass
            except WorkbenchError as error:
                code = ("workspace_backup_limit_exceeded" if error.code == "snapshot_limit_exceeded"
                        else "workspace_backup_source_invalid")
                raise WorkbenchError(code, "An existing supported source file without symlinks is required") from error
            result = workspace_backup.create_backup(database, args.destination, credential=credential)
        elif action == "inspect":
            result = workspace_backup.inspect_bundle(args.bundle)
        else:
            result = workspace_backup.restore_bundle(args.bundle, args.destination)
        return _public_summary(result, secrets, action)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("workspace_backup_command_failed", "Workspace backup command failed") from error
