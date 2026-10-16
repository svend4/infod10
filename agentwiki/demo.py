"""A local, deterministic multi-worker demonstration with fictional sources.

Workers build proposals. Only the coordinator changes the accepted snapshot.
No model, network call, or execution of proposed code is involved.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
import time
from typing import Any

from .store import Store, WorkbenchError


DATABASE_NAME = "state.sqlite3"
FIXTURE_SOURCE = """FICTIONAL DEMONSTRATION SPECIFICATION. This is not a real API.
The API timeout may be 30 or 40 seconds.
The retry limit may be 3 or 4 attempts.
The total retry budget is 120 seconds.
Linux is supported.
Windows is supported.
The policy is timeout_seconds * max_attempts <= budget_seconds.
"""


def _claim(identifier: str, value: Any, quote: str, *, scope: str = "api:v1",
           unit: str | None = None) -> dict:
    result = {"id": identifier, "kind": "claim", "scope": scope, "value": value,
              "unit": unit if unit is not None else "",
              "evidence": {"source_id": "fixture-spec", "quote": quote}}
    return result


def _document(identifier: str, template: str, bindings: list[str],
              *, kind: str = "document") -> dict:
    return {"id": identifier, "kind": kind, "template": template,
            "bindings": bindings}


def _proposal(snapshot: dict, writes: list[dict], reads: list[str] = (),
              queries: list[str] = (), rationale: str = "") -> dict:
    # Both existing reads and absence of every new write are pinned to one head.
    identifiers = set(reads) | {item["id"] for item in writes}
    return {
        "readset": {key: snapshot["digests"].get(key) for key in sorted(identifiers)},
        "queries": {scope: snapshot.get("queries", {}).get(
            scope, Store.empty_query_digest()) for scope in queries},
        "writes": deepcopy(writes),
        "rationale": rationale,
    }


def _enqueue(store: Store, task_id: str, role: str, proposal: dict) -> None:
    store.enqueue(task_id, role, {"proposal": proposal}, depends_on=[])


def _run_workers(store: Store, count: int, workers: int) -> tuple[dict, dict]:
    """Schedule real threads; this measures local work, not model throughput."""
    results: dict[str, dict] = {}
    lock = threading.Lock()
    gate = threading.Barrier(min(count, workers))
    active = 0
    peak = 0
    thread_names: set[str] = set()
    started = time.monotonic()

    def work(index: int) -> dict:
        nonlocal active, peak
        # The first batch starts together; later jobs never wait for absent peers.
        if index < min(count, workers):
            gate.wait(timeout=30)
        worker = f"worker-{index + 1:02d}"
        with lock:
            active += 1
            peak = max(peak, active)
            thread_names.add(threading.current_thread().name)
        job_start = time.monotonic()
        try:
            task = store.claim_task(worker, lease_seconds=60)
            if task is None:
                raise RuntimeError("Очередь пуста до завершения запланированных заданий.")
            submitted = store.submit(task["task_id"], worker, task["token"],
                                     task["payload"]["proposal"])
            reviewed = store.review(submitted["proposal_id"], reviewer="reviewer")
            return {"task_id": task["task_id"], "worker": worker,
                    "thread": threading.current_thread().name,
                    "proposal_id": submitted["proposal_id"],
                    "proposal_digest": submitted["digest"], "review": reviewed,
                    "elapsed_seconds": round(time.monotonic() - job_start, 6)}
        finally:
            with lock:
                active -= 1

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="agentwiki") as executor:
        futures = [executor.submit(work, index) for index in range(count)]
        for future in as_completed(futures):
            result = future.result()
            results[result["task_id"]] = result
    return results, {
        "scheduled_jobs": count, "requested_workers": workers,
        "observed_threads": sorted(thread_names),
        "observed_peak_jobs_in_worker_section": peak,
        "elapsed_seconds": round(time.monotonic() - started, 6),
        "interpretation": "Локальные потоки и операции SQLite; не измерение LLM или CPU capacity.",
    }


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Демонстрация остановлена: " + message)


def _accept(store: Store, results: dict, task_id: str) -> dict:
    return store.accept(results[task_id]["proposal_id"], coordinator="coordinator")


def _platform_document(snapshot: dict) -> dict:
    platforms = sorted(identifier for identifier, artifact in snapshot["artifacts"].items()
                       if artifact.get("kind") == "claim"
                       and artifact.get("scope") == "platforms:api-v1")
    template = "# Поддерживаемые платформы\n\n" + "\n".join(
        f"- {identifier}: {{{{{identifier}}}}}" for identifier in platforms) + "\n"
    return _proposal(snapshot, [_document("docs/platforms.md", template, platforms)],
                     reads=platforms, queries=["platforms:api-v1"],
                     rationale="Полный список платформ обязан учитывать новые элементы запроса.")


def _write_report(workspace: Path, report: dict) -> None:
    (workspace / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    execution = report["execution"]
    lines = [
        "# Выполненная локальная демонстрация", "",
        "Источники и API вымышлены. Здесь работали Python workers без LLM и сетевых запросов.",
        "Код из предложений сохранён как текст и не выполнялся.", "",
        f"- Создано: {report['created_at']}",
        f"- Запрошено workers: {execution['requested_workers']}",
        f"- Начальных заданий: {execution['scheduled_jobs']}",
        f"- Наблюдавшийся максимум jobs в worker section: {execution['observed_peak_jobs_in_worker_section']}",
        f"- Использованные потоки: {', '.join(execution['observed_threads'])}",
        f"- Итоговая ревизия: {report['final_snapshot']['revision']}",
        f"- Release: {report['release']['release_id']}", "",
        "Число workers задаёт размер локального пула. Наблюдаемая конкурентность не равна числу одновременно занятых CPU и не определяет вместимость сервиса.",
        "", "## Порядок координации", "",
    ]
    for index, item in enumerate(report["sequence"], 1):
        lines.append(f"{index}. {item['description']}")
        lines.append("   ~~~json")
        lines.extend("   " + line for line in json.dumps(
            item["result"], ensure_ascii=False, indent=2).splitlines())
        lines.append("   ~~~")
    lines.extend([
        "", "## Семантическое ограничение", "",
        "Начало: `30 × 3 = 90 ≤ 120`. Каждое предложение отдельно допустимо:",
        "`40 × 3 = 120`, `30 × 4 = 120`. Совместный результат `40 × 4 = 160 > 120` отклонён при принятии.",
        "Проверка цитаты показывает наличие текста в вымышленном источнике; она не доказывает истинность произвольного утверждения.",
        "", "## Файлы", "",
        "`report.json` хранит результаты методов хранилища, идентификаторы предложений и задания.",
        "Каталог `release/` содержит согласованный экспорт документов, кода, claims и поиска одной ревизии.",
        "События и состояния задач также записаны в SQLite. Это локальная демонстрация, не распределённый кластер.", "",
    ])
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")


def run_demo(workspace: Path | str, workers: int = 6) -> dict:
    """Build a reproducible scenario, preserving every pre-existing workspace."""
    workspace = Path(workspace).expanduser().resolve()
    if not 1 <= workers <= 32:
        raise ValueError("Число workers должно быть от 1 до 32.")
    previous_report = workspace / "report.json"
    if previous_report.is_file():
        # A repeated invocation is a read: no tasks, sources or releases are replaced.
        report = json.loads(previous_report.read_text(encoding="utf-8"))
        if report.get("demo_schema_version") != 1 or not (workspace / DATABASE_NAME).is_file():
            raise ValueError("Каталог содержит неподходящий отчёт. Укажите новый пустой --workspace.")
        return report
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Демонстрация требует пустой --workspace. Существующие данные сохранены; выберите новый каталог.")
    workspace.mkdir(parents=True, exist_ok=True)
    store = Store(workspace / DATABASE_NAME)
    store.add_source("fixture-spec", FIXTURE_SOURCE)
    store.bootstrap([
        _claim("api.timeout_seconds", 30, "The API timeout may be 30 or 40 seconds.", unit="s"),
        _claim("api.max_attempts", 3, "The retry limit may be 3 or 4 attempts.", unit="count"),
        _claim("api.budget_seconds", 120, "The total retry budget is 120 seconds.", unit="s"),
        _claim("platform.linux", True, "Linux is supported.", scope="platforms:api-v1"),
    ])
    initial = store.snapshot()
    definitions = [
        ("initial-reference", "doc_author", _document("docs/reference.md",
            "# API\n\nTimeout: {{api.timeout_seconds}} s. Attempts: {{api.max_attempts}}. Budget: {{api.budget_seconds}} s.\n",
            ["api.timeout_seconds", "api.max_attempts", "api.budget_seconds"])),
        ("initial-code", "code_author", _document("code/client_config.py",
            '"""Generated configuration; no network client is executed."""\nTIMEOUT_SECONDS = {{api.timeout_seconds}}\nMAX_ATTEMPTS = {{api.max_attempts}}\nBUDGET_SECONDS = {{api.budget_seconds}}\n',
            ["api.timeout_seconds", "api.max_attempts", "api.budget_seconds"], kind="code")),
        ("initial-retries", "doc_author", _document("docs/retries.md",
            "# Повторные попытки\n\nДо {{api.max_attempts}} попыток по {{api.timeout_seconds}} секунд; бюджет {{api.budget_seconds}} секунд.\n",
            ["api.max_attempts", "api.timeout_seconds", "api.budget_seconds"])),
        ("initial-linux", "doc_author", _document("docs/linux.md",
            "# Linux\n\nПоддержка Linux: {{platform.linux}}.\n", ["platform.linux"])),
    ]
    for task_id, role, document in definitions:
        _enqueue(store, task_id, role, _proposal(initial, [document], reads=document["bindings"],
            rationale="Независимый документ или код из общей исходной ревизии."))
    _enqueue(store, "initial-platform-list", "doc_author", _platform_document(initial))
    _enqueue(store, "initial-windows", "analyst", _proposal(initial, [
        _claim("platform.windows", True, "Windows is supported.", scope="platforms:api-v1")],
        rationale="Добавить платформу, присутствующую в вымышленном источнике."))
    parallel, execution = _run_workers(store, 6, workers)
    _require(all(item["review"]["approved"] for item in parallel.values()),
             "одно из исходных предложений не прошло review.")
    sequence: list[dict] = []

    def record(description: str, result: dict) -> dict:
        sequence.append({"description": description, "result": result})
        return result

    windows = record("Сначала принято добавление Windows.", _accept(store, parallel, "initial-windows"))
    _require(windows["accepted"], "добавление Windows отклонено.")
    phantom = record("Список только Linux отклонён: изменилось множество результатов query.",
                     _accept(store, parallel, "initial-platform-list"))
    _require(not phantom["accepted"], "устаревший список платформ был принят.")
    _require(any(item.get("code") == "query_changed" for item in phantom["reasons"]),
             "список платформ должен быть отклонён из-за изменения query.")
    for task_id, _, _ in definitions:
        result = record(f"Принято независимое предложение {task_id}.", _accept(store, parallel, task_id))
        _require(result["accepted"], f"независимое предложение {task_id} отклонено.")
    _enqueue(store, "retry-platform-list", "doc_author", _platform_document(store.snapshot()))
    retry, _ = _run_workers(store, 1, workers)
    retried = record("Новый список Linux и Windows принят после чтения свежей ревизии.",
                     _accept(store, retry, "retry-platform-list"))
    _require(retried["accepted"], "обновлённый список платформ отклонён.")

    policy_head = store.snapshot()
    _enqueue(store, "change-timeout", "analyst", _proposal(policy_head, [
        _claim("api.timeout_seconds", 40, "The API timeout may be 30 or 40 seconds.", unit="s")],
        rationale="40 × 3 = 120: отдельное допустимое изменение."))
    _enqueue(store, "change-attempts", "analyst", _proposal(policy_head, [
        _claim("api.max_attempts", 4, "The retry limit may be 3 or 4 attempts.", unit="count")],
        rationale="30 × 4 = 120: отдельное допустимое изменение."))
    _enqueue(store, "stale-document", "doc_author", _proposal(policy_head, [
        _document("docs/stale-observation.md", "# Наблюдение\n\nTimeout: {{api.timeout_seconds}} s.\n", ["api.timeout_seconds"])],
        reads=["api.timeout_seconds"], rationale="Это предложение прочитало timeout до изменения."))
    policy, policy_execution = _run_workers(store, 3, workers)
    _require(all(item["review"]["approved"] for item in policy.values()),
             "оба изменения должны пройти review до принятия первого.")
    changed = record("Принят timeout 40: итоговый бюджет 40 × 3 = 120.",
                     _accept(store, policy, "change-timeout"))
    _require(changed["accepted"], "допустимое изменение timeout отклонено.")
    rejected_budget = record("Отклонены 4 попытки: совместный бюджет 40 × 4 = 160 превышает 120.",
                             _accept(store, policy, "change-attempts"))
    _require(not rejected_budget["accepted"], "нарушение бюджета было принято.")
    _require(any(item.get("code") == "invariant_violation" for item in rejected_budget["reasons"]),
             "изменение числа попыток должно нарушать invariant, а не конфликтовать с readset.")
    stale = record("Отклонён документ с устаревшим readset для timeout 30.",
                   _accept(store, policy, "stale-document"))
    _require(not stale["accepted"], "устаревшее чтение было принято.")
    _require(any(item.get("code") == "stale_read" for item in stale["reasons"]),
             "устаревший документ должен быть отклонён по readset.")

    lease_head = store.snapshot()
    lease_proposal = _proposal(lease_head, [
        _document("docs/lease-recovery.md", "# Возврат задания\n\nБюджет: {{api.budget_seconds}} s.\n", ["api.budget_seconds"])],
        reads=["api.budget_seconds"], rationale="Повторный захват истёкшего задания новым worker.")
    _enqueue(store, "lease-recovery", "doc_author", lease_proposal)
    old_lease = store.claim_task("expired-worker", roles=["doc_author"], lease_seconds=0)
    new_lease = store.claim_task("replacement-worker", roles=["doc_author"], lease_seconds=60)
    _require(old_lease is not None and new_lease is not None,
             "задание не было передано новому worker после истечения lease.")
    _require(old_lease["task_id"] == new_lease["task_id"] == "lease-recovery",
             "восстановлено неожиданное задание.")
    try:
        store.submit("lease-recovery", "expired-worker", old_lease["token"], lease_proposal)
    except WorkbenchError as error:
        _require(error.code == "lease_lost", "ожидался fencing отказ lease_lost.")
        record("Старый worker не смог отправить результат с истёкшим token.",
               {"error_code": error.code, "message": str(error)})
    else:
        raise RuntimeError("Истёкший lease позволил отправить результат.")
    submitted = store.submit("lease-recovery", "replacement-worker", new_lease["token"], lease_proposal)
    lease_review = store.review(submitted["proposal_id"], reviewer="reviewer")
    _require(lease_review["approved"], "восстановленное предложение не прошло review.")
    recovered = record("Результат нового worker принят.", store.accept(submitted["proposal_id"]))
    _require(recovered["accepted"], "восстановленное предложение отклонено.")
    replay = record("Повторное принятие того же proposal вернуло прежний результат без новой ревизии.",
                    store.accept(submitted["proposal_id"]))
    _require(replay["accepted"] and replay["replayed"]
             and replay["revision"] == recovered["revision"], "повторное принятие изменило ревизию.")

    release = store.publish(workspace / "release")
    record("Опубликован согласованный локальный release одной ревизии.", release)
    final = store.snapshot()
    report = {
        "demo_schema_version": 1,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "fictional_sources": True, "uses_llm": False, "executes_proposed_code": False,
        "workspace": str(workspace), "database": DATABASE_NAME,
        "execution": execution, "policy_execution": policy_execution,
        "initial_snapshot": {"revision": initial["revision"], "digests": initial["digests"]},
        "initial_workers": parallel, "retry_workers": retry, "policy_workers": policy,
        "sequence": sequence,
        "policy_examples": {"initial": "30 * 3 = 90 <= 120", "timeout_alone": "40 * 3 = 120 <= 120",
                            "attempts_alone": "30 * 4 = 120 <= 120", "combined_rejected": "40 * 4 = 160 > 120"},
        "final_snapshot": {"revision": final["revision"], "digests": final["digests"]},
        "counts": {
            "submitted_proposals": len(parallel) + len(retry) + len(policy) + 1,
            "accepted_proposals": sum(item["result"].get("accepted") is True
                                      and not item["result"].get("replayed", False)
                                      for item in sequence),
            "blocked_accepts": sum(item["result"].get("accepted") is False for item in sequence),
            "idempotent_accept_replays": sum(item["result"].get("replayed") is True for item in sequence),
            "fenced_submissions": sum(item["result"].get("error_code") == "lease_lost" for item in sequence),
        },
        "status": store.status(), "tasks": store.tasks(), "events": store.events(),
        "release": release,
    }
    _write_report(workspace, report)
    return report
