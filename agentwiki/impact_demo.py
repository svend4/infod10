"""A runnable declared-dependency scenario; not a benchmark or test suite."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from .demo import DATABASE_NAME, _platform_document, _proposal, run_demo
from .impact import analyze_impact, dependency_graph
from .releases import compare_releases
from .store import Store, WorkbenchError


def _accept(store: Store, task_id: str, role: str, payload: dict) -> dict:
    store.enqueue(task_id, role, {"proposal": payload})
    worker = "impact-author"
    task = store.claim_task(worker, roles=[role])
    if task is None or task["task_id"] != task_id:
        raise RuntimeError("Impact demo requires an otherwise drained task queue")
    proposal = store.submit(task_id, worker, task["token"], task["payload"]["proposal"])
    review = store.review(proposal["proposal_id"], reviewer="impact-editor")
    result = store.accept(proposal["proposal_id"], coordinator="impact-coordinator")
    if not review["approved"] or not result["accepted"]:
        raise RuntimeError("Impact scenario could not accept its intended change: " + json.dumps(result))
    return {"submitted": proposal, "review": review, "accepted": result}


def run_impact_demo(workspace: str | Path, workers: int = 6) -> dict[str, Any]:
    workspace = Path(workspace).expanduser().resolve()
    if not 1 <= workers <= 32:
        raise ValueError("Workers must be between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Impact demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    first = run_demo(workspace / "base", workers=workers)
    store = Store(workspace / "base" / DATABASE_NAME)
    release_directory = workspace / "release"
    before = store.publish(release_directory)
    base_snapshot = store.snapshot()
    timeout_impact = analyze_impact(base_snapshot, ["api.timeout_seconds"])

    timeout = deepcopy(base_snapshot["artifacts"]["api.timeout_seconds"])
    timeout["value"] = 30
    timeout_change = _accept(store, "impact-timeout", "analyst", _proposal(
        base_snapshot, [timeout], rationale="Use the other timeout value explicitly allowed by the fictional specification."
    ))
    after_timeout = store.publish(release_directory)
    timeout_comparison = compare_releases(before["path"], after_timeout["path"])

    store.add_source("fixture-platform-extension", "FICTIONAL extension to the demo specification.\nmacOS is supported.\n")
    current = store.snapshot()
    mac = {
        "id": "platform.macos", "kind": "claim", "scope": "platforms:api-v1",
        "value": True, "unit": "",
        "evidence": {"source_id": "fixture-platform-extension", "quote": "macOS is supported."},
    }
    platform_change = _accept(store, "impact-macos", "analyst", _proposal(
        current, [mac], rationale="Add a platform; an already accepted full list must be revisited."
    ))
    platform_snapshot = store.snapshot()
    platform_impact = analyze_impact(platform_snapshot, ["platform.macos"])
    try:
        store.publish(release_directory)
    except WorkbenchError as error:
        if error.code != "release_needs_review":
            raise
        blocked_release = {"code": error.code, "reasons": json.loads(str(error)), "revision": platform_snapshot["revision"]}
    else:
        raise RuntimeError("The stale full platform list unexpectedly passed the publication gate")

    refresh = _accept(store, "impact-refresh-platforms", "doc_author", _platform_document(store.snapshot()))
    final = store.publish(release_directory)
    platform_comparison = compare_releases(after_timeout["path"], final["path"])
    final_snapshot = store.snapshot()
    report = {
        "schema_version": 1, "created_at": datetime.now(timezone.utc).isoformat(),
        "workspace": str(workspace), "uses_llm": False, "fictional_sources": True,
        "executes_proposed_code": False,
        "initial_worker_execution": first["execution"],
        "releases": {"before": before, "after_timeout": after_timeout, "after_platform_refresh": final},
        "timeout": {"impact_before_change": timeout_impact, "change": timeout_change, "comparison": timeout_comparison},
        "platform": {"change": platform_change, "impact_before_refresh": platform_impact, "blocked_release": blocked_release, "refresh": refresh, "comparison": platform_comparison},
        "summary": {
            "timeout_candidate_files": timeout_impact["affected_files"],
            "timeout_actual_changed_files": timeout_comparison["changed_rendered_files"],
            "timeout_all_files": timeout_impact["counts"]["total_files"],
            "platform_review_files": platform_impact["review_files"],
            "platform_actual_changed_files": platform_comparison["changed_rendered_files"],
            "publication_was_blocked": True,
            "final_revision": final_snapshot["revision"],
        },
        "limitations": [
            "Only explicitly declared dependencies are analyzed; no NLP inference.",
            "Candidate counts do not measure execution-time or quality improvements.",
            "All invariants still run at acceptance; graph selection does not bypass them.",
            "Bootstrap and producer metadata are trusted local inputs, not authenticated evidence.",
            "This is one functional scenario, not a fault or load verification program.",
        ],
    }
    for name, value in [
        ("report.json", report), ("graph.json", dependency_graph(final_snapshot)),
        ("impact-timeout.json", timeout_impact), ("impact-platform.json", platform_impact),
        ("comparison-timeout.json", timeout_comparison), ("comparison-platform.json", platform_comparison),
    ]:
        (workspace / name).write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Выполненный пример анализа влияния", "",
        "Python workers и вымышленные источники. LLM не вызывались; предложенный код не выполнялся.", "",
        f"Время запуска: {report['created_at']}", "",
        "## Изменение тайм-аута", "",
        "Принято изменение 40 → 30 секунд. Документы и Python-константы получены из одного снимка.", "",
        f"Кандидаты графа: {', '.join(timeout_impact['affected_files'])}.",
        f"Фактически изменились: {', '.join(timeout_comparison['changed_rendered_files'])}.",
        f"Всего файлов документов и кода: {timeout_impact['counts']['total_files']}.", "",
        "Число кандидатов показывает область зависимостей. Оно не является измеренным ускорением; экспорт по-прежнему формирует полный согласованный выпуск.", "",
        "## Новая платформа", "",
        "Принят claim macOS. Ранее принятый полный список Linux/Windows больше не соответствует результату запроса.",
        f"Граф указал повторное ревью: {', '.join(platform_impact['review_files'])}.",
        f"Экспорт остановлен с {blocked_release['code']} на ревизии {blocked_release['revision']}.",
        "После нового чтения списка и принятия исправленного документа экспорт разрешён.",
        f"Фактически изменились: {', '.join(platform_comparison['changed_rendered_files'])}.", "",
        "## Сохранённые выпуски", "",
    ]
    for label, release in report["releases"].items():
        lines.append(f"- {label}: [revision {release['revision']}](release/{release['release_id']}/manifest.json), schema 2.")
    lines.extend([
        "", "## Данные", "",
        "- [Полный отчёт](report.json).",
        "- [Граф зависимостей](graph.json).",
        "- [Влияние тайм-аута](impact-timeout.json).",
        "- [Влияние новой платформы](impact-platform.json).",
        "- [Различия после изменения тайм-аута](comparison-timeout.json).",
        "- [Различия после обновления списка платформ](comparison-platform.json).", "",
        "Исходная рабочая база и повтор первого demo остаются в локальном workspace. В Git сохраняются отчёты и неизменяемые выпуски; абсолютные пути в JSON относятся к этому запуску.",
        "", "Граф ограничен объявленными связями. Полнота смысла свободного текста, достоверность источников, нагрузочные свойства и восстановление после аварий этим запуском не установлены.", "",
    ])
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
