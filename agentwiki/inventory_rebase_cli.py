"""Read-only planning for explicit promise inventories on new candidates."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import coverage, inventory_rebase, obligations
from .store import WorkbenchError


def register_rebase_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "inventory-rebase", help="Подготовить перенос явной описи на новый сохранённый кандидат.",
        description=("Сравнить прежнюю опись с новым отчётом обязательств и сохранить черновик. "
                     "Все прежние обещания сохраняются; решения проверяющих не переносятся."),
    )
    commands = parser.add_subparsers(dest="inventory_rebase_command", required=True)
    plan = commands.add_parser("plan", help="Сохранить план переноса описи без изменения баз.")
    plan.add_argument("--previous", required=True, type=Path,
                      help="Прежний сохранённый отчёт покрытия обещаний.")
    plan.add_argument("--target", required=True, type=Path,
                      help="Новый сохранённый отчёт межфайловых обязательств.")
    plan.add_argument("--mapping", type=Path,
                      help="Явное сопоставление прежних и новых ID файлов и обязательств.")
    plan.add_argument("--output", required=True, type=Path,
                      help="Новый JSON-файл; существующий не перезаписывается.")
    show = commands.add_parser("show", help="Прочитать план с проверкой сохранённых оснований.")
    show.add_argument("--input", required=True, type=Path)
    export = commands.add_parser("export-draft", help="Сохранить только черновик описи из проверенного плана.")
    export.add_argument("--input", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path,
                        help="Новый файл описи; все прежние записи сохраняются.")


def run_rebase_command(args: argparse.Namespace) -> dict[str, Any]:
    command = args.inventory_rebase_command
    if command == "show":
        return inventory_rebase.load_report(args.input.expanduser())
    if command == "export-draft":
        report = inventory_rebase.load_report(args.input.expanduser())
        inventory = report["observation"]["draft_inventory"]
        output = args.output.expanduser()
        with output.open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
        return {"path": str(output), "source_report_id": report["report_id"],
                "preserved_promise_count": len(inventory["promises"]),
                "requires_human_review": True, "auto_accept": False}
    if command != "plan":
        raise WorkbenchError("unknown_inventory_rebase_command", "Unknown inventory-rebase command")
    previous = coverage.load_report(args.previous.expanduser())
    target = obligations.load_report(args.target.expanduser())
    mapping = inventory_rebase.load_mapping(args.mapping.expanduser()) if args.mapping is not None else None
    report = inventory_rebase.plan_rebase(previous, target, mapping)
    output = args.output.expanduser()
    inventory_rebase.save_report(report, output)
    return {"path": str(output), "report_id": report["report_id"],
            "report_digest": report["report_digest"], "observation": report["observation"]}
