"""Commands for private Store snapshots and quarantined inspection copies.

Source preflight is read only and never constructs Store. Public output is
limited to nonsecret snapshot metadata; manifest checksums remain private.
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


_PUBLIC_FIELDS = {
    "rules_version", "kind", "backup_id", "created_at", "inspection_only",
    "domain_writes_allowed", "revision", "event_high_water", "event_count",
    "table_counts", "integrity", "foreign_keys",
}


def _public_summary(value: Any, secrets: list[str]) -> dict[str, Any]:
    """Expose only the snapshot API's fixed public metadata fields."""
    if not isinstance(value, dict) or not _PUBLIC_FIELDS.issubset(value):
        raise WorkbenchError("snapshot_summary_invalid", "Snapshot API returned an unsupported public summary")
    summary = {key: value[key] for key in sorted(_PUBLIC_FIELDS)}
    if (summary["rules_version"] != "store-snapshot-1"
            or not isinstance(summary["backup_id"], str)
            or not re.fullmatch(r"sbk_[0-9a-f]{32}", summary["backup_id"])
            or summary["inspection_only"] is not True
            or summary["domain_writes_allowed"] is not False
            or summary["integrity"] != "ok" or summary["foreign_keys"] != "ok"
            or any(type(summary[key]) is not int or not 0 <= summary[key] < 2**63
                   for key in ("revision", "event_high_water", "event_count"))
            or not isinstance(summary["table_counts"], dict)
            or any(not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", name)
                   or type(count) is not int or not 0 <= count < 2**63
                   for name, count in summary["table_counts"].items())
            or not isinstance(summary["kind"], str)
            or summary["kind"] not in {"backup", "restore"}
            or type(summary["created_at"]) not in {int, float}
            or not 0 <= summary["created_at"] < 2**63
            or not math.isfinite(summary["created_at"])):
        raise WorkbenchError("snapshot_summary_invalid", "Snapshot API returned an unsupported public summary")
    return _redact_value(summary, secrets)


def _destination_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--destination", required=True, type=Path,
        help="Новый несуществующий каталог в частном родительском каталоге 0700; существующий пустой тоже отклоняется.",
    )


def _bundle_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--bundle", required=True, type=Path, help="Частный каталог готового снимка.")


def register_backup_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register private snapshot creation, validation and inspection restore."""
    parser = subparsers.add_parser(
        "store-backup", help="Создать частный снимок Store и восстановить копию для осмотра.",
        description=(
            "Снимок store-snapshot-1 создаётся из существующей базы без миграции источника. "
            "Родитель назначения должен принадлежать текущему владельцу и иметь права 0700; "
            "каталог назначения должен отсутствовать, даже пустой существующий не используется. "
            "Скопированная база уже помещена в карантин, база и частный manifest имеют права 0600. "
            "Восстановление создаёт только inspection-only копию: доменные записи и выпуск запрещены. "
            "Контрольные суммы остаются частными; снимок не подтверждает истинность прозы."
        ),
    )
    commands = parser.add_subparsers(dest="backup_command", required=True)

    create = commands.add_parser("create", help="Создать частный снимок существующей базы с административным допуском.")
    _workspace_argument(create)
    _credential_argument(create)
    _destination_argument(create)

    inspect = commands.add_parser("inspect", help="Проверить готовый снимок и вывести только безопасную сводку.")
    _bundle_argument(inspect)

    restore = commands.add_parser(
        "restore", help="Создать новую карантинную копию для осмотра без активации доменных записей.",
    )
    _bundle_argument(restore)
    _destination_argument(restore)


def run_backup_command(args: argparse.Namespace) -> dict[str, Any]:
    """Run a snapshot operation without constructing Store on the source."""
    action = args.backup_command
    if action not in {"create", "inspect", "restore"}:
        raise WorkbenchError("unknown_backup_command", "Unknown Store backup command")
    secrets: list[str] = []
    try:
        from . import store_backup

        if action == "create":
            credential = read_credential(args.credential)
            secrets.append(credential)
            source = _existing_database(args.workspace)
            result = store_backup.create_backup(source, args.destination, credential=credential)
        elif action == "inspect":
            result = store_backup.inspect_bundle(args.bundle)
        else:
            result = store_backup.restore_bundle(args.bundle, args.destination)
        return _public_summary(result, secrets)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, sqlite3.Error) as error:
        raise WorkbenchError("snapshot_command_failed", "Snapshot command failed") from error
