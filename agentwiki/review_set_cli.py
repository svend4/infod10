"""CLI helpers for an advisory review set covering one proposal's writes."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .demo import DATABASE_NAME
from .editorial import EditorialJournal
from .review_set import (
    create_review_set,
    load_bindings,
    load_review_set,
    recheck_review_set,
    save_review_set,
)
from .store import Store, WorkbenchError


def register_review_set_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register creation and reassessment without granting acceptance rights."""
    parser = subparsers.add_parser(
        "review-set",
        help="Сопоставить все записи предложения с редакционными пакетами.",
        description=(
            "Сохранить совещательное рассмотрение нескольких файлов одного предложения. "
            "Покрытие идентификаторов записей не доказывает смысловую полноту. "
            "Команда не принимает изменения и не выполняет предложенный код."
        ),
    )
    commands = parser.add_subparsers(dest="review_set_command", required=True)
    create = commands.add_parser("create", help="Сохранить набор рассмотрения предложения.")
    create.add_argument("--proposal", required=True, help="ID сохранённого предложения Store.")
    create.add_argument(
        "--output", required=True, type=Path,
        help="Новый JSON-файл; существующий файл не перезаписывается.",
    )
    recheck = commands.add_parser("recheck", help="Повторно оценить сохранённый набор.")
    recheck.add_argument("--input", required=True, type=Path, help="Сохранённый набор рассмотрения.")
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
            "--bindings", required=True, type=Path,
            help="JSON-список текущих {artifact_id, packet_id, pair}; читается один раз.",
        )


def run_review_set_command(args: argparse.Namespace) -> dict[str, Any]:
    """Capture explicit inputs before opening the two existing databases."""
    action = args.review_set_command
    if action not in {"create", "recheck"}:
        raise WorkbenchError("unknown_review_set_command", f"Unknown review-set command: {action}")
    bindings = load_bindings(args.bindings.expanduser())
    saved = load_review_set(args.input.expanduser()) if action == "recheck" else None
    database = args.workspace.expanduser() / DATABASE_NAME
    if not database.is_file():
        raise WorkbenchError("missing_workspace", f"Existing workspace database required: {database}")
    journal = EditorialJournal(args.journal.expanduser(), create=False)
    store = Store(database)
    if action == "recheck":
        return recheck_review_set(saved, store, journal, bindings)
    review_set = create_review_set(store, journal, args.proposal, bindings)
    output = args.output.expanduser()
    save_review_set(review_set, output)
    return {
        "path": str(output),
        "review_set_id": review_set["review_set_id"],
        "review_set_digest": review_set["review_set_digest"],
        "observation": review_set["observation"],
    }
