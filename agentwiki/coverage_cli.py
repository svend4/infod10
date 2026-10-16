"""Read-only inventory coverage over a saved obligation report."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import obligations
from .coverage import analyze_coverage, load_inventory, load_report, save_report
from .store import WorkbenchError


def register_coverage_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "coverage", help="Показать покрытие явно перечисленных обещаний документа.",
        description=("Сопоставить ручную опись обещаний с объявленными обязательствами "
                     "в сохранённом отчёте. Полнота описи и истинность прозы не устанавливаются."),
    )
    commands = parser.add_subparsers(dest="coverage_command", required=True)
    analyze = commands.add_parser("analyze", help="Сохранить анализ покрытия описи обещаний.")
    analyze.add_argument("--obligations", required=True, type=Path,
                         help="Сохранённый отчёт межфайловых обязательств.")
    analyze.add_argument("--input", required=True, type=Path,
                         help="JSON-опись обещаний с точными цитатами и выбранными полями.")
    analyze.add_argument("--output", required=True, type=Path,
                         help="Новый JSON-файл отчёта; существующий не перезаписывается.")
    show = commands.add_parser("show", help="Прочитать отчёт с проверкой внутренней согласованности.")
    show.add_argument("--input", required=True, type=Path)


def run_coverage_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.coverage_command == "show":
        return load_report(args.input.expanduser())
    if args.coverage_command != "analyze":
        raise WorkbenchError("unknown_coverage_command", "Unknown coverage command")
    source = obligations.load_report(args.obligations.expanduser())
    inventory = load_inventory(args.input.expanduser())
    report = analyze_coverage(source, inventory)
    output = args.output.expanduser()
    save_report(report, output)
    return {"path": str(output), "report_id": report["report_id"],
            "report_digest": report["report_digest"], "observation": report["observation"]}
