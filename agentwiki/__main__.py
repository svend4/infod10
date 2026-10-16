"""Russian-friendly command line for the local agent workbench."""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
import sys

from .demo import DATABASE_NAME, run_demo
from .store import Store, WorkbenchError, search_release
from .impact import analyze_impact, dependency_graph
from .releases import _compare_loaded_releases, load_release
from .corpus import analyze_case, get_case, load_corpus
from .semantic import analyze_pair_file
from .editorial_cli import register_editorial_parser, run_editorial_command
from .dossier_cli import register_dossier_parser, run_dossier_command
from .review_set_cli import register_review_set_parser, run_review_set_command
from .obligations_cli import register_obligations_parser, run_obligations_command
from .coverage_cli import register_coverage_parser, run_coverage_command


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


def _snapshot(arguments: argparse.Namespace) -> dict:
    if arguments.workspace is not None:
        return _existing_store(arguments.workspace).snapshot()
    return load_release(arguments.release.expanduser())["snapshot"]


def _mermaid(graph: dict) -> str:
    identifiers = {node["id"]: f"n{index}" for index, node in enumerate(graph["nodes"])}
    lines = ["flowchart LR"]
    for node in graph["nodes"]:
        label = html.escape(node["id"], quote=True)
        lines.append(f'    {identifiers[node["id"]]}["{label}"]')
    for edge in graph["edges"]:
        lines.append(f'    {identifiers[edge["source"]]} -->|{edge["kind"]}| {identifiers[edge["target"]]}')
    return "\n".join(lines)


