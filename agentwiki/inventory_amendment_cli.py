"""Commands for explicitly authored changes to a preserved promise inventory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from . import coverage, inventory_amendment, inventory_rebase
from .store import WorkbenchError


def register_amendment_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "inventory-amendment", help="Явно пересмотреть описания обещаний из плана переноса.",
        description=("Применить авторские правки к сохранённому плану переноса. "
                     "Сохраняются прежние ID, порядок и число записей; рассмотрение человеком остаётся обязательным."),
    )
    commands = parser.add_subparsers(dest="inventory_amendment_command", required=True)
    plan = commands.add_parser("plan", help="Сохранить результат явно заданных авторских правок.")
    plan.add_argument("--rebase", required=True, type=Path, help="Сохранённый план переноса описи.")
    plan.add_argument("--changes", required=True, type=Path, help="План авторских действий с точными ID и digest основания.")
    plan.add_argument("--output", required=True, type=Path, help="Новый JSON-файл; существующий не перезаписывается.")
    show = commands.add_parser("show", help="Проверить отчёт и показать сводку авторских изменений.")
    show.add_argument("--input", required=True, type=Path)
    for command, description in [
        ("export-inventory", "Сохранить итоговую опись с прежними ID и порядком записей."),
        ("export-coverage", "Сохранить обычный отчёт покрытия итоговой описи."),
    ]:
        export = commands.add_parser(command, help=description)
        export.add_argument("--input", required=True, type=Path)
        export.add_argument("--output", required=True, type=Path, help="Новый файл; существующий не перезаписывается.")


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    observation = report["observation"]
    return {
        "report_id": report["report_id"], "report_digest": report["report_digest"],
        "state": observation["state"], "counts": observation["counts"],
        "results": observation["results"], "blockers": observation["blockers"],
        "preserved_promise_count": len(observation["final_inventory"]["promises"]),
        "requires_human_review": True, "auto_accept": False, "live_state_checked": False,
    }


def run_amendment_command(args: argparse.Namespace) -> dict[str, Any]:
    command = args.inventory_amendment_command
    if command == "plan":
        rebase = inventory_rebase.load_report(args.rebase.expanduser())
        changes = inventory_amendment.load_plan(args.changes.expanduser())
        report = inventory_amendment.apply_amendments(rebase, changes)
        output = args.output.expanduser()
        inventory_amendment.save_report(report, output)
        return {"path": str(output), **_summary(report)}
    if command not in {"show", "export-inventory", "export-coverage"}:
        raise WorkbenchError("unknown_inventory_amendment_command", "Unknown inventory-amendment command")
    report = inventory_amendment.load_report(args.input.expanduser())
    if command == "show":
        return _summary(report)
    output = args.output.expanduser()
    if command == "export-coverage":
        exported = inventory_amendment.export_coverage(report)
        coverage.save_report(exported, output)
        return {
            "path": str(output), "source_report_id": report["report_id"],
            "source_report_digest": report["report_digest"],
            "amendment_report_id": report["report_id"],
            "amendment_report_digest": report["report_digest"],
            "lineage_in_export": False,
            "coverage_report_id": exported["report_id"],
            "coverage_report_digest": exported["report_digest"],
            "state": exported["observation"]["state"],
            "requires_human_review": True, "auto_accept": False, "live_state_checked": False,
        }
    inventory = report["observation"]["final_inventory"]
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(inventory, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
    return {
        "path": str(output), "source_report_id": report["report_id"],
        "source_report_digest": report["report_digest"],
        "preserved_promise_count": len(inventory["promises"]),
        "requires_human_review": True, "auto_accept": False, "live_state_checked": False,
    }
