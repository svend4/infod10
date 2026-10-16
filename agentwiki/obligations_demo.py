"""Programmed authors expose declared document/configuration disagreements.

The ordinary scenario saves three proposed compositions and four observations.
Only an explicit background timeout claim is accepted. Proposed Python text is
never executed; successful relation analysis grants no acceptance permission.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
from threading import Barrier, current_thread
from typing import Any
from zoneinfo import ZoneInfo

from .dossier_demo import FIXTURE_SOURCE, _claim, _file, _proposal
from .editorial import EditorialJournal
from .obligations import analyze_obligations, save_report
from .review_set import create_review_set, save_review_set
from .store import Store


DATABASE_NAME = "state.sqlite3"
JOURNAL_DATABASE_NAME = "editorial.sqlite3"
POLICY_SOURCE = (
    "FICTIONAL AUTHORED RETRY-POLICY SCENARIO. Not a real client specification.\n"
    "The baseline declared retry methods are GET.\n"
    "The documented allowed retry methods are GET and HEAD.\n"
    "The unsafe configuration initially declares GET, HEAD and POST.\n"
    "The fixed configuration initially declares GET and HEAD.\n"
    "The dynamic configuration initially declares GET and HEAD, then adds POST.\n"
)


def _policy_pair(before_template: str, after_template: str, label: str) -> dict[str, Any]:
    """Author explicit set metadata; do not infer meaning from free prose."""
    source_hash = hashlib.sha256(POLICY_SOURCE.encode("utf-8")).hexdigest()
    quotes = {
        "document": "The documented allowed retry methods are GET and HEAD.",
        "unsafe": "The unsafe configuration initially declares GET, HEAD and POST.",
        "fixed": "The fixed configuration initially declares GET and HEAD.",
        "dynamic": "The dynamic configuration initially declares GET and HEAD, then adds POST.",
    }

    def statement(text: str, members: list[str], quote: str, complete: bool) -> dict[str, Any]:
        return {
            "subject": "fictional-api-client", "property": "declared-retry-methods",
            "scope": {"version": "v1", "policy": "retry-methods"},
            "kind": "specification", "modality": "asserted", "polarity": "positive",
            "value": {"type": "set", "members": members, "complete": complete},
            "attribution": "programmed scenario author",
            "evidence": {"source_id": "fictional-retry-policy", "source_digest": source_hash, "quote": quote},
            "text": text,
        }

    methods = ["GET", "HEAD", "POST"] if label == "unsafe" else ["GET", "HEAD"]
    return {
        "before": statement(before_template, ["GET"], "The baseline declared retry methods are GET.", True),
        "after": statement(after_template, methods, quotes[label], label != "dynamic"),
        "sources": {"fictional-retry-policy": {"text": POLICY_SOURCE, "sha256": source_hash}},
    }


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete product workflow before writing a success report."""
    if not condition:
        raise RuntimeError("Obligations demonstration stopped: " + message)


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _codes(value: Any) -> set[str]:
    if isinstance(value, dict):
        own = {value["code"]} if isinstance(value.get("code"), str) else set()
        return own.union(*(_codes(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_codes(item) for item in value))
    return set()


def run_obligations_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a bounded cooperative workflow into a new empty workspace."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Obligations demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    store = Store(workspace / DATABASE_NAME)
    journal = EditorialJournal(workspace / JOURNAL_DATABASE_NAME)
    store.add_source("dossier-fixture", FIXTURE_SOURCE)
    before_files = {
        "document": _file("docs/reference.md", "document",
            "# Вымышленный клиент API\n\nТайм-аут: {{api.timeout_seconds}} секунд.\n\n"
            "Разрешённые методы политики повторов: GET.\n\n"
            '```json agentwiki:retry-policy\n{"allowed_methods":["GET"]}\n```\n'),
        "code": _file("code/client_config.py", "code",
            '"""Fictional client configuration."""\n'
            'TIMEOUT_SECONDS = {{api.timeout_seconds}}\nALLOWED_METHODS = {"GET"}\n'),
    }
    after_files = {
        "document": _file("docs/reference.md", "document",
            "# Вымышленный клиент API\n\nТайм-аут: {{api.timeout_seconds}} секунд.\n\n"
            "Объявленная политика повторов разрешает GET и HEAD.\n\n"
            '```json agentwiki:retry-policy\n{"allowed_methods":["GET","HEAD"]}\n```\n'),
        "unsafe": _file("code/client_config.py", "code",
            '"""Fictional configuration; proposed data, not executed."""\n'
            'TIMEOUT_SECONDS = {{api.timeout_seconds}}\nALLOWED_METHODS = {"GET", "HEAD", "POST"}\n'),
        "fixed": _file("code/client_config.py", "code",
            '"""Fictional configuration; proposed data, not executed."""\n'
            'TIMEOUT_SECONDS = {{api.timeout_seconds}}\nALLOWED_METHODS = {"GET", "HEAD"}\n'),
        "dynamic": _file("code/client_config.py", "code",
            '"""Fictional configuration; mutation is outside the static grammar."""\n'
            'TIMEOUT_SECONDS = {{api.timeout_seconds}}\nALLOWED_METHODS = {"GET", "HEAD"}\n'
            'ALLOWED_METHODS.add("POST")\n'),
    }
    store.bootstrap([
        _claim("api.timeout_seconds", 30, "s", "The API timeout may be 30 or 40 seconds."),
        _claim("api.max_attempts", 3, "count", "The retry limit is 3 attempts."),
        _claim("api.budget_seconds", 120, "s", "The total retry budget is 120 seconds."),
        *before_files.values(),
    ])
    initial_snapshot = store.snapshot()
    authors: dict[str, dict[str, Any]] = {}
    component_reviews: dict[str, dict[str, Any]] = {}
    packets: dict[str, dict[str, Any]] = {}
    pairs: dict[str, dict[str, Any]] = {}
    editorial_reviews: list[dict[str, Any]] = []

    def prepare_components(labels: list[str]) -> None:
        for label in labels:
            store.enqueue("obligation-" + label, "doc_author" if label == "document" else "code_author", {
                "proposal": _proposal(initial_snapshot, after_files[label],
                                      "Author the explicitly declared retry-policy configuration."),
            })
        ready = Barrier(min(workers, len(labels)))

        def author_job(index: int, label: str) -> dict[str, Any]:
            if index < min(workers, len(labels)):
                ready.wait(timeout=30)
            author = "obligation-" + label + "-author"
            role = "doc_author" if label == "document" else "code_author"
            task = store.claim_task(author, roles=[role], lease_seconds=60)
            _require(task is not None, "a queued author task was unavailable")
            actual_label = task["task_id"].removeprefix("obligation-")
            submitted = store.submit(task["task_id"], author, task["token"], task["payload"]["proposal"])
            return {"label": actual_label, "author": author, "thread": current_thread().name,
                    "task_id": task["task_id"], **submitted}

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="obligation-author") as executor:
            jobs = [executor.submit(author_job, index, label) for index, label in enumerate(labels)]
            for future in jobs:
                author = future.result()
                label = author["label"]
                authors[label] = author
                component_reviews[label] = store.review(author["proposal_id"], reviewer="component-formal-agent")
                _require(component_reviews[label]["approved"], "a component formal review failed")
        for label in labels:
            before = before_files["document" if label == "document" else "code"]
            # These set fields are caller-authored metadata, not extracted
            # facts. Dynamic mutation is intentionally not a complete set.
            pairs[label] = _policy_pair(before["template"], after_files[label]["template"], label)
            packets[label] = journal.create_packet(pairs[label], proposer=authors[label]["author"],
                                                   proposer_kind="agent")
            _write_json(workspace / (label + "-pair.json"), pairs[label])
        jobs = [(label, reviewer) for label in labels
                for reviewer in ("editorial-agent-a", "editorial-agent-b")]
        ready_reviews = Barrier(min(workers, len(jobs)))

        def review_job(index: int, label: str, reviewer: str) -> dict[str, Any]:
            if index < min(workers, len(jobs)):
                ready_reviews.wait(timeout=30)
            packet = packets[label]
            decision = journal.record_decision(
                packet["packet_id"], reviewer=reviewer, actor_kind="agent", verdict="approve",
                rationale=("Programmed approval for consideration of the authored retry-method set metadata. "
                           "This does not certify truth, complete wording or code behavior. "
                           "Declared cross-file obligations are analyzed separately; human review remains required."),
                operation_id=label + "-" + reviewer + "-1", expected_packet_digest=packet["packet_digest"],
                expected_previous_decision=None,
            )
            return {"label": label, "reviewer": reviewer, "thread": current_thread().name,
                    "decision": decision}

        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="obligation-review") as executor:
            futures = [executor.submit(review_job, index, label, reviewer)
                       for index, (label, reviewer) in enumerate(jobs)]
            editorial_reviews.extend(future.result() for future in futures)

    spec = {"schema_version": 1, "obligations": [{
        "id": "retry-methods", "description": "Configured retry methods must be allowed by the declared document policy.",
        "scope": {"api_version": "v1", "policy": "retry-methods"},
        "relation": "code_subset_of_document", "value_type": "string_set",
        "document": {"artifact_id": "docs/reference.md", "fence_id": "retry-policy", "path": ["allowed_methods"]},
        "code": {"artifact_id": "code/client_config.py", "symbol": "ALLOWED_METHODS"},
    }]}
    _write_json(workspace / "obligations.json", spec)
    compositions: dict[str, dict[str, Any]] = {}
    composite_reviews: dict[str, dict[str, Any]] = {}
    bindings: dict[str, list[dict[str, Any]]] = {}
    review_sets: dict[str, dict[str, Any]] = {}
    analyses: dict[str, dict[str, Any]] = {}

    def observe(stage: str, proposal_id: str, selected: list[dict[str, Any]]) -> None:
        review_set = create_review_set(store, journal, proposal_id, selected)
        analysis = analyze_obligations(review_set, spec)
        review_sets[stage] = review_set
        analyses[stage] = analysis
        save_review_set(review_set, workspace / (stage + "-review-set.json"))
        save_report(analysis, workspace / (stage + "-obligations.json"))
        _write_json(workspace / (stage + "-bindings.json"), selected)

    def compose_variant(label: str) -> None:
        store.enqueue("obligation-compose-" + label, "integrator", {
            "components": [{"proposal_id": authors[item]["proposal_id"], "digest": authors[item]["digest"]}
                           for item in ("document", label)],
            "rationale": "Compose the document with the " + label + " configuration for declared relation analysis.",
        })
        task = store.claim_task("obligation-integrator", roles=["integrator"], lease_seconds=60)
        _require(task is not None, "the composition task was unavailable")
        composed = store.compose(task["task_id"], "obligation-integrator", task["token"])
        compositions[label] = composed
        composite_reviews[label] = store.review(composed["proposal_id"], reviewer="composite-formal-agent")
        _require(composite_reviews[label]["approved"], "the composite formal review failed")
        bindings[label] = [{"artifact_id": after_files[item]["id"], "packet_id": packets[item]["packet_id"],
                            "pair": pairs[item]} for item in ("document", label)]
        observe(label, composed["proposal_id"], bindings[label])

    prepare_components(["document", "unsafe"])
    compose_variant("unsafe")
    _require(review_sets["unsafe"]["observation"]["state"] == "current_for_human_review",
             "the fully covered unsafe composition was not available for relation analysis")
    _require(analyses["unsafe"]["observation"]["declared_relation_state"] == "relations_mismatch",
             "the configured extra method was not exposed")
    prepare_components(["fixed", "dynamic"])
    compose_variant("fixed")
    compose_variant("dynamic")
    _require(analyses["fixed"]["observation"]["state"] == "current_for_human_review",
             "the fixed declared relation was not available for human review")
    _require(analyses["dynamic"]["observation"]["declared_relation_state"] == "unresolved",
             "a dynamic module was incorrectly treated as a closed static configuration")
    update = _claim("api.timeout_seconds", 40, "s", "The API timeout may be 30 or 40 seconds.")
    store.enqueue("obligation-timeout-update", "analyst", {
        "proposal": _proposal(store.snapshot(), update, "Change the shared fictional timeout dependency."),
    })
    task = store.claim_task("background-analyst", roles=["analyst"], lease_seconds=60)
    _require(task is not None, "the background dependency task was unavailable")
    submitted = store.submit(task["task_id"], "background-analyst", task["token"], task["payload"]["proposal"])
    background_review = store.review(submitted["proposal_id"], reviewer="background-formal-agent")
    accepted = store.accept(submitted["proposal_id"], coordinator="background-coordinator")
    _require(background_review["approved"] and accepted["accepted"], "the fictional timeout update failed")
    observe("stale", compositions["fixed"]["proposal_id"], bindings["fixed"])
    _require(analyses["stale"]["observation"]["declared_relation_state"] == "declared_relations_match"
             and analyses["stale"]["observation"]["state"] == "blocked"
             and "stale_read" in _codes(analyses["stale"]["observation"]),
             "matching declared values hid a stale upstream basis")
    proposals = {label: store.proposal(item["proposal_id"]) for label, item in authors.items()}
    proposals.update({"composite-" + label: store.proposal(item["proposal_id"])
                      for label, item in compositions.items()})
    _require(all(item["status"] == "proposed" for item in proposals.values()),
             "an advisory operation accepted a proposed document or code file")
    journal_snapshot = journal.export_snapshot()
    _write_json(workspace / "journal.json", journal_snapshot)
    _write_json(workspace / "proposals.json", proposals)
    _write_json(workspace / "compositions.json", compositions)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin", "workspace": str(workspace),
        "uses_llm": False, "fictional_sources": True, "executes_proposed_code": False,
        "requires_human_review": True, "human_review_recorded": False, "auto_accept": False,
        "calls_store_accept": True, "store_accept_scope": "One background fictional timeout claim only.",
        "accepts_document_or_code_proposals": False, "publishes_release": False,
        "execution": {
            "requested_pool_workers": workers, "submitted_author_jobs": len(authors),
            "submitted_editorial_review_jobs": len(editorial_reviews),
            "author_ids": sorted({item["author"] for item in authors.values()}),
            "observed_author_threads": sorted({item["thread"] for item in authors.values()}),
            "observed_editorial_threads": sorted({item["thread"] for item in editorial_reviews}),
            "integrator_id": "obligation-integrator", "component_formal_reviewer_id": "component-formal-agent",
            "composite_formal_reviewer_id": "composite-formal-agent", "all_actor_kinds": ["agent"],
        },
        "authors": authors, "component_reviews": component_reviews, "compositions": compositions,
        "composite_reviews": composite_reviews, "editorial_reviews": editorial_reviews,
        "dependency_change": {"submitted": submitted, "review": background_review, "acceptance": accepted},
        "stages": {stage: {"review_set_id": review_sets[stage]["review_set_id"],
                           "review_set_state": review_sets[stage]["observation"]["state"],
                           "report_id": analysis["report_id"], "observation": analysis["observation"]}
                   for stage, analysis in analyses.items()},
        "final_runtime_status": store.status(),
        "summary": {
            "component_proposal_count": len(authors), "composite_proposal_count": len(compositions),
            "review_set_count": len(review_sets), "obligation_report_count": len(analyses),
            "packet_count": len(journal_snapshot["packets"]), "decision_count": len(journal_snapshot["decisions"]),
            "journal_event_count": len(journal_snapshot["events"]),
            "extra_post_method_exposed": True, "repaired_declared_relation_matches": True,
            "dynamic_module_remains_unresolved": True, "matching_values_do_not_hide_stale_basis": True,
            "file_proposals_remain_unaccepted": True, "human_review_recorded": False, "auto_accept": False,
        },
        "limitations": [
            "Local authors and reviewers are programmed workers, not LLMs or independent experts.",
            "The document fence and code symbol are caller-selected; scope metadata is declared, not inferred.",
            "The comparison covers one declared policy value, not prose entailment or complete program behavior.",
            "Set metadata and fictional scenario quotes are authored separately; citation integrity is not entailment or truth.",
            "Dynamic metadata lists only the initial declaration and marks completeness false; mutation is not evaluated.",
            "The shared document packet is intentionally reused and is not a new independent review.",
            "Only the captured snapshot is analyzed; no live-state query or cross-database transaction occurs.",
            "The scenario is not a quality benchmark, load measurement or execution of proposed Python code.",
        ],
    }
    _write_json(workspace / "report.json", report)
    lines = [
        "# Выполненный пример обязательств между документом и конфигурацией", "",
        "Программные авторы предлагают документ и три варианта Python-конфигурации вымышленного клиента.",
        "Интегратор создаёт три отдельных составных предложения. Каждое получает собственное формальное ревью",
        "и полный комплект редакторских пакетов. Предложения файлов остаются непринятыми, выпуск не создаётся.",
        "Внешние LLM не вызываются; предложенный код не выполняется; человеческое заключение не имитируется.", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin); технические события используют UTC.", "",
        "## Наблюдавшиеся этапы", "",
        "| Этап | Комплект рассмотрения | Объявленная связь | Общий результат |",
        "|---|---|---|---|",
    ]
    descriptions = {"unsafe": "В коде дополнительно объявлен POST", "fixed": "Код исправлен на GET и HEAD",
                    "dynamic": "После объявления множества добавлен вызов .add", "stale": "Общий тайм-аут изменён с 30 до 40"}
    for stage, analysis in analyses.items():
        observation = analysis["observation"]
        lines.append(f"| {descriptions[stage]} | {review_sets[stage]['observation']['state']} | "
                     f"{observation['declared_relation_state']} | {observation['state']} |")
    lines += ["", "В первом варианте покрытие обоих файлов и согласие программных проверяющих не обнаруживали",
        "несовпадение политики. Дополнительное обязательство извлекает `allowed_methods` из явно помеченного",
        "JSON-блока документа и `ALLOWED_METHODS` из ограниченного статического модуля: `POST` отсутствует",
        "в документе, поэтому отношение `code_subset_of_document` нарушено.", "",
        "Исправление создаёт новое предложение кода и новую композицию. Первоначальные предложения и результаты",
        "сохранены. Неизменившийся пакет документа переиспользован явно; повтор не означает независимой экспертизы.",
        "Вариант с `.add(\"POST\")` получает `unresolved`: анализатор не делает вывода по одному литералу,",
        "когда остальной модуль выходит за ограниченную грамматику конфигурации.", "",
        "После явного принятия фонового claim тайм-аута повторно захвачен комплект исправленной композиции.",
        "Связь методов по-прежнему совпадает, но `stale_read` исходного предложения сохраняет общий результат `blocked`.", "",
        "## Границы вывода", "",
        "Проверяется только объявленное отношение выбранных значений. JSON-блок не доказывает соответствие",
        "произвольному тексту страницы; модуль конфигурации не доказывает поведение сетевого клиента.",
        "Область действия задана автором и не извлекается автоматически. Множества методов в редакторских парах",
        "заданы авторами отдельно и связаны с вымышленным снимком сценария. Наличие цитаты не доказывает",
        "соответствие метаданных всему тексту или коду. Для динамического варианта указано начальное объявление",
        "GET и HEAD с `complete=false`: последующая мутация не вычисляется.",
        "Источники вымышлены. Имена и типы участников самозаявленные. Все результаты требуют рассмотрения человеком.", "",
        "`Store.accept` вызван только для одного фонового вымышленного claim тайм-аута; ни один документ или код",
        "не принят. Снимки runtime и журнала последовательны. Анализ отчёта читает только сохранённые данные,",
        "не запрашивает текущие базы и не резервирует разрешение принятия.", "",
        "## Материалы", "",
        "- [Полный отчёт](report.json), [обязательство](obligations.json), [предложения](proposals.json).",
        "- [Композиции](compositions.json) и [редакторский журнал](journal.json).",
    ]
    for stage in analyses:
        lines.append(f"- `{stage}`: [комплект]({stage}-review-set.json), [анализ]({stage}-obligations.json), "
                     f"[привязки]({stage}-bindings.json).")
    lines += ["", f"Пул: {workers}; авторских заданий: {len(authors)}; редакторских заданий: {len(editorial_reviews)}.",
              "Размер пула и выполненные задания не являются измеренной ёмкостью сервиса или числом LLM.", ""]
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
