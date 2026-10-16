"""CLI helpers for proposal-bound, advisory editorial dossiers.

A dossier captures the exact proposal and editorial basis. Creating or
rechecking one never accepts a proposal, changes a decision, or executes code.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .demo import DATABASE_NAME
from .dossier import create_dossier, load_dossier, recheck_dossier, save_dossier
from .editorial import EditorialJournal
from .semantic import load_pair_file
from .store import Store, WorkbenchError


def register_dossier_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register dossier creation and read-only reassessment commands."""
    dossier = subparsers.add_parser(
        "dossier",
        help="Связать точное предложение с редакционным пакетом без принятия.",
        description=(
            "Сохранить основание рассмотрения конкретного предложения и повторно "
            "проверить его актуальность. Досье не предоставляет полномочий "
            "на принятие изменений и не выполняет предложенный код."
        ),
    )
    commands = dossier.add_subparsers(dest="dossier_command", required=True)

    create = commands.add_parser(
        "create", help="Сохранить досье предложения и редакционного пакета.",
    )
    create.add_argument("--proposal", required=True, help="ID сохранённого предложения Store.")
    create.add_argument("--packet", required=True, help="ID сохранённого редакционного пакета.")
    create.add_argument("--artifact", required=True, help="ID рассматриваемого артефакта предложения.")
    create.add_argument(
        "--output", required=True, type=Path,
        help="Новый JSON-файл досье; существующий файл не перезаписывается.",
    )

    recheck = commands.add_parser(
        "recheck", help="Повторно оценить досье относительно текущего состояния.",
    )
    recheck.add_argument("--input", required=True, type=Path, help="Сохранённое JSON-досье.")

    for command in (create, recheck):
        command.add_argument(
            "--workspace", required=True, type=Path,
            help=f"Существующий рабочий каталог с {DATABASE_NAME}.",
        )
        command.add_argument(
            "--journal", required=True, type=Path,
            help="Существующий SQLite-файл редакционного журнала.",
        )
        command.add_argument(
            "--current-input", required=True, type=Path,
            help="Текущая JSON-пара before, after, sources; читается один раз.",
        )


def run_dossier_command(args: argparse.Namespace) -> dict[str, Any]:
    """Execute a dossier action and return its JSON-ready record or report."""
    action = args.dossier_command
    if action not in {"create", "recheck"}:
        raise WorkbenchError("unknown_dossier_command", f"Unknown dossier command: {action}")

    # Capture file inputs before opening either database. Store initializes
    # databases, so explicitly refuse a missing workspace database first.
    pair = load_pair_file(args.current_input.expanduser())
    dossier = load_dossier(args.input.expanduser()) if action == "recheck" else None
    database = args.workspace.expanduser() / DATABASE_NAME
    if not database.is_file():
        raise WorkbenchError(
            "missing_workspace", f"Existing workspace database required: {database}",
        )
    journal = EditorialJournal(args.journal.expanduser(), create=False)
    store = Store(database)

    if action == "recheck":
        return recheck_dossier(dossier, store, journal, pair)

    dossier = create_dossier(
        store, journal, args.proposal, args.packet, args.artifact, pair,
    )
    output = args.output.expanduser()
    save_dossier(dossier, output)
    return {
        "path": str(output),
        "dossier_id": dossier["dossier_id"],
        "dossier_digest": dossier["dossier_digest"],
        "observation": dossier["observation"],
    }
