"""Programmed authors compose document/code changes and expose review coverage.

Composition produces a new proposal; component reviews never become its review.
Review sets remain advisory observations. Only explicit background fixture
updates are accepted. Proposed Python text is never executed.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
from pathlib import Path
from threading import Barrier, current_thread
from typing import Any
from zoneinfo import ZoneInfo

from .dossier_demo import FIXTURE_SOURCE, _claim, _file, _pair, _proposal
from .editorial import EditorialJournal
from .review_set import create_review_set, recheck_review_set, save_review_set
from .store import Store


DATABASE_NAME = "state.sqlite3"
JOURNAL_DATABASE_NAME = "editorial.sqlite3"


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete product scenario before writing a success report."""
    if not condition:
        raise RuntimeError("Review-set demonstration stopped: " + message)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _codes(value: Any) -> set[str]:
    if isinstance(value, dict):
        own = {value["code"]} if isinstance(value.get("code"), str) else set()
        return own.union(*(_codes(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_codes(item) for item in value))
    return set()


def run_review_set_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save one bounded cooperative workflow in a new empty directory."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Review-set demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    store = Store(workspace / DATABASE_NAME)
    journal = EditorialJournal(workspace / JOURNAL_DATABASE_NAME)
    store.add_source("dossier-fixture", FIXTURE_SOURCE)
    before_files = {
        "document": _file("docs/reference.md", "document",
                          "# API\n\nТайм-аут: {{api.timeout_seconds}} секунд.\n"),
        "code": _file("code/client_config.py", "code",
                      '"""Fictional API configuration."""\n'
                      "TIMEOUT_SECONDS = {{api.timeout_seconds}}\n"),
    }
    after_files = {
        "document": _file("docs/reference.md", "document",
                          "# Настройка API\n\nТайм-аут запроса: {{api.timeout_seconds}} секунд.\n"),
        "code": _file("code/client_config.py", "code",
                      '"""Fictional API configuration; editorial review remains required."""\n'
                      "TIMEOUT_SECONDS = {{api.timeout_seconds}}\n"),
    }
    store.bootstrap([
        _claim("api.timeout_seconds", 30, "s", "The API timeout may be 30 or 40 seconds."),
        _claim("api.max_attempts", 3, "count", "The retry limit is 3 attempts."),
        _claim("api.budget_seconds", 120, "s", "The total retry budget is 120 seconds."),
        *before_files.values(),
    ])
    initial_snapshot = store.snapshot()
    roles = {"document": "doc_author", "code": "code_author"}
    for label, body in after_files.items():
        store.enqueue("review-set-" + label, roles[label], {
            "proposal": _proposal(initial_snapshot, body, "Revise one existing file for composition.")
        })
    authors_ready = Barrier(min(workers, len(after_files)))

    def author_job(label: str) -> dict[str, Any]:
        authors_ready.wait(timeout=30)
        author = "review-set-" + label + "-author"
        task = store.claim_task(author, roles=[roles[label]], lease_seconds=60)
        _require(task is not None, "the author's queued task was unavailable")
        submitted = store.submit(task["task_id"], author, task["token"], task["payload"]["proposal"])
        return {"label": label, "author": author, "thread": current_thread().name,
                "task_id": task["task_id"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="review-set-author") as executor:
        futures = {label: executor.submit(author_job, label) for label in after_files}
        authors = {label: future.result() for label, future in futures.items()}
    component_reviews = {
        label: store.review(item["proposal_id"], reviewer="component-formal-agent")
        for label, item in authors.items()
    }
    _require(all(item["approved"] for item in component_reviews.values()),
             "a component's formal review failed")
    components = [{"proposal_id": item["proposal_id"], "digest": item["digest"]}
                  for item in authors.values()]
    store.enqueue("review-set-compose", "integrator", {
        "components": components,
        "rationale": "Compose the declared independent document and code changes as one new proposal.",
    })
    integrator = "review-set-integrator"
    compose_task = store.claim_task(integrator, roles=["integrator"], lease_seconds=60)
    _require(compose_task is not None, "the composition task was unavailable")
    composition = store.compose(compose_task["task_id"], integrator, compose_task["token"])
    composite_id = composition["proposal_id"]
    _require(composite_id not in {item["proposal_id"] for item in authors.values()},
             "composition did not produce a distinct proposal")
    packets = {}
    pairs = {}
    bindings = []
    for label, body in after_files.items():
        # Reused fictional quantity metadata is explicitly authored. It is not
        # extracted from templates and does not assess whole-file equivalence.
        pairs[label] = _pair(before_files[label]["template"], body["template"])
        packets[label] = journal.create_packet(
            pairs[label], proposer=authors[label]["author"], proposer_kind="agent")
        bindings.append({"artifact_id": body["id"], "packet_id": packets[label]["packet_id"],
                         "pair": pairs[label]})
        _write_json(workspace / (label + "-pair.json"), pairs[label])
    _write_json(workspace / "bindings.json", bindings)
    partial_bindings = [item for item in bindings if item["artifact_id"] == after_files["document"]["id"]]
    _write_json(workspace / "partial-bindings.json", partial_bindings)
    review_jobs = [(label, reviewer) for label in after_files
                   for reviewer in ("editorial-agent-a", "editorial-agent-b")]
    reviewers_ready = Barrier(min(workers, len(review_jobs)))

    def reviewer_job(index: int, label: str, reviewer: str) -> dict[str, Any]:
        if index < min(workers, len(review_jobs)):
            reviewers_ready.wait(timeout=30)
        packet = packets[label]
        decision = journal.record_decision(
            packet["packet_id"], reviewer=reviewer, actor_kind="agent", verdict="approve",
            rationale=("The authored quantity metadata compares 30 seconds with 30000 milliseconds. "
                       "Exact template attachment is checked separately. This programmed approval "
                       "does not assess all wording, metadata or program behavior."),
            operation_id=label + "-" + reviewer + "-1",
            expected_packet_digest=packet["packet_digest"], expected_previous_decision=None,
        )
        return {"label": label, "reviewer": reviewer, "thread": current_thread().name,
                "decision": decision}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="review-set-review") as executor:
        futures = [executor.submit(reviewer_job, index, label, reviewer)
                   for index, (label, reviewer) in enumerate(review_jobs)]
        editorial_reviews = [future.result() for future in futures]

    before_formal = create_review_set(store, journal, composite_id, bindings)
    save_review_set(before_formal, workspace / "before-formal-review-set.json")
    _require(before_formal["observation"]["state"] == "blocked"
             and "unapproved" in _codes(before_formal["observation"]),
             "component approvals were incorrectly transferred to the composite")
    composite_review = store.review(composite_id, reviewer="composite-formal-agent")
    _require(composite_review["approved"], "the new composite's own formal review failed")
    partial = create_review_set(store, journal, composite_id, partial_bindings)
    complete = create_review_set(store, journal, composite_id, bindings)
    save_review_set(partial, workspace / "partial-review-set.json")
    save_review_set(complete, workspace / "complete-review-set.json")
    _require(partial["observation"]["state"] == "blocked"
             and "uncovered_writes" in _codes(partial["observation"]),
             "partial coverage did not expose the unreviewed code write")
    _require(complete["observation"]["state"] == "current_for_human_review",
             "the fully attached observation was not current for human review")

    def accept_background(task_id: str, body: dict[str, Any], rationale: str) -> dict[str, Any]:
        store.enqueue(task_id, "analyst", {"proposal": _proposal(store.snapshot(), body, rationale)})
        task = store.claim_task("background-analyst", roles=["analyst"], lease_seconds=60)
        _require(task is not None, "the background fixture task was unavailable")
        submitted = store.submit(task["task_id"], "background-analyst", task["token"],
                                 task["payload"]["proposal"])
        review = store.review(submitted["proposal_id"], reviewer="background-formal-agent")
        accepted = store.accept(submitted["proposal_id"], coordinator="background-coordinator")
        _require(review["approved"] and accepted["accepted"], "a background fixture update failed")
        return {"task_id": task_id, "submitted": submitted, "review": review, "acceptance": accepted}

    unrelated_change = accept_background(
        "review-set-windows", _claim("platform.windows", True, "", "Windows is supported.",
                                     scope="platforms:api-v1"),
        "Update an unrelated platform claim without modifying the composite's declared basis.")
    after_unrelated = recheck_review_set(complete, store, journal, bindings)
    _require(after_unrelated["state"] == "current_for_human_review",
             "an unrelated revision incorrectly invalidated the review set")
    earlier = next(item["decision"] for item in editorial_reviews
                   if item["label"] == "code" and item["reviewer"] == "editorial-agent-a")
    objection = journal.record_decision(
        packets["code"]["packet_id"], reviewer="editorial-agent-a", actor_kind="agent",
        verdict="request_changes",
        rationale=("The authored quantity comparison does not assess complete code behavior. "
                   "A human reviewer needs to inspect the full proposed file and its assumptions."),
        operation_id="code-editorial-agent-a-2",
        expected_packet_digest=packets["code"]["packet_digest"],
        expected_previous_decision=earlier["decision_id"],
    )
    after_objection = recheck_review_set(complete, store, journal, bindings)
    _require(after_objection["state"] == "blocked", "the code objection did not block the observation")
    entries = {item["artifact_id"]: item for item in after_objection["current_observation"]["entries"]}
    # The underlying single-file observation still reports its own local
    # uncovered_writes. The review set handles combined write coverage above;
    # inspect the remaining reasons to localize the packet-specific objection.
    code_reasons = _codes(entries[after_files["code"]["id"]]["blockers"]) - {"uncovered_writes"}
    document_reasons = _codes(entries[after_files["document"]["id"]]["blockers"]) - {"uncovered_writes"}
    _require(bool(code_reasons) and not document_reasons,
             "the packet-specific objection was not localized to the code entry")
    dependency_change = accept_background(
        "review-set-timeout", _claim("api.timeout_seconds", 40, "s",
                                     "The API timeout may be 30 or 40 seconds."),
        "Update the shared timeout dependency; 40 times 3 still fits the 120-second budget.")
    after_dependency = recheck_review_set(complete, store, journal, bindings)
    _require("stale_read" in _codes(after_dependency["current_observation"]),
             "the changed shared runtime dependency was not visible")
    for entry in after_dependency["current_observation"]["entries"]:
        _require("stale_read" in _codes(entry["runtime_errors"]),
                 "a file entry lost the composite's stale runtime basis")
    replay = store.compose(compose_task["task_id"], integrator, compose_task["token"])
    _require(replay["replayed"] and replay["proposal_id"] == composite_id,
             "completed composition did not replay the original saved proposal")
    final_proposals = {
        **{label: store.proposal(item["proposal_id"]) for label, item in authors.items()},
        "composite": store.proposal(composite_id),
    }
    _require(all(item["status"] == "proposed" for item in final_proposals.values()),
             "an advisory operation accepted a file proposal")
    rechecks = {"after-unrelated": after_unrelated, "after-objection": after_objection,
                "after-dependency": after_dependency}
    for name, value in rechecks.items():
        _write_json(workspace / ("recheck-" + name + ".json"), value)
    snapshot = journal.export_snapshot()
    _write_json(workspace / "journal.json", snapshot)
    _write_json(workspace / "composition.json", composition)
    _write_json(workspace / "composition-replay.json", replay)
    _write_json(workspace / "source-proposals.json", final_proposals)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin", "workspace": str(workspace),
        "uses_llm": False, "fictional_sources": True, "executes_proposed_code": False,
        "requires_human_review": True, "human_review_recorded": False, "auto_accept": False,
        "calls_store_accept": True,
        "store_accept_scope": "Only unrelated Windows and shared-timeout background fixture claims.",
        "accepts_document_or_code_proposals": False, "publishes_release": False,
        "execution": {
            "requested_pool_workers": workers, "submitted_author_jobs": len(authors),
            "submitted_editorial_review_jobs": len(editorial_reviews),
            "observed_author_threads": sorted({item["thread"] for item in authors.values()}),
            "observed_editorial_threads": sorted({item["thread"] for item in editorial_reviews}),
            "author_ids": [item["author"] for item in authors.values()],
            "integrator_id": integrator,
            "editorial_reviewer_ids": sorted({item["reviewer"] for item in editorial_reviews}),
            "component_formal_reviewer_id": "component-formal-agent",
            "composite_formal_reviewer_id": "composite-formal-agent", "all_actor_kinds": ["agent"],
        },
        "initial_runtime_revision": initial_snapshot["revision"], "authors": authors,
        "component_reviews": component_reviews, "composition": composition,
        "composite_review": composite_review, "packets": packets, "editorial_reviews": editorial_reviews,
        "initial_review_sets": {
            label: {"review_set_id": item["review_set_id"],
                    "review_set_digest": item["review_set_digest"], "observation": item["observation"]}
            for label, item in (("before_formal", before_formal), ("partial", partial), ("complete", complete))
        },
        "unrelated_change": unrelated_change, "objection": objection,
        "dependency_change": dependency_change, "rechecks": rechecks,
        "composition_replay": replay, "final_runtime_status": store.status(),
        "final_proposal_statuses": {label: item["status"] for label, item in final_proposals.items()},
        "summary": {
            "source_proposal_count": len(authors), "composite_proposal_count": 1,
            "review_set_count": 3, "packet_count": len(snapshot["packets"]),
            "decision_count": len(snapshot["decisions"]), "journal_event_count": len(snapshot["events"]),
            "component_approvals_not_transferred": True, "uncovered_code_write_visible": True,
            "unchanged_basis_survives_unrelated_revision": True, "code_objection_localized": True,
            "shared_dependency_change_blocks_composite": True, "composition_replays_original": True,
            "file_proposals_remain_unaccepted": True,
            "human_review_recorded": False, "auto_accept": False,
        },
        "limitations": [
            "Authors, integrator and reviewers are programmed local workers, not LLMs or independent experts.",
            "Write-ID coverage is not semantic coverage; metadata and complete program behavior require separate review.",
            "Quantity fields are explicitly authored illustration metadata, not extracted from or aligned with the templates.",
            "The fictional semantic source and runtime fixture source are separate; identity is not inferred.",
            "Runtime and journal are sequential observations without a shared transaction or reservation of acceptance.",
            "A current review set still requires human review and grants no acceptance or publication right.",
            "The scenario is not a quality benchmark, load measurement or execution of proposed code.",
        ],
    }
    _write_json(workspace / "report.json", report)
    lines = [
        "# Выполненный пример композиции документа и кода", "",
        "Два программных автора создают отдельные предложения. Программный интегратор сохраняет новое предложение",
        "с двумя файлами и объединёнными объявленными основаниями. Два редакционных агента рассматривают каждый файл.",
        "Источники вымышлены, внешние LLM не вызываются. Предложенный Python-код не выполняется.",
        "Человеческое заключение не имитируется.", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические события хранят время в UTC.", "",
        "## Наблюдавшиеся переходы", "",
        "| Этап | Состояние | Основание |", "|---|---|---|",
        "| Компоненты проверены, композиция ещё не проверена | "
        + before_formal["observation"]["state"] + " | `unapproved`: прежние формальные решения не перенесены |",
        "| Композиция проверена, приложен только пакет документа | "
        + partial["observation"]["state"] + " | `uncovered_writes`: пакет кода отсутствует |",
        "| Приложены пакеты обоих файлов | " + complete["observation"]["state"]
        + " | Все write ID сопоставлены; рассмотрение человеком всё ещё требуется |",
        "| Принято постороннее изменение Windows | " + after_unrelated["state"]
        + " | Объявленные основания композиции не изменились |",
        "| Поддержка пакета кода заменена замечанием | " + after_objection["state"]
        + " | Замечание относится к пакету кода; у пакета документа нет нового замечания |",
        "| Общий тайм-аут изменён с 30 до 40 секунд | " + after_dependency["state"]
        + " | `stale_read` у композиции и обеих записей |", "",
        "Повтор завершённой операции композиции после изменения runtime вернул первоначальное предложение",
        "с `replayed=true`. Новое предложение и дополнительная формальная проверка при повторе не создавались.", "",
        "## Что подтверждает набор рассмотрения", "",
        "`before.text` каждого пакета буквально совпадает с принятым шаблоном соответствующего файла,",
        "а `after.text` — с шаблоном в составном предложении. Рассматриваются `docs/reference.md` и `code/client_config.py`.",
        "Покрытие write ID показывает отсутствие пропущенного файла; оно не означает покрытия каждого утверждения,",
        "метаданных, побочных эффектов или поведения программы. Поля 30 секунд / 30000 миллисекунд заданы авторами",
        "отдельно и не извлечены из шаблонов. Они не доказывают смысловую эквивалентность полных файлов.", "",
        "## Принятие и ограничения", "",
        "Исходные предложения и композиция остались `proposed`; выпуск не создавался.",
        "`Store.accept` использован только явно для двух фоновых вымышленных claims: Windows и тайм-аут.",
        "Создание и повторная оценка набора рассмотрения не принимают изменения.",
        "Снимки SQLite runtime и редакторского журнала читаются последовательно: общей транзакции нет.",
        "Имена и тип участников самозаявленные; несколько решений не доказывают независимость экспертизы.", "",
        "## Исполнение и материалы", "",
        f"Размер пула: {workers}; заданий авторов: {len(authors)}; первых редакционных решений: {len(editorial_reviews)}.",
        "Наблюдавшиеся потоки авторов: " + ", ".join(report["execution"]["observed_author_threads"]) + ".",
        "Наблюдавшиеся потоки редакторов: " + ", ".join(report["execution"]["observed_editorial_threads"]) + ".",
        "Это выполненные локальные задания; ёмкость сервиса, производительность и качество LLM не измерялись.", "",
        "- [Полный отчёт](report.json) и [композиция](composition.json).",
        "- [Повтор композиции](composition-replay.json) и [исходные предложения](source-proposals.json).",
        "- [Все привязки](bindings.json) и [неполные привязки](partial-bindings.json).",
        "- [До формальной проверки](before-formal-review-set.json).",
        "- [Неполное рассмотрение](partial-review-set.json) и [полное рассмотрение](complete-review-set.json).",
        "- [После постороннего изменения](recheck-after-unrelated.json).",
        "- [После замечания](recheck-after-objection.json).",
        "- [После изменения общей зависимости](recheck-after-dependency.json).",
        "- [Редакторский журнал](journal.json).", "",
    ]
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
