"""A bounded product example joining runtime proposals and editorial history.

Programmed authors and reviewers work on document and code data. Dossiers are
advisory observations; only two explicit background fixture updates use
Store.accept. Neither dossier operation accepts the proposed files.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from threading import Barrier, current_thread
from typing import Any
from zoneinfo import ZoneInfo

from .corpus import get_case
from .dossier import create_dossier, recheck_dossier, save_dossier
from .editorial import EditorialJournal
from .store import Store


DATABASE_NAME = "state.sqlite3"
JOURNAL_DATABASE_NAME = "editorial.sqlite3"
FIXTURE_SOURCE = """FICTIONAL DOSSIER DEMONSTRATION SPECIFICATION. Not a real API.
The API timeout may be 30 or 40 seconds.
The retry limit is 3 attempts.
The total retry budget is 120 seconds.
Windows is supported.
The policy is timeout_seconds * max_attempts <= budget_seconds.
"""


def _claim(identifier: str, value: Any, unit: str, quote: str,
           scope: str = "api:v1") -> dict[str, Any]:
    return {"id": identifier, "kind": "claim", "scope": scope,
            "value": value, "unit": unit,
            "evidence": {"source_id": "dossier-fixture", "quote": quote}}


def _file(identifier: str, kind: str, template: str) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "template": template,
            "bindings": ["api.timeout_seconds"]}


def _proposal(snapshot: dict[str, Any], body: dict[str, Any],
              rationale: str) -> dict[str, Any]:
    reads = {body["id"], *body.get("bindings", [])}
    return {"readset": {key: snapshot["digests"].get(key) for key in sorted(reads)},
            "queries": {}, "writes": [deepcopy(body)], "rationale": rationale}


def _pair(before_template: str, after_template: str) -> dict[str, Any]:
    # These structured fields are authored illustration metadata. They are not
    # parsed from either template and do not establish whole-file equivalence.
    case = get_case("units-equivalent-seconds-milliseconds")
    pair = {key: deepcopy(case[key]) for key in ("before", "after", "sources")}
    pair["before"]["text"] = before_template
    pair["after"]["text"] = after_template
    return pair


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete product scenario before writing a success report."""
    if not condition:
        raise RuntimeError("Dossier demonstration stopped: " + message)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_dossier_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save one cooperative workflow in a new, otherwise empty directory."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Dossier demo needs a new empty workspace; existing data is preserved")
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
                      '"""Fictional API configuration; source values require editorial review."""\n'
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
        store.enqueue("dossier-" + label, roles[label], {
            "proposal": _proposal(initial_snapshot, body,
                                  "Revise one existing file from the captured runtime basis.")
        })

    authors_ready = Barrier(min(workers, len(after_files)))

    def author_job(label: str) -> dict[str, Any]:
        authors_ready.wait(timeout=30)
        author = "dossier-" + label + "-author"
        task = store.claim_task(author, roles=[roles[label]], lease_seconds=60)
        _require(task is not None, "the author's queued task was not available")
        submitted = store.submit(task["task_id"], author, task["token"],
                                 task["payload"]["proposal"])
        return {"label": label, "author": author, "thread": current_thread().name,
                "task_id": task["task_id"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dossier-author") as executor:
        author_futures = {label: executor.submit(author_job, label) for label in after_files}
        authors = {label: future.result() for label, future in author_futures.items()}

    formal_reviews = {}
    packets = {}
    pairs = {}
    for label, author in authors.items():
        formal_reviews[label] = store.review(author["proposal_id"], reviewer="formal-review-agent")
        _require(formal_reviews[label]["approved"], "the initial formal review was not approved")
        pairs[label] = _pair(before_files[label]["template"], after_files[label]["template"])
        packets[label] = journal.create_packet(pairs[label], proposer=author["author"],
                                                proposer_kind="agent")
        _write_json(workspace / (label + "-pair.json"), pairs[label])

    review_jobs = [(label, reviewer) for label in after_files
                   for reviewer in ("editorial-agent-a", "editorial-agent-b")]
    reviewers_ready = Barrier(min(workers, len(review_jobs)))

    def reviewer_job(index: int, label: str, reviewer: str) -> dict[str, Any]:
        if index < min(workers, len(review_jobs)):
            reviewers_ready.wait(timeout=30)
        packet = packets[label]
        decision = journal.record_decision(
            packet["packet_id"], reviewer=reviewer, actor_kind="agent", verdict="approve",
            rationale=("The explicitly authored quantity metadata compares 30 seconds with "
                       "30000 milliseconds. Exact template attachment is checked separately. "
                       "This programmed approval does not assess all wording or code behavior."),
            operation_id=label + "-" + reviewer + "-1",
            expected_packet_digest=packet["packet_digest"], expected_previous_decision=None,
        )
        return {"label": label, "reviewer": reviewer, "thread": current_thread().name,
                "decision": decision}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="dossier-review") as executor:
        review_futures = [executor.submit(reviewer_job, index, label, reviewer)
                          for index, (label, reviewer) in enumerate(review_jobs)]
        reviews = [future.result() for future in review_futures]

    dossiers = {}
    for label, author in authors.items():
        dossier = create_dossier(store, journal, author["proposal_id"],
                                 packets[label]["packet_id"], after_files[label]["id"], pairs[label])
        _require(dossier["observation"]["state"] == "current_for_human_review",
                 "the initial dossier was not current for human review")
        dossiers[label] = dossier
        save_dossier(dossier, workspace / (label + "-dossier.json"))

    def accept_background(task_id: str, body: dict[str, Any], rationale: str) -> dict[str, Any]:
        snapshot = store.snapshot()
        store.enqueue(task_id, "analyst", {"proposal": _proposal(snapshot, body, rationale)})
        task = store.claim_task("background-analyst", roles=["analyst"], lease_seconds=60)
        _require(task is not None, "the background task was not available")
        submitted = store.submit(task["task_id"], "background-analyst", task["token"],
                                 task["payload"]["proposal"])
        review = store.review(submitted["proposal_id"], reviewer="background-formal-review-agent")
        accepted = store.accept(submitted["proposal_id"], coordinator="background-coordinator")
        _require(review["approved"] and accepted["accepted"], "the background fixture update failed")
        return {"task_id": task_id, "submitted": submitted, "review": review, "acceptance": accepted}

    unrelated_change = accept_background(
        "unrelated-windows", _claim("platform.windows", True, "", "Windows is supported.",
                                    scope="platforms:api-v1"),
        "Change an unrelated artifact without changing either proposal's declared basis.",
    )
    after_unrelated = {
        label: recheck_dossier(dossier, store, journal, pairs[label])
        for label, dossier in dossiers.items()
    }
    _require(all(item["state"] == "current_for_human_review" for item in after_unrelated.values()),
             "an unrelated revision incorrectly made a dossier stale")

    earlier_decision = next(item["decision"] for item in reviews
                            if item["label"] == "document" and item["reviewer"] == "editorial-agent-a")
    objection = journal.record_decision(
        packets["document"]["packet_id"], reviewer="editorial-agent-a", actor_kind="agent",
        verdict="request_changes",
        rationale=("The quantity metadata covers one declared comparison. A human editor still "
                   "needs to assess the complete document and whether its wording is supported."),
        operation_id="document-editorial-agent-a-2",
        expected_packet_digest=packets["document"]["packet_digest"],
        expected_previous_decision=earlier_decision["decision_id"],
    )
    after_objection = {
        label: recheck_dossier(dossier, store, journal, pairs[label])
        for label, dossier in dossiers.items()
    }
    _require(after_objection["document"]["state"] == "blocked",
             "the document objection did not block the current observation")
    _require(after_objection["code"]["state"] == "current_for_human_review",
             "a review of another packet incorrectly changed the code dossier")

    dependency_change = accept_background(
        "timeout-update", _claim("api.timeout_seconds", 40, "s",
                                 "The API timeout may be 30 or 40 seconds."),
        "Change the declared timeout dependency; 40 times 3 still fits the 120-second budget.",
    )
    after_dependency = {
        label: recheck_dossier(dossier, store, journal, pairs[label])
        for label, dossier in dossiers.items()
    }
    for label, item in after_dependency.items():
        _require(any(error.get("code") == "stale_read"
                     for error in item["current_observation"]["runtime_errors"]),
                 label + " did not retain the stale runtime read")
        _require(store.proposal(authors[label]["proposal_id"])["status"] == "proposed",
                 "an advisory dossier unexpectedly accepted a proposed file")

    rechecks = {"after-unrelated": after_unrelated, "after-objection": after_objection,
                "after-dependency": after_dependency}
    for stage, items in rechecks.items():
        for label, value in items.items():
            _write_json(workspace / ("recheck-" + label + "-" + stage + ".json"), value)
    snapshot = journal.export_snapshot()
    _write_json(workspace / "journal.json", snapshot)
    report = {
        "schema_version": 1,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin", "workspace": str(workspace),
        "uses_llm": False, "fictional_sources": True, "executes_proposed_code": False,
        "requires_human_review": True, "human_review_recorded": False, "auto_accept": False,
        "calls_store_accept": True,
        "store_accept_scope": "Only the unrelated Windows claim and timeout background fixture update.",
        "accepts_document_or_code_proposals": False, "publishes_release": False,
        "execution": {
            "requested_pool_workers": workers, "submitted_author_jobs": len(authors),
            "submitted_editorial_review_jobs": len(reviews),
            "observed_author_threads": sorted({item["thread"] for item in authors.values()}),
            "observed_editorial_threads": sorted({item["thread"] for item in reviews}),
            "author_ids": [item["author"] for item in authors.values()],
            "editorial_reviewer_ids": sorted({item["reviewer"] for item in reviews}),
            "formal_reviewer_id": "formal-review-agent", "all_actor_kinds": ["agent"],
        },
        "initial_runtime_revision": initial_snapshot["revision"],
        "authors": authors, "formal_reviews": formal_reviews, "packets": packets,
        "editorial_reviews": reviews,
        "initial_dossiers": {label: {"dossier_id": dossier["dossier_id"],
                                       "dossier_digest": dossier["dossier_digest"],
                                       "observation": dossier["observation"]}
                             for label, dossier in dossiers.items()},
        "unrelated_change": unrelated_change, "objection": objection,
        "dependency_change": dependency_change, "rechecks": rechecks,
        "final_runtime_status": store.status(),
        "summary": {
            "dossier_count": len(dossiers), "packet_count": len(snapshot["packets"]),
            "decision_count": len(snapshot["decisions"]), "journal_event_count": len(snapshot["events"]),
            "unchanged_basis_survives_unrelated_revision": True,
            "changed_decision_heads_are_visible": True,
            "relevant_runtime_change_remains_a_stale_read": True,
            "document_and_code_proposals_remain_unaccepted": True,
            "human_review_recorded": False, "auto_accept": False,
        },
        "limitations": [
            "Authors and reviewers are programmed local workers, not LLMs or independent experts.",
            "The structured quantity fields are explicitly authored; they are not extracted from or semantically aligned with the templates.",
            "Exact text attachment binds the selected file template, not the truth or meaning of its complete content.",
            "The fictional semantic source is separate from the runtime fixture source; their identity is not inferred.",
            "Runtime and journal reads are separate observations; there is no transaction across the databases or reservation of future acceptance.",
            "A current dossier still requires human review and grants no acceptance or publication right.",
            "The scenario is not a quality benchmark, load measurement, or execution of proposed code.",
        ],
    }
    _write_json(workspace / "report.json", report)
    lines = [
        "# Выполненный пример досье для документа и кода", "",
        "Два программных автора предлагают изменения существующих файлов. Формальный проверяющий проверяет runtime,",
        "а два редакционных агента записывают решения по каждому пакету. Внешние LLM не вызываются; источники вымышлены.",
        "Предлагаемый код сохраняется как текст и не выполняется. Человеческое заключение не имитируется.", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические события хранят время в UTC.", "",
        "## Наблюдавшиеся переходы", "",
        "| Этап | Документ | Код |", "|---|---|---|",
        "| Созданы досье после формальной и редакционной проверки | "
        + dossiers["document"]["observation"]["state"] + " | "
        + dossiers["code"]["observation"]["state"] + " |",
    ]
    labels = {"after-unrelated": "Принято изменение посторонней платформы",
              "after-objection": "Проверяющий документа заменил поддержку замечанием",
              "after-dependency": "Принято изменение тайм-аута с 30 до 40 секунд"}
    for stage, items in rechecks.items():
        lines.append(f"| {labels[stage]} | {items['document']['state']} | {items['code']['state']} |")
    lines.extend([
        "", "Смена общей ревизии сама по себе не делает досье устаревшим: сравниваются объявленные основания.",
        "Смена последнего решения видна отдельно от изменения runtime. После обновления тайм-аута оба предложения",
        "содержат `stale_read`, хотя их шаблоны и пакеты не менялись. Старые файлы досье сохраняют первоначальное наблюдение.", "",
        "## Что именно связано", "",
        "Каждое досье относится к одному существующему файлу: `docs/reference.md` либо `code/client_config.py`.",
        "`before.text` буквально равен принятому шаблону; `after.text` — шаблону в соответствующем proposal.",
        "Поля семантической пары 30 секунд / 30000 миллисекунд заданы авторами проекта отдельно.",
        "Они не извлечены из шаблонов и не подтверждают эквивалентность всей формулировки или поведения программы.",
        "Вымышленный семантический источник и runtime-источник сохранены отдельно; программа не устанавливает их тождество.", "",
        "## Принятие и ограничения", "",
        "`current_for_human_review` означает актуальное основание для рассмотрения человеком. Автоматического принятия нет.",
        "Предложения документа и кода остались в состоянии `proposed`; выпуск не создавался.",
        "Координатор явно принял только два фоновых изменения вымышленных claims: Windows и тайм-аут.",
        "Сбор досье и повторное рассмотрение не вызывают `Store.accept`.",
        "SQLite runtime и редакторский журнал читаются раздельно: общей транзакции и блокировки будущего принятия нет.", "",
        "## Исполнение и файлы", "",
        f"Размер пула: {workers}; заданий авторов: {len(authors)}; первых редакционных решений: {len(reviews)}.",
        "Наблюдавшиеся потоки авторов: " + ", ".join(report["execution"]["observed_author_threads"]) + ".",
        "Наблюдавшиеся потоки редакторов: " + ", ".join(report["execution"]["observed_editorial_threads"]) + ".",
        "Это размер локального пула и выполненные задания; производительность LLM или ёмкость сервиса не измерялась.", "",
        "- [Полный отчёт](report.json).",
        "- [Досье документа](document-dossier.json) и [досье кода](code-dossier.json).",
        "- [Пара документа](document-pair.json) и [пара кода](code-pair.json).",
        "- [Редакторский журнал](journal.json).",
        "- [Документ после замечания](recheck-document-after-objection.json).",
        "- [Документ после изменения зависимости](recheck-document-after-dependency.json).",
        "- [Код после изменения зависимости](recheck-code-after-dependency.json).", "",
    ])
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
