"""Read-only commands for declared requirement changes in an amendment."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from . import inventory_amendment, requirement_delta
from .store import WorkbenchError


def register_delta_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "requirement-delta", help="Показать изменения явно заданных требований в пересмотре описи.",
        description=("Сравнить прежние и пересмотренные описания, выбранные значения и основания. "
                     "Смысл прозы и поведение программы не устанавливаются; блокировки сохраняются."),
    )
    commands = parser.add_subparsers(dest="requirement_delta_command", required=True)
    analyze = commands.add_parser("analyze", help="Сохранить анализ одного зафиксированного авторского пересмотра.")
    analyze.add_argument("--amendment", required=True, type=Path, help="Сохранённый отчёт amendment-1.")
    analyze.add_argument("--output", required=True, type=Path, help="Новый JSON-файл; существующий не перезаписывается.")
    show = commands.add_parser("show", help="Проверить сохранённый анализ и показать его сводку.")
    show.add_argument("--input", required=True, type=Path)


def _summary(report: dict[str, Any]) -> dict[str, Any]:
    observed = report["observation"]
    return {
        "report_id": report["report_id"], "report_digest": report["report_digest"],
        "source_amendment_id": observed["source_amendment_id"],
        "source_amendment_digest": observed["source_amendment_digest"],
        "state": observed["state"], "attention_state": observed["attention_state"],
        "counts": observed["counts"],
        "results": [{
            "id": item["id"], "action": item["action"],
            "amendment_outcome": item["amendment_outcome"],
            "requirement_change": item["requirement_change"],
            "document_value_change": item["document"]["selected_value_change"],
            "code_value_change": item["code"]["selected_value_change"],
            "attention_reasons": item["attention_reasons"],
        } for item in observed["results"]],
        "review_attention": observed["review_attention"], "blockers": observed["blockers"],
        "requires_human_review": True, "auto_accept": False,
        "live_state_checked": False, "atomic_capture": False,
    }


def run_delta_command(args: argparse.Namespace) -> dict[str, Any]:
    command = args.requirement_delta_command
    if command == "analyze":
        amendment = inventory_amendment.load_report(args.amendment.expanduser())
        report = requirement_delta.inspect_amendment(amendment)
        output = args.output.expanduser()
        requirement_delta.save_report(report, output)
        return {"path": str(output), **_summary(report)}
    if command == "show":
        return _summary(requirement_delta.load_report(args.input.expanduser()))
    raise WorkbenchError("unknown_requirement_delta_command", "Unknown requirement-delta command")
