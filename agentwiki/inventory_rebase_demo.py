"""A fresh cooperative workflow for retaining an explicit promise inventory.

Only a fictional background timeout claim is accepted. Proposed document and
Python bodies are data: no file proposal is accepted or executed, no release
is published, and old editorial decisions never authorize a new candidate.
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

from . import coverage, inventory_rebase, obligations
from .coverage_demo import ATTEMPTS_QUOTE, METHODS_QUOTE, _codes, _pair
from .dossier_demo import FIXTURE_SOURCE, _claim, _file, _proposal
from .editorial import EditorialJournal
from .review_set import create_review_set, save_review_set
from .store import Store


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete product workflow before writing a success report."""
    if not condition:
        raise RuntimeError("Inventory-rebase demonstration stopped: " + message)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _spec(document_id: str, code_id: str, suffix: str = "") -> dict[str, Any]:
    return {"schema_version": 1, "obligations": [
        {"id": "retry-methods" + suffix, "description": "The declared code methods must be allowed by the document.",
         "scope": {"api_version": "v1", "policy": "retry-methods"},
         "relation": "code_subset_of_document", "value_type": "string_set",
         "document": {"artifact_id": document_id, "fence_id": "retry-policy", "path": ["allowed_methods"]},
         "code": {"artifact_id": code_id, "symbol": "ALLOWED_METHODS"}},
        {"id": "retry-attempts" + suffix, "description": "The declared attempt limit must equal the document value.",
         "scope": {"api_version": "v1", "policy": "retry-limit"},
         "relation": "equals", "value_type": "integer",
         "document": {"artifact_id": document_id, "fence_id": "retry-policy", "path": ["max_attempts"]},
         "code": {"artifact_id": code_id, "symbol": "MAX_ATTEMPTS"}},
    ]}


