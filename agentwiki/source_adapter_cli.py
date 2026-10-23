"""Read-only commands for a captured, explicitly selected syntax projection.

The input Python files are data. Neither source nor adapter code is imported,
compiled or executed. Saved reports retain their own inputs for offline show.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from .store import WorkbenchError


def register_source_adapter_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "source-adapter", help="Сверить выбранные объявления исходника, адаптера и документа.",
        description=(
            "Извлечь только поддерживаемые AST-формы по явному профилю. Python читается как данные. "
            "Совпадение синтаксических значений не доказывает поведение библиотеки, смысл прозы "
            "или полноту описи и не разрешает принятие либо публикацию."
        ),
    )
    commands = parser.add_subparsers(dest="source_adapter_command", required=True)
    analyze = commands.add_parser("analyze", help="Сохранить полный снимок и сравнение объявленных полей.")
    analyze.add_argument("--source", required=True, type=Path, help="UTF-8 Python исходник; не исполняется.")
    analyze.add_argument("--adapter", required=True, type=Path, help="Авторский Python с прямыми литералами.")
    analyze.add_argument("--document", required=True, type=Path, help="Документ с явно выбранным JSON fence.")
    analyze.add_argument("--profile", required=True, type=Path, help="JSON-профиль из 1..16 явных связей.")
    analyze.add_argument("--output", required=True, type=Path, help="Новый JSON-отчёт; существующий не перезаписывается.")
    show = commands.add_parser("show", help="Пересчитать сохранённый отчёт по его собственному снимку.")
    show.add_argument("--input", required=True, type=Path, help="Сохранённый полный JSON-отчёт.")


def run_source_adapter_command(args: argparse.Namespace) -> dict[str, Any]:
    """Return the same closed summary for analyze and later historical show."""
    action = args.source_adapter_command
    if action not in {"analyze", "show"}:
        raise WorkbenchError("unknown_source_adapter_command", "Unknown source-adapter command")
    try:
        from . import source_adapter

        if action == "show":
            report = source_adapter.load_report(args.input)
        else:
            source = source_adapter.read_text(args.source)
            adapter = source_adapter.read_text(args.adapter)
            document = source_adapter.read_text(args.document)
            profile = source_adapter.load_profile(args.profile)
            report = source_adapter.analyze_source_adapter(source, adapter, document, profile)
            source_adapter.save_report(report, args.output)
        return source_adapter.summary(report)
    except WorkbenchError as error:
        code = error.code if error.code in {"source_adapter_invalid", "source_adapter_io_failed"} else "source_adapter_invalid"
        raise WorkbenchError(code, "Source-adapter command could not complete") from error
    except (OSError, ValueError, TypeError, KeyError, OverflowError, UnicodeError, RecursionError, RuntimeError) as error:
        raise WorkbenchError("source_adapter_io_failed", "Source-adapter command failed") from error
