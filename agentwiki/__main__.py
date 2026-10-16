"""Russian-friendly command line for the local agent workbench."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .demo import DATABASE_NAME, run_demo
from .store import Store, WorkbenchError, search_release


def _workers(value: str) -> int:
    try:
        count = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите целое число workers от 1 до 32.") from error
    if not 1 <= count <= 32:
        raise argparse.ArgumentTypeError("Число workers должно быть от 1 до 32.")
    return count


def _existing_store(workspace: Path) -> Store:
    database = workspace.expanduser().resolve() / DATABASE_NAME
    if not database.is_file():
        raise ValueError(f"База не найдена: {database}. Сначала выполните команду demo.")
    return Store(database)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m agentwiki",
        description="Локальная многоагентная работа с документами и кодом: предложения, review и согласованная публикация.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Запустить сценарий на вымышленных источниках без LLM.")
    demo.add_argument("--workspace", required=True, type=Path, help="Новый пустой каталог; существующие данные не удаляются.")
    demo.add_argument("--workers", default=6, type=_workers, help="Размер локального пула, 1–32 (по умолчанию 6).")
    demo.add_argument("--json", action="store_true", help="Вывести полный машинный отчёт.")
    status = commands.add_parser("status", help="Показать состояние существующей базы.")
    status.add_argument("--workspace", required=True, type=Path)
    export = commands.add_parser("export", help="Сохранить согласованный release документов, кода и поиска.")
    export.add_argument("--workspace", required=True, type=Path)
    export.add_argument("--output", required=True, type=Path, help="Каталог публикации.")
    search = commands.add_parser("search", help="Искать текст внутри одного опубликованного release.")
    search.add_argument("--release", required=True, type=Path, help="Каталог release с manifest.json.")
    search.add_argument("--query", required=True, help="Текст поискового запроса.")
    arguments = parser.parse_args(argv)
    try:
        if arguments.command == "demo":
            report = run_demo(arguments.workspace, workers=arguments.workers)
            if arguments.json:
                print(json.dumps(report, ensure_ascii=False, indent=2))
            else:
                workspace = arguments.workspace.expanduser().resolve()
                print("Локальная демонстрация сохранена. Повторный запуск читает существующий отчёт.")
                print(f"Отчёт: {workspace / 'report.md'}")
                print(f"JSON: {workspace / 'report.json'}")
                print(f"Ревизия: {report['final_snapshot']['revision']}")
                print(f"Release: {report['release']['release_id']}")
                print("Workers — локальные Python потоки; LLM не вызывалась, предложенный код не выполнялся.")
        elif arguments.command == "status":
            print(json.dumps(_existing_store(arguments.workspace).status(), ensure_ascii=False, indent=2))
        elif arguments.command == "export":
            release = _existing_store(arguments.workspace).publish(arguments.output.expanduser().resolve())
            print(json.dumps(release, ensure_ascii=False, indent=2))
        else:
            result = search_release(arguments.release.expanduser().resolve(), arguments.query)
            print(json.dumps(result, ensure_ascii=False, indent=2))
    except (WorkbenchError, ValueError, OSError, RuntimeError) as error:
        code = getattr(error, "code", "operation_failed")
        print(f"Ошибка [{code}]: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