def run_rebase_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a bounded product example in a new empty directory."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Inventory-rebase demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    store = Store(workspace / "state.sqlite3")
    journal = EditorialJournal(workspace / "editorial.sqlite3")
    store.add_source("dossier-fixture", FIXTURE_SOURCE)
    old_doc = (
        "# Вымышленный клиент API\n\nТайм-аут: {{api.timeout_seconds}} секунд.\n\n"
        "Объявленная политика повторов разрешает GET.\n\n"
        '```json agentwiki:retry-policy\n{"allowed_methods":["GET"],"max_attempts":3}\n```\n'
    )
    new_doc = (
        "# Вымышленный клиент API\n\nТайм-аут: {{api.timeout_seconds}} секунд.\n\n"
        + METHODS_QUOTE + "\n\n" + ATTEMPTS_QUOTE + "\n\n"
        '```json agentwiki:retry-policy\n{"allowed_methods":["GET","HEAD"],"max_attempts":3}\n```\n'
    )
    old_code = (
        '"""Fictional baseline configuration."""\nTIMEOUT_SECONDS = {{api.timeout_seconds}}\n'
        'ALLOWED_METHODS = {"GET"}\nMAX_ATTEMPTS = 3\n'
    )
    new_code = (
        '"""Fictional candidate configuration; never executed."""\n'
        'TIMEOUT_SECONDS = {{api.timeout_seconds}}\nALLOWED_METHODS = {"GET", "HEAD"}\nMAX_ATTEMPTS = 3\n'
    )
    doc_id, code_id = "docs/reference.md", "code/client_config.py"
    renamed_doc_id, renamed_code_id = "docs/reference-v2.md", "code/client_config_v2.py"
    baseline = {
        doc_id: _file(doc_id, "document", old_doc), code_id: _file(code_id, "code", old_code),
        renamed_doc_id: _file(renamed_doc_id, "document", old_doc),
        renamed_code_id: _file(renamed_code_id, "code", old_code),
    }
    store.bootstrap([
        _claim("api.timeout_seconds", 30, "s", "The API timeout may be 30 or 40 seconds."),
        _claim("api.max_attempts", 3, "count", "The retry limit is 3 attempts."),
        _claim("api.budget_seconds", 120, "s", "The total retry budget is 120 seconds."),
        *baseline.values(),
    ])
    bodies = {
        "base-document": _file(doc_id, "document", new_doc),
        "base-code": _file(code_id, "code", new_code),
        "prose-document": _file(doc_id, "document", "Вводное пояснение к вымышленной конфигурации.\n\n" + new_doc),
        "renamed-document": _file(renamed_doc_id, "document", "Кандидат размещён по явно сопоставленному пути.\n\n" + new_doc),
        "renamed-code": _file(renamed_code_id, "code", new_code.replace("Fictional candidate configuration;", "Fictional candidate at an explicitly mapped path;")),
        "changed-document": _file(doc_id, "document", new_doc.replace(ATTEMPTS_QUOTE, "Клиент делает не более 4 попыток.").replace('"max_attempts":3', '"max_attempts":4')),
        "changed-code": _file(code_id, "code", new_code.replace("MAX_ATTEMPTS = 3", "MAX_ATTEMPTS = 4")),
    }
    initial_snapshot = store.snapshot()
    for label, body in bodies.items():
        role = "doc_author" if body["kind"] == "document" else "code_author"
        store.enqueue("inventory-rebase-" + label, role, {
            "proposal": _proposal(initial_snapshot, body, "Author a fictional candidate for explicit inventory maintenance.")})
    ready = Barrier(min(workers, len(bodies)))

    def author_job(index: int, label: str) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            ready.wait(timeout=30)
        role = "doc_author" if bodies[label]["kind"] == "document" else "code_author"
        author = "inventory-document-author" if role == "doc_author" else "inventory-code-author"
        task = store.claim_task(author, roles=[role], lease_seconds=60)
        _require(task is not None, "an author task was unavailable")
        actual_label = task["task_id"].removeprefix("inventory-rebase-")
        submitted = store.submit(task["task_id"], author, task["token"], task["payload"]["proposal"])
        return {"label": actual_label, "author": author, "thread": current_thread().name,
                "task_id": task["task_id"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="inventory-author") as executor:
        futures = [executor.submit(author_job, index, label) for index, label in enumerate(bodies)]
        authors = {result["label"]: result for result in (future.result() for future in futures)}
    component_reviews = {label: store.review(item["proposal_id"], reviewer="component-formal-agent")
                         for label, item in authors.items()}
    _require(all(item["approved"] for item in component_reviews.values()), "a component formal review failed")
    packets, pairs = {}, {}
    for label, body in bodies.items():
        pairs[label] = _pair(baseline[body["id"]]["template"], body["template"])
        packets[label] = journal.create_packet(pairs[label], proposer=authors[label]["author"], proposer_kind="agent")
        _write_json(workspace / (label + "-pair.json"), pairs[label])
    jobs = [(label, reviewer) for label in bodies for reviewer in ("inventory-review-agent-a", "inventory-review-agent-b")]
    ready_reviews = Barrier(min(workers, len(jobs)))

    def review_job(index: int, label: str, reviewer: str) -> dict[str, Any]:
        if index < min(workers, len(jobs)):
            ready_reviews.wait(timeout=30)
        packet = packets[label]
        decision = journal.record_decision(
            packet["packet_id"], reviewer=reviewer, actor_kind="agent", verdict="approve",
            rationale=("Programmed review of separately supplied fictional retry-method metadata. "
                       "The pair is attached to exact templates; this is not whole-file semantic validation, "
                       "an attempt-limit review, an authorization or an independent expert conclusion. "
                       "This fresh decision concerns this packet only; no old decision is transferred."),
            operation_id=label + "-" + reviewer, expected_packet_digest=packet["packet_digest"],
            expected_previous_decision=None,
        )
        return {"label": label, "reviewer": reviewer, "thread": current_thread().name, "decision": decision}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="inventory-review") as executor:
        futures = [executor.submit(review_job, index, label, reviewer)
                   for index, (label, reviewer) in enumerate(jobs)]
        editorial_reviews = [future.result() for future in futures]
    selections = {
        "base": ("base-document", "base-code"),
        "prose": ("prose-document", "base-code"),
        "renamed": ("renamed-document", "renamed-code"),
        "changed": ("changed-document", "changed-code"),
    }
    compositions, composite_reviews, bindings, review_sets, analyses = {}, {}, {}, {}, {}
    full_spec = _spec(doc_id, code_id)
    renamed_spec = _spec(renamed_doc_id, renamed_code_id, "-v2")
    for stage, labels in selections.items():
        store.enqueue("inventory-compose-" + stage, "integrator", {
            "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                           for label in labels],
            "rationale": "Compose exact fictional document and code candidates for " + stage + " inventory planning.",
        })
        task = store.claim_task("inventory-integrator", roles=["integrator"], lease_seconds=60)
        _require(task is not None, "the composition task was unavailable")
        compositions[stage] = store.compose(task["task_id"], "inventory-integrator", task["token"])
        composite_reviews[stage] = store.review(compositions[stage]["proposal_id"], reviewer="composite-formal-agent")
        _require(composite_reviews[stage]["approved"], "a composite formal review failed")
        bindings[stage] = [{"artifact_id": bodies[label]["id"], "packet_id": packets[label]["packet_id"],
                            "pair": pairs[label]} for label in labels]
        review_sets[stage] = create_review_set(store, journal, compositions[stage]["proposal_id"], bindings[stage])
        _require(review_sets[stage]["observation"]["state"] == "current_for_human_review", "a complete review set was blocked")
        analyses[stage] = obligations.analyze_obligations(review_sets[stage], renamed_spec if stage == "renamed" else full_spec)
        _require(analyses[stage]["observation"]["declared_relation_state"] == "declared_relations_match",
                 "a fresh candidate did not match its explicitly declared configuration policy")
        save_review_set(review_sets[stage], workspace / (stage + "-review-set.json"))
        obligations.save_report(analyses[stage], workspace / (stage + "-obligations.json"))
        _write_json(workspace / (stage + "-bindings.json"), bindings[stage])
    inventory = {"schema_version": 1, "promises": [
        {"id": identifier, "description": requirement["description"],
         "anchor": {"artifact_id": doc_id, "quote": quote},
         "requirement": {key: deepcopy(requirement[key]) for key in ("scope", "relation", "value_type", "document", "code")},
         "obligation_ids": [requirement["id"]]}
        for identifier, quote, requirement in zip(
            ("allowed-methods-promise", "attempt-limit-promise"), (METHODS_QUOTE, ATTEMPTS_QUOTE), full_spec["obligations"])
    ]}
    original_coverage = coverage.analyze_coverage(analyses["base"], inventory)
    _require(original_coverage["observation"]["counts"]["covered"] == 2, "the original inventory was incomplete")
    coverage.save_report(original_coverage, workspace / "base-coverage.json")
    _write_json(workspace / "base-inventory.json", inventory)
    _write_json(workspace / "full-spec.json", full_spec)
    _write_json(workspace / "renamed-spec.json", renamed_spec)
    mapping = {"schema_version": 1,
               "artifact_ids": {doc_id: renamed_doc_id, code_id: renamed_code_id},
               "obligation_ids": {"retry-methods": "retry-methods-v2", "retry-attempts": "retry-attempts-v2"}}
    _write_json(workspace / "renamed-mapping.json", mapping)
    plans = {stage: inventory_rebase.plan_rebase(original_coverage, analyses[stage], mapping if stage == "renamed" else None)
             for stage in ("prose", "renamed", "changed")}
    for stage in ("prose", "renamed"):
        _require(plans[stage]["observation"]["counts"]["reusable_descriptor"] == 2,
                 "unchanged declared selectors, values, anchors and dependencies were not reusable in " + stage)
    changed_results = {item["id"]: item for item in plans["changed"]["observation"]["results"]}
    _require(changed_results["attempt-limit-promise"]["state"] != "reusable_descriptor"
             and not changed_results["attempt-limit-promise"]["draft_obligation_ids"],
             "the changed attempt quotation or value silently retained its old descriptor references")

    update = _claim("api.timeout_seconds", 40, "s", "The API timeout may be 30 or 40 seconds.")
    store.enqueue("inventory-timeout-update", "analyst", {
        "proposal": _proposal(store.snapshot(), update, "Change the shared fictional rendering dependency.")})
    task = store.claim_task("background-analyst", roles=["analyst"], lease_seconds=60)
    _require(task is not None, "the background task was unavailable")
    submitted = store.submit(task["task_id"], "background-analyst", task["token"], task["payload"]["proposal"])
    background_review = store.review(submitted["proposal_id"], reviewer="background-formal-agent")
    accepted = store.accept(submitted["proposal_id"], coordinator="background-coordinator")
    _require(background_review["approved"] and accepted["accepted"], "the fictional timeout update failed")
    review_sets["stale"] = create_review_set(store, journal, compositions["base"]["proposal_id"], bindings["base"])
    save_review_set(review_sets["stale"], workspace / "stale-review-set.json")
    analyses["stale"] = obligations.analyze_obligations(review_sets["stale"], full_spec)
    obligations.save_report(analyses["stale"], workspace / "stale-obligations.json")
    plans["stale"] = inventory_rebase.plan_rebase(original_coverage, analyses["stale"])
    _require(analyses["stale"]["observation"]["declared_relation_state"] == "declared_relations_match"
             and "stale_read" in _codes(plans["stale"]["observation"])
             and plans["stale"]["observation"]["draft_coverage"]["state"] == "blocked",
             "matching selected values hid a stale captured proposal basis")
    original_ids = [item["id"] for item in inventory["promises"]]
    for stage, plan in plans.items():
        draft = plan["observation"]["draft_inventory"]
        _require([item["id"] for item in draft["promises"]] == original_ids,
                 "a plan dropped or reordered an original promise")
        inventory_rebase.save_report(plan, workspace / (stage + "-rebase.json"))
        _write_json(workspace / (stage + "-draft-inventory.json"), draft)
    proposals = {label: store.proposal(item["proposal_id"]) for label, item in authors.items()}
    proposals.update({"composite-" + stage: store.proposal(item["proposal_id"])
                      for stage, item in compositions.items()})
    _require(all(item["status"] == "proposed" for item in proposals.values()), "an advisory operation accepted a file proposal")
    journal_snapshot = journal.export_snapshot()
    _write_json(workspace / "journal.json", journal_snapshot)
    _write_json(workspace / "proposals.json", proposals)
    _write_json(workspace / "compositions.json", compositions)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin", "workspace": str(workspace),
        "concurrency": {"worker_pool_size": workers, "submitted_author_jobs": len(authors),
                        "submitted_editorial_review_jobs": len(editorial_reviews),
                        "author_ids": sorted({item["author"] for item in authors.values()}),
                        "observed_author_threads": sorted({item["thread"] for item in authors.values()}),
                        "observed_editorial_threads": sorted({item["thread"] for item in editorial_reviews}),
                        "integrator_id": "inventory-integrator", "all_actor_kinds": ["agent"]},
        "authors": authors, "component_reviews": component_reviews, "compositions": compositions,
        "composite_reviews": composite_reviews, "editorial_reviews": editorial_reviews,
        "dependency_change": {"submitted": submitted, "review": background_review, "acceptance": accepted},
        "stages": {stage: {"report_id": plan["report_id"], "observation": plan["observation"]}
                   for stage, plan in plans.items()},
        "final_runtime_status": store.status(),
        "summary": {"component_proposal_count": len(authors), "composite_proposal_count": len(compositions),
                    "review_set_count": len(review_sets), "obligation_report_count": len(analyses),
                    "coverage_report_count": 1, "rebase_plan_count": len(plans), "listed_promise_count": len(original_ids),
                    "packet_count": len(journal_snapshot["packets"]), "decision_count": len(journal_snapshot["decisions"]),
                    "journal_event_count": len(journal_snapshot["events"]),
                    "all_original_promises_preserved": True, "old_editorial_decisions_transferred": False,
                    "file_proposals_remain_unaccepted": True, "human_review_recorded": False, "auto_accept": False},
        "limitations": [
            "The manually authored inventory lists two promises and does not establish completeness of a page.",
            "Descriptor reuse compares captured declared locations, values and dependencies; it is not semantic certification or approval transfer.",
            "The path mapping targets separately seeded artifacts; no runtime rename or deletion of original files occurs.",
            "Fresh self-declared programmed decisions are recorded for each exact packet; identical content can share a content-addressed packet.",
            "Fictional semantic metadata concerns retry methods only and does not independently validate whole prose or attempt limits.",
            "Proposed Python code is not executed, no external LLM or human review is simulated, and no release is created.",
            "Saved reports capture observations; there is no live-state check or shared transaction of the two databases.",
            "Worker pool size is not a measured capacity, a quality benchmark or a number of independent models.",
        ],
    }
    _write_json(workspace / "report.json", report)
    descriptions = {"prose": "Добавлено вводное пояснение вне выбранных обещаний",
                    "renamed": "Явно сопоставлены другие пути кандидатов и ID обязательств",
                    "changed": "Цитата и значения лимита изменены с 3 на 4",
                    "stale": "Фоновый тайм-аут изменён с 30 на 40"}
    lines = [
        "# Выполненный пример переноса описи обещаний", "",
        "Два программных автора подготовили семь вкладов; интегратор создал четыре композиции.",
        "Для каждого точного пакета решения записаны явно. План переноса читает сохранённые отчёты",
        "и не переносит редакторские голоса. Все файловые предложения остались непринятыми.", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin); технические события используют UTC.", "",
        "## Наблюдавшиеся этапы", "",
        "| Изменение | Повторно используемые описания | Требуют рассмотрения | Неразрешимы | Обещаний в черновике |",
        "|---|---:|---:|---:|---:|",
    ]
    for stage, plan in plans.items():
        counts = plan["observation"]["counts"]
        lines.append(f"| {descriptions[stage]} | {counts['reusable_descriptor']} | {counts['needs_review']} | {counts['unresolved']} | {len(plan['observation']['draft_inventory']['promises'])} |")
    lines += ["", "Вводный текст меняет расположение цитат, но выбранные цитаты, поля, типы, значения и зависимости",
        "остаются прежними. Для других путей задано явное сопоставление; альтернативные пути существовали",
        "в начальном снимке отдельно, операция переименования или удаления runtime не выполнялась.", "",
        "В варианте с четырьмя попытками документ и код согласованы между собой, однако прежнее обещание",
        "о трёх попытках изменилось. Оно сохраняет свой ID и место в описи, а неподтверждённые ссылки очищаются.",
        "Знаменатель остаётся равен двум: исчезновение прежней цитаты не удаляет запись из рассмотрения.", "",
        "После принятия только фонового claim тайм-аута выбранные значения методов и лимита по-прежнему",
        "совпадают. Изменённая зависимость и `stale_read` остаются видимыми; план не снимает блокировку.", "",
        "## Границы вывода", "",
        "Повторное использование описания касается явно объявленных полей и точной уникальной цитаты.",
        "Оно не переносит решения редакторов, не подтверждает смысл всей страницы и не разрешает принятие.",
        "Опись написана вручную; два пункта не означают полноту страницы. Смысловое соответствие цитаты",
        "структурированному полю требует рассмотрения человеком. Имена и типы участников самозаявленные.",
        "Внешние LLM не подключены, человеческое заключение не имитируется, предложенный код не выполняется.",
        "Снимки двух баз последовательны, общей транзакции нет; сохранённый анализ не проверяет живое состояние.", "",
        "## Материалы", "",
        "- [Полный отчёт](report.json), [предложения](proposals.json), [композиции](compositions.json).",
        "- [Редакторский журнал](journal.json), [прежняя опись](base-inventory.json), [прежнее покрытие](base-coverage.json).",
        "- [Исходные обязательства](full-spec.json), [обязательства других путей](renamed-spec.json), [явная карта ID](renamed-mapping.json).",
        "- [Начальный комплект](base-review-set.json), [начальные обязательства](base-obligations.json).",
    ]
    for stage in plans:
        lines.append(f"- `{stage}`: [план]({stage}-rebase.json), [черновик]({stage}-draft-inventory.json), [обязательства]({stage}-obligations.json), [комплект]({stage}-review-set.json).")
    lines += ["", f"Пул: {workers}; авторских заданий: {len(authors)}; редакторских заданий: {len(editorial_reviews)}.",
              "Размер пула не является измеренной ёмкостью сервиса или числом LLM.", ""]
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return report
