"""Commands for caller-declared promises bound to a captured source report."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .store import WorkbenchError


def register_source_promise_coverage_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "source-promise-coverage", help="Связать объявленные обещания с точным source-adapter отчётом.",
        description=(
            "Проверить цитаты и точные метаданные ссылок новой явной описи. Покрытие и результат "
            "сравнения различаются; все исходные блокеры сохраняются. Цитата вне fenced blocks "
            "не устанавливает смысл прозы, полноту описи или право принятия."
        ),
    )
    commands = parser.add_subparsers(dest="source_promise_coverage_command", required=True)
    analyze = commands.add_parser("analyze", help="Сохранить отдельный отчёт покрытия объявленной описи.")
    analyze.add_argument("--source-report", required=True, type=Path, help="Полный сохранённый source-adapter-1 report.")
    analyze.add_argument("--inventory", required=True, type=Path, help="Новая явная опись с точными ID/digest основания.")
    analyze.add_argument("--output", required=True, type=Path, help="Новый полный JSON-отчёт; существующий не перезаписывается.")
    show = commands.add_parser("show", help="Пересчитать сохранённый отчёт без живых inputs.")
    show.add_argument("--input", required=True, type=Path, help="Сохранённый полный отчёт source-promise-coverage.")


def run_source_promise_coverage_command(args: argparse.Namespace) -> dict[str, Any]:
    """Analyze and historical show return the same closed captured summary."""
    action = args.source_promise_coverage_command
    if action not in {"analyze", "show"}:
        raise WorkbenchError("unknown_source_promise_coverage_command", "Unknown source-promise-coverage command")
    try:
        from . import source_adapter, source_promise_coverage

        if action == "show":
            report = source_promise_coverage.load_report(args.input)
        else:
            parent = source_adapter.load_report(args.source_report)
            inventory = source_promise_coverage.load_inventory(args.inventory)
            report = source_promise_coverage.analyze_source_promise_coverage(parent, inventory)
            source_promise_coverage.save_report(report, args.output)
        return source_promise_coverage.summary(report)
    except WorkbenchError as error:
        code = error.code if error.code in {"source_promise_coverage_invalid", "source_promise_coverage_io_failed"} else "source_promise_coverage_invalid"
        raise WorkbenchError(code, "Source-promise-coverage command could not complete") from error
    except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError, RuntimeError) as error:
        raise WorkbenchError("source_promise_coverage_io_failed", "Source-promise-coverage command failed") from error
