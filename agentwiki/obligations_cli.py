"""Read-only analysis of declared relations in a saved review set."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .obligations import analyze_obligations, load_report, load_spec, save_report
from .review_set import load_review_set
from .store import WorkbenchError


def register_obligations_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "obligations", help="Сопоставить объявленные значения документа и конфигурации.",
        description=("Читать сохранённый комплект рассмотрения без обращения к БД. "
                     "Предложенный код анализируется как данные и не выполняется. "
                     "Совпадение объявленных значений требует рассмотрения человеком."),
    )
    commands = parser.add_subparsers(dest="obligations_command", required=True)
    analyze = commands.add_parser("analyze", help="Сохранить анализ явно заданных связей.")
    analyze.add_argument("--review-set", required=True, type=Path,
                         help="Сохранённый JSON-комплект рассмотрения.")
    analyze.add_argument("--input", required=True, type=Path,
                         help="JSON-описание обязательств между документом и кодом.")
    analyze.add_argument("--output", required=True, type=Path,
                         help="Новый JSON-файл отчёта; существующий не перезаписывается.")
    show = commands.add_parser("show", help="Прочитать сохранённый отчёт с проверкой целостности.")
    show.add_argument("--input", required=True, type=Path)


def run_obligations_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.obligations_command == "show":
        return load_report(args.input.expanduser())
    if args.obligations_command != "analyze":
        raise WorkbenchError("unknown_obligations_command", "Unknown obligations command")
    review_set = load_review_set(args.review_set.expanduser())
    spec = load_spec(args.input.expanduser())
    report = analyze_obligations(review_set, spec)
    output = args.output.expanduser()
    save_report(report, output)
    return {
        "path": str(output), "report_id": report["report_id"],
        "report_digest": report["report_digest"], "observation": report["observation"],
    }