def comparison_with_impact(before: Path, after: Path) -> dict:
    previous, current = load_release(before), load_release(after)
    comparison = _compare_loaded_releases(previous, current)
    old = previous["snapshot"]
    new = current["snapshot"]
    changes = comparison["artifact_changes"]
    modified = changes["modified"]
    dependency_changes = comparison["dependency_changes"]
    source_changes = comparison["source_changes"]
    before_seeds = sorted(
        set(changes["removed"] + modified)
        | {key for key in dependency_changes["removed"] + dependency_changes["modified"] if key in old["artifacts"]}
        | {"source:" + key for key in source_changes["removed"] + source_changes["modified"]}
    )
    after_seeds = sorted(
        set(changes["added"] + modified)
        | {key for key in dependency_changes["added"] + dependency_changes["modified"] if key in new["artifacts"]}
        | {"source:" + key for key in source_changes["added"] + source_changes["modified"]}
    )
    before_impact = analyze_impact(old, before_seeds) if before_seeds else None
    after_impact = analyze_impact(new, after_seeds) if after_seeds else None
    predicted = set()
    for impact in [before_impact, after_impact]:
        if impact:
            predicted.update(impact["affected_files"])
    actual = set(comparison["changed_rendered_files"])
    comparison["impact"] = {
        "before": before_impact, "after": after_impact,
        "predicted_files": sorted(predicted),
        "actual_changed_files": sorted(actual),
        "changed_files_outside_declared_graph": sorted(actual - predicted),
        "predicted_but_byte_unchanged": sorted(predicted - actual),
        "interpretation": "Declared dependency candidates, not measured acceleration or proof of semantic completeness",
    }
    return comparison


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
    for name, description in [("impact", "Показать влияние объявленных изменений."), ("graph", "Показать граф объявленных зависимостей.")]:
        command = commands.add_parser(name, help=description)
        source = command.add_mutually_exclusive_group(required=True)
        source.add_argument("--workspace", type=Path)
        source.add_argument("--release", type=Path)
        if name == "impact":
            command.add_argument("--changed", action="append", required=True, help="ID артефакта или source:/query:/rule: ID; можно повторять.")
        else:
            command.add_argument("--format", choices=["json", "mermaid"], default="json")
    compare = commands.add_parser("compare", help="Сравнить проверенные выпуски и объяснить влияние изменений.")
    compare.add_argument("--before", type=Path, required=True)
    compare.add_argument("--after", type=Path, required=True)
    impact_demo = commands.add_parser("impact-demo", help="Выполнить пример графа влияния и обновления списка платформ.")
    impact_demo.add_argument("--workspace", type=Path, required=True)
    impact_demo.add_argument("--workers", default=6, type=_workers)
    corpus = commands.add_parser("corpus", help="Просматривать авторские примеры смысловых изменений и их разбор.")
    corpus_commands = corpus.add_subparsers(dest="corpus_command", required=True)
    for action in ["list", "show", "analyze"]:
        command = corpus_commands.add_parser(action)
        command.add_argument("--path", type=Path, help="Другой JSONL-корпус; по умолчанию semantic-v1.")
        if action == "list":
            command.add_argument("--category", help="Фильтр по категории.")
            command.add_argument("--with-annotations", action="store_true", help="Показать авторские редакторские пояснения.")
        else:
            command.add_argument("--case", required=True, help="Идентификатор случая.")
            if action == "show":
                command.add_argument("--pair", action="store_true", help="Вывести только before/after/sources для review-pair.")
    review_pair = commands.add_parser("review-pair", help="Объяснить различия пары структурированных утверждений; автоматического принятия нет.")
    review_pair.add_argument("--input", type=Path, required=True, help="JSON с before, after и sources.")
    register_editorial_parser(commands)
    register_dossier_parser(commands)
    register_review_set_parser(commands)
    register_obligations_parser(commands)
    register_coverage_parser(commands)
    editorial_demo = commands.add_parser("editorial-demo", help="Показать историю решений программных проверяющих и устаревание оснований.")
    editorial_demo.add_argument("--workspace", type=Path, required=True)
    editorial_demo.add_argument("--workers", default=3, type=_workers)
    dossier_demo = commands.add_parser("dossier-demo", help="Связать рассмотрение с точными предложениями документов и кода.")
    dossier_demo.add_argument("--workspace", type=Path, required=True)
    dossier_demo.add_argument("--workers", default=3, type=_workers)
    review_set_demo = commands.add_parser("review-set-demo", help="Собрать вклады авторов и рассмотреть все файлы общего предложения.")
    review_set_demo.add_argument("--workspace", type=Path, required=True)
    review_set_demo.add_argument("--workers", default=3, type=_workers)
    obligations_demo = commands.add_parser("obligations-demo", help="Сопоставить явные обязательства документации и конфигурации кода.")
    obligations_demo.add_argument("--workspace", type=Path, required=True)
    obligations_demo.add_argument("--workers", default=3, type=_workers)
    coverage_demo = commands.add_parser("coverage-demo", help="Показать покрытие заявленных обещаний документации обязательствами.")
    coverage_demo.add_argument("--workspace", type=Path, required=True)
    coverage_demo.add_argument("--workers", default=3, type=_workers)
    compose = commands.add_parser("compose", help="Собрать предложение по уже выданной аренде задания integrator.")
    compose.add_argument("--workspace", type=Path, required=True)
    compose.add_argument("--task", required=True)
    compose.add_argument("--worker", required=True)
    compose.add_argument("--token", type=int, required=True)
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
        elif arguments.command == "search":
            result = search_release(arguments.release.expanduser(), arguments.query)
            print(json.dumps(result, ensure_ascii=False, indent=2))
        elif arguments.command == "impact":
            print(json.dumps(analyze_impact(_snapshot(arguments), arguments.changed), ensure_ascii=False, indent=2))
        elif arguments.command == "graph":
            graph = dependency_graph(_snapshot(arguments))
            print(_mermaid(graph) if arguments.format == "mermaid" else json.dumps(graph, ensure_ascii=False, indent=2))
        elif arguments.command == "compare":
            print(json.dumps(comparison_with_impact(arguments.before.expanduser(), arguments.after.expanduser()), ensure_ascii=False, indent=2))
        elif arguments.command == "impact-demo":
            from .impact_demo import run_impact_demo

            report = run_impact_demo(arguments.workspace, arguments.workers)
            print(json.dumps({"report": str(arguments.workspace / "report.md"), "releases": {name: release["release_id"] for name, release in report["releases"].items()}, "summary": report["summary"]}, ensure_ascii=False, indent=2))
        elif arguments.command == "review-pair":
            print(json.dumps(analyze_pair_file(arguments.input.expanduser()), ensure_ascii=False, indent=2))
        elif arguments.command == "editorial":
            print(json.dumps(run_editorial_command(arguments), ensure_ascii=False, indent=2))
        elif arguments.command == "dossier":
            print(json.dumps(run_dossier_command(arguments), ensure_ascii=False, indent=2))
        elif arguments.command == "review-set":
            print(json.dumps(run_review_set_command(arguments), ensure_ascii=False, indent=2))
        elif arguments.command == "obligations":
            print(json.dumps(run_obligations_command(arguments), ensure_ascii=False, indent=2))
        elif arguments.command == "coverage":
            print(json.dumps(run_coverage_command(arguments), ensure_ascii=False, indent=2))
        elif arguments.command == "coverage-demo":
            from .coverage_demo import run_coverage_demo

            report = run_coverage_demo(arguments.workspace, arguments.workers)
            print(json.dumps({"report": str(arguments.workspace / "report.md"), "summary": report["summary"]}, ensure_ascii=False, indent=2))
        elif arguments.command == "obligations-demo":
            from .obligations_demo import run_obligations_demo

            report = run_obligations_demo(arguments.workspace, arguments.workers)
            print(json.dumps({"report": str(arguments.workspace / "report.md"), "summary": report["summary"]}, ensure_ascii=False, indent=2))
        elif arguments.command == "review-set-demo":
            from .review_set_demo import run_review_set_demo

            report = run_review_set_demo(arguments.workspace, arguments.workers)
            print(json.dumps({"report": str(arguments.workspace / "report.md"), "summary": report["summary"]}, ensure_ascii=False, indent=2))
        elif arguments.command == "compose":
            print(json.dumps(_existing_store(arguments.workspace).compose(arguments.task, arguments.worker, arguments.token), ensure_ascii=False, indent=2))
        elif arguments.command == "dossier-demo":
            from .dossier_demo import run_dossier_demo

            report = run_dossier_demo(arguments.workspace, arguments.workers)
            print(json.dumps({"report": str(arguments.workspace / "report.md"), "summary": report["summary"]}, ensure_ascii=False, indent=2))
        elif arguments.command == "editorial-demo":
            from .editorial_demo import run_editorial_demo

            report = run_editorial_demo(arguments.workspace, arguments.workers)
            print(json.dumps({"report": str(arguments.workspace / "report.md"), "summary": report["summary"]}, ensure_ascii=False, indent=2))
        elif arguments.corpus_command == "list":
            catalog = load_corpus(arguments.path)
            if arguments.category is not None and arguments.category not in catalog["categories"]:
                raise WorkbenchError("unknown_category", f"Unknown corpus category: {arguments.category}")
            cases = [case for case in catalog["cases"].values() if arguments.category is None or case["category"] == arguments.category]
            cases = [{key: case[key] for key in ["case_id", "category", "title", "annotation", "limitations"]} for case in cases]
            if not arguments.with_annotations:
                cases = [{key: case[key] for key in ["case_id", "category", "title"]} for case in cases]
            print(json.dumps({"corpus_id": catalog["corpus_id"], "sha256": catalog["sha256"], "total_cases": catalog["case_count"], "selected_cases": len(cases), "categories": catalog["categories"], "annotation_status": "project_authored_illustration", "cases": cases}, ensure_ascii=False, indent=2))
        elif arguments.corpus_command == "show":
            case = get_case(arguments.case, arguments.path)
            value = {key: case[key] for key in ["before", "after", "sources"]} if arguments.pair else case
            print(json.dumps(value, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(analyze_case(arguments.case, arguments.path), ensure_ascii=False, indent=2))
    except (WorkbenchError, ValueError, OSError, RuntimeError) as error:
        code = getattr(error, "code", "operation_failed")
        print(f"Ошибка [{code}]: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
