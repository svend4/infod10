"""Programmed document/code authors expose gaps in an explicit promise inventory.

This ordinary product workflow reads proposed code as data. Only a fictional
background timeout claim is accepted. No file proposal or release is accepted.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
from threading import Barrier, current_thread
from typing import Any
from zoneinfo import ZoneInfo

from .coverage import analyze_coverage, save_report
from .dossier_demo import FIXTURE_SOURCE, _claim, _file, _proposal
from .editorial import EditorialJournal
from . import obligations
from .review_set import create_review_set, save_review_set
from .store import Store


METHODS_QUOTE = "Клиент повторяет только методы GET и HEAD."
ATTEMPTS_QUOTE = "Клиент делает не более 3 попыток."
POLICY_SOURCE = (
    "FICTIONAL AUTHORED INVENTORY SCENARIO. Not a real client specification.\n"
    "The baseline declared retry methods are GET.\n"
    "The proposed declared retry methods are GET and HEAD.\n"
    "Structured set metadata concerns methods only, not attempt limits or complete program behavior.\n"
)


def _pair(before: str, after: str) -> dict[str, Any]:
    """Attach explicitly authored fictional set metadata to exact templates."""
    source_hash = hashlib.sha256(POLICY_SOURCE.encode("utf-8")).hexdigest()

    def statement(text: str, members: list[str], quote: str) -> dict[str, Any]:
        return {
            "subject": "fictional-api-client", "property": "declared-retry-methods",
            "scope": {"version": "v1", "policy": "retry-methods"},
            "kind": "specification", "modality": "asserted", "polarity": "positive",
            "value": {"type": "set", "members": members, "complete": True},
            "attribution": "programmed scenario author",
            "evidence": {"source_id": "fictional-inventory-policy", "source_digest": source_hash, "quote": quote},
            "text": text,
        }

    return {
        "before": statement(before, ["GET"], "The baseline declared retry methods are GET."),
        "after": statement(after, ["GET", "HEAD"], "The proposed declared retry methods are GET and HEAD."),
        "sources": {"fictional-inventory-policy": {"text": POLICY_SOURCE, "sha256": source_hash}},
    }


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete product workflow before writing a success report."""
    if not condition:
        raise RuntimeError("Coverage demonstration stopped: " + message)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def _codes(value: Any) -> set[str]:
    if isinstance(value, dict):
        own = {value["code"]} if isinstance(value.get("code"), str) else set()
        return own.union(*(_codes(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_codes(item) for item in value))
    return set()


def run_coverage_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save one bounded cooperative workflow in a new empty workspace."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Coverage demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    store = Store(workspace / "state.sqlite3")
    journal = EditorialJournal(workspace / "editorial.sqlite3")
    store.add_source("dossier-fixture", FIXTURE_SOURCE)
    before_files = {
        "document": _file("docs/reference.md", "document",
            "# Вымышленный клиент API\n\nТайм-аут: {{api.timeout_seconds}} секунд.\n\n"
            "Объявленная политика повторов разрешает GET.\n\n"
            '```json agentwiki:retry-policy\n{"allowed_methods":["GET"],"max_attempts":3}\n```\n'),
        "code": _file("code/client_config.py", "code",
            '"""Fictional client configuration."""\nTIMEOUT_SECONDS = {{api.timeout_seconds}}\n'
            'ALLOWED_METHODS = {"GET"}\nMAX_ATTEMPTS = 3\n'),
    }
    after_files = {
        "document": _file("docs/reference.md", "document",
            "# Вымышленный клиент API\n\nТайм-аут: {{api.timeout_seconds}} секунд.\n\n"
            + METHODS_QUOTE + "\n\n" + ATTEMPTS_QUOTE + "\n\n"
            '```json agentwiki:retry-policy\n{"allowed_methods":["GET","HEAD"],"max_attempts":3}\n```\n'),
        "fixed": _file("code/client_config.py", "code",
            '"""Fictional proposed configuration; not executed."""\n'
            'TIMEOUT_SECONDS = {{api.timeout_seconds}}\nALLOWED_METHODS = {"GET", "HEAD"}\nMAX_ATTEMPTS = 3\n'),
        "changed": _file("code/client_config.py", "code",
            '"""Fictional proposed configuration; not executed."""\n'
            'TIMEOUT_SECONDS = {{api.timeout_seconds}}\nALLOWED_METHODS = {"GET", "HEAD"}\nMAX_ATTEMPTS = 4\n'),
    }
    store.bootstrap([
        _claim("api.timeout_seconds", 30, "s", "The API timeout may be 30 or 40 seconds."),
        _claim("api.max_attempts", 3, "count", "The retry limit is 3 attempts."),
        _claim("api.budget_seconds", 120, "s", "The total retry budget is 120 seconds."),
        *before_files.values(),
    ])
    initial_snapshot = store.snapshot()
    roles = {"document": "doc_author", "fixed": "code_author", "changed": "code_author"}
    for label, body in after_files.items():
        store.enqueue("coverage-" + label, roles[label], {
            "proposal": _proposal(initial_snapshot, body, "Author an explicit fictional retry-policy contribution.")})
    ready = Barrier(min(workers, len(after_files)))

    def author_job(index: int, label: str) -> dict[str, Any]:
        if index < min(workers, len(after_files)):
            ready.wait(timeout=30)
        author = "coverage-" + label + "-author"
        task = store.claim_task(author, roles=[roles[label]], lease_seconds=60)
        _require(task is not None, "a queued author task was unavailable")
        actual_label = task["task_id"].removeprefix("coverage-")
        submitted = store.submit(task["task_id"], author, task["token"], task["payload"]["proposal"])
        return {"label": actual_label, "author": author, "thread": current_thread().name,
                "task_id": task["task_id"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="coverage-author") as executor:
        futures = [executor.submit(author_job, index, label) for index, label in enumerate(after_files)]
        authors = {item["label"]: item for item in (future.result() for future in futures)}
    component_reviews = {label: store.review(item["proposal_id"], reviewer="component-formal-agent")
                         for label, item in authors.items()}
    _require(all(item["approved"] for item in component_reviews.values()), "a component formal review failed")
    packets, pairs = {}, {}
    for label, body in after_files.items():
        before = before_files["document" if label == "document" else "code"]
        pairs[label] = _pair(before["template"], body["template"])
        packets[label] = journal.create_packet(pairs[label], proposer=authors[label]["author"], proposer_kind="agent")
        _write_json(workspace / (label + "-pair.json"), pairs[label])
    jobs = [(label, reviewer) for label in after_files for reviewer in ("editorial-agent-a", "editorial-agent-b")]
    ready_reviews = Barrier(min(workers, len(jobs)))

    def review_job(index: int, label: str, reviewer: str) -> dict[str, Any]:
        if index < min(workers, len(jobs)):
            ready_reviews.wait(timeout=30)
        packet = packets[label]
        decision = journal.record_decision(
            packet["packet_id"], reviewer=reviewer, actor_kind="agent", verdict="approve",
            rationale=("Programmed consideration of authored fictional retry-method metadata. "
                       "This does not establish attempt-limit compliance, complete prose, code behavior or truth. "
                       "The promise inventory is separately authored and analyzed; human review remains required."),
            operation_id=label + "-" + reviewer + "-1", expected_packet_digest=packet["packet_digest"],
            expected_previous_decision=None,
        )
        return {"label": label, "reviewer": reviewer, "thread": current_thread().name, "decision": decision}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="coverage-review") as executor:
        futures = [executor.submit(review_job, index, label, reviewer)
                   for index, (label, reviewer) in enumerate(jobs)]
        editorial_reviews = [future.result() for future in futures]
    compositions, composite_reviews, bindings, review_sets = {}, {}, {}, {}
    for label in ("fixed", "changed"):
        store.enqueue("coverage-compose-" + label, "integrator", {
            "components": [{"proposal_id": authors[item]["proposal_id"], "digest": authors[item]["digest"]}
                           for item in ("document", label)],
            "rationale": "Compose the document and the " + label + " literal configuration for inventory review.",
        })
        task = store.claim_task("coverage-integrator", roles=["integrator"], lease_seconds=60)
        _require(task is not None, "the composition task was unavailable")
        compositions[label] = store.compose(task["task_id"], "coverage-integrator", task["token"])
        composite_reviews[label] = store.review(compositions[label]["proposal_id"], reviewer="composite-formal-agent")
        _require(composite_reviews[label]["approved"], "a composite formal review failed")
        bindings[label] = [{"artifact_id": after_files[item]["id"], "packet_id": packets[item]["packet_id"],
                            "pair": pairs[item]} for item in ("document", label)]
        review_sets[label] = create_review_set(store, journal, compositions[label]["proposal_id"], bindings[label])
        _require(review_sets[label]["observation"]["state"] == "current_for_human_review", "a complete review set was blocked")
        save_review_set(review_sets[label], workspace / (label + "-review-set.json"))
        _write_json(workspace / (label + "-bindings.json"), bindings[label])

    methods = {
        "id": "retry-methods", "description": "Declared configured methods must be allowed by the document.",
        "scope": {"api_version": "v1", "policy": "retry-methods"},
        "relation": "code_subset_of_document", "value_type": "string_set",
        "document": {"artifact_id": "docs/reference.md", "fence_id": "retry-policy", "path": ["allowed_methods"]},
        "code": {"artifact_id": "code/client_config.py", "symbol": "ALLOWED_METHODS"},
    }
    attempts = {
        "id": "retry-attempts", "description": "The declared configuration attempt limit equals the document value.",
        "scope": {"api_version": "v1", "policy": "retry-limit"}, "relation": "equals", "value_type": "integer",
        "document": {"artifact_id": "docs/reference.md", "fence_id": "retry-policy", "path": ["max_attempts"]},
        "code": {"artifact_id": "code/client_config.py", "symbol": "MAX_ATTEMPTS"},
    }
    partial_spec = {"schema_version": 1, "obligations": [methods]}
    full_spec = {"schema_version": 1, "obligations": [methods, attempts]}

    def promise(identifier: str, quote: str, requirement: dict[str, Any]) -> dict[str, Any]:
        return {"id": identifier, "description": requirement["description"],
                "anchor": {"artifact_id": "docs/reference.md", "quote": quote},
                "requirement": {key: deepcopy(requirement[key])
                                for key in ("scope", "relation", "value_type", "document", "code")},
                "obligation_ids": [requirement["id"]]}

    full_inventory = {"schema_version": 1, "promises": [
        promise("allowed-methods-promise", METHODS_QUOTE, methods),
        promise("attempt-limit-promise", ATTEMPTS_QUOTE, attempts),
    ]}
    partial_inventory = deepcopy(full_inventory)
    partial_inventory["promises"][1]["obligation_ids"] = []
    wrong_inventory = deepcopy(full_inventory)
    wrong_inventory["promises"][1]["obligation_ids"] = ["retry-methods"]
    for filename, value in [("partial-spec.json", partial_spec), ("full-spec.json", full_spec),
                            ("partial-inventory.json", partial_inventory), ("full-inventory.json", full_inventory),
                            ("wrong-mapping-inventory.json", wrong_inventory)]:
        _write_json(workspace / filename, value)
    analyses = {
        "partial": obligations.analyze_obligations(review_sets["fixed"], partial_spec),
        "full": obligations.analyze_obligations(review_sets["fixed"], full_spec),
        "changed-value": obligations.analyze_obligations(review_sets["changed"], full_spec),
    }
    coverage_reports = {
        "partial": analyze_coverage(analyses["partial"], partial_inventory),
        "full": analyze_coverage(analyses["full"], full_inventory),
        "wrong-mapping": analyze_coverage(analyses["full"], wrong_inventory),
        "changed-value": analyze_coverage(analyses["changed-value"], full_inventory),
    }
    _require(analyses["partial"]["observation"]["state"] == "current_for_human_review"
             and coverage_reports["partial"]["observation"]["state"] == "blocked"
             and coverage_reports["partial"]["observation"]["counts"]["uncovered"] == 1,
             "an unrepresented listed attempt promise was not exposed")
    _require(coverage_reports["full"]["observation"]["state"] == "current_for_human_review"
             and coverage_reports["full"]["observation"]["counts"]["covered"] == 2
             and coverage_reports["full"]["observation"]["counts"]["matching"] == 2,
             "the complete matching declared inventory was blocked")
    _require(coverage_reports["wrong-mapping"]["observation"]["state"] == "blocked"
             and coverage_reports["wrong-mapping"]["observation"]["counts"]["invalid_mapping"] == 1,
             "a method obligation was allowed to stand in for the attempt-limit requirement")
    _require(analyses["changed-value"]["observation"]["declared_relation_state"] == "relations_mismatch"
             and coverage_reports["changed-value"]["observation"]["state"] == "blocked"
             and coverage_reports["changed-value"]["observation"]["counts"]["covered"] == 2
             and coverage_reports["changed-value"]["observation"]["counts"]["mismatch"] == 1,
             "coverage hid the changed declared attempt value")

    update = _claim("api.timeout_seconds", 40, "s", "The API timeout may be 30 or 40 seconds.")
    store.enqueue("coverage-timeout-update", "analyst", {
        "proposal": _proposal(store.snapshot(), update, "Change the shared fictional timeout dependency.")})
    task = store.claim_task("background-analyst", roles=["analyst"], lease_seconds=60)
    _require(task is not None, "the background task was unavailable")
    submitted = store.submit(task["task_id"], "background-analyst", task["token"], task["payload"]["proposal"])
    background_review = store.review(submitted["proposal_id"], reviewer="background-formal-agent")
    accepted = store.accept(submitted["proposal_id"], coordinator="background-coordinator")
    _require(background_review["approved"] and accepted["accepted"], "the fictional timeout update failed")
    review_sets["stale"] = create_review_set(store, journal, compositions["fixed"]["proposal_id"], bindings["fixed"])
    save_review_set(review_sets["stale"], workspace / "stale-review-set.json")
    analyses["stale"] = obligations.analyze_obligations(review_sets["stale"], full_spec)
    coverage_reports["stale"] = analyze_coverage(analyses["stale"], full_inventory)
    _require(analyses["stale"]["observation"]["declared_relation_state"] == "declared_relations_match"
             and coverage_reports["stale"]["observation"]["state"] == "blocked"
             and coverage_reports["stale"]["observation"]["counts"]["matching"] == 2
             and "stale_read" in _codes(coverage_reports["stale"]["observation"]),
             "matching listed promises hid a stale proposal dependency")
    for stage, analysis in analyses.items():
        obligations.save_report(analysis, workspace / (stage + "-obligations.json"))
    for stage, analysis in coverage_reports.items():
        save_report(analysis, workspace / (stage + "-coverage.json"))
    proposals = {label: store.proposal(item["proposal_id"]) for label, item in authors.items()}
    proposals.update({"composite-" + label: store.proposal(item["proposal_id"])
                      for label, item in compositions.items()})
    _require(all(item["status"] == "proposed" for item in proposals.values()),
             "an advisory operation accepted a document or code proposal")
    journal_snapshot = journal.export_snapshot()
    _write_json(workspace / "journal.json", journal_snapshot)
    _write_json(workspace / "proposals.json", proposals)
    _write_json(workspace / "compositions.json", compositions)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin", "workspace": str(workspace),
        "concurrency": {
            "worker_pool_size": workers, "submitted_author_jobs": len(authors),
            "submitted_editorial_review_jobs": len(editorial_reviews),
            "author_ids": sorted({item["author"] for item in authors.values()}),
            "observed_author_threads": sorted({item["thread"] for item in authors.values()}),
            "observed_editorial_threads": sorted({item["thread"] for item in editorial_reviews}),
            "integrator_id": "coverage-integrator", "component_formal_reviewer_id": "component-formal-agent",
            "composite_formal_reviewer_id": "composite-formal-agent", "all_actor_kinds": ["agent"],
        },
        "authors": authors, "component_reviews": component_reviews, "compositions": compositions,
        "composite_reviews": composite_reviews, "editorial_reviews": editorial_reviews,
        "dependency_change": {"submitted": submitted, "review": background_review, "acceptance": accepted},
        "stages": {stage: {"report_id": analysis["report_id"], "observation": analysis["observation"]}
                   for stage, analysis in coverage_reports.items()},
        "final_runtime_status": store.status(),
        "summary": {
            "component_proposal_count": len(authors), "composite_proposal_count": len(compositions),
            "review_set_count": len(review_sets), "obligation_report_count": len(analyses),
            "coverage_report_count": len(coverage_reports), "listed_promise_count": 2,
            "packet_count": len(journal_snapshot["packets"]), "decision_count": len(journal_snapshot["decisions"]),
            "journal_event_count": len(journal_snapshot["events"]),
            "uncovered_listed_promise_exposed": True, "complete_declared_mapping_matches": True,
            "wrong_mapping_blocked": True, "changed_value_remains_blocked": True,
            "matching_values_do_not_hide_stale_basis": True, "file_proposals_remain_unaccepted": True,
            "human_review_recorded": False, "auto_accept": False,
        },
        "limitations": [
            "The inventory is manually authored and lists two promises; it does not prove completeness of the page.",
            "Exact unique anchors attach inventory records to captured rendered text bytes, not to inferred or certified meaning.",
            "Requirement metadata and scope are caller-authored; exact mapping does not establish prose entailment.",
            "The equality requirement is a declared configuration policy, not a proof of runtime attempt behavior.",
            "Only captured inputs are read; no live-state check or shared database transaction occurs.",
            "Authors and reviewers are programmed workers with self-declared identities, not LLMs or independent experts.",
            "Fictional set metadata covers methods only; it does not assess all wording or attempt-limit compliance.",
            "The scenario is not a quality benchmark, load measurement or execution of proposed Python code.",
        ],
    }
    _write_json(workspace / "report.json", report)
    descriptions = {
        "partial": "Лимит попыток перечислен, но обязательство не приложено",
        "full": "Оба обещания связаны с правильными полями",
        "wrong-mapping": "Лимиту попыток ошибочно сопоставлено обязательство методов",
        "changed-value": "Код объявляет 4 попытки при документированных 3",
        "stale": "Тайм-аут фонового claim изменён с 30 до 40",
    }
    lines = [
        "# Выполненный пример покрытия описи обещаний", "",
        "Программные авторы подготовили документ и два варианта конфигурации вымышленного клиента.",
        "Интегратор создал две композиции с собственным формальным ревью и полным комплектом пакетов.",
        "Предложения документов и кода остались непринятыми; выпуск не создавался.", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin); технические события используют UTC.", "",
        "## Наблюдавшиеся этапы", "", "| Этап | Результат |", "|---|---|",
    ]
    for stage, analysis in coverage_reports.items():
        lines.append(f"| {descriptions[stage]} | {analysis['observation']['state']} |")
    lines += ["", "В первом этапе прежнее обязательство методов совпадает. Ручная опись отдельно перечисляет",
        "обещание о максимуме попыток, для которого ещё нет связи с обязательством: весь результат блокируется.",
        "Добавление связи `max_attempts` → `MAX_ATTEMPTS` покрывает оба перечисленных обещания, пока значения",
        "равны 3. Ссылка на подходящее по результату, но другое по полям обязательство методов блокируется.", "",
        "В другой композиции `MAX_ATTEMPTS = 4`. Наличие правильной связи означает покрытие,",
        "однако объявленные значения расходятся: полнота связей не снимает `mismatch`.",
        "После явного принятия фонового claim тайм-аута повторно захвачен комплект исправленной композиции.",
        "Оба выбранных значения совпадают, но основание предложения устарело (`stale_read`), и блокер сохранён.", "",
        "## Границы вывода", "",
        "Опись написана вручную и содержит два обещания. Она не доказывает полноту охвата всей страницы.",
        "Точное уникальное вхождение цитаты связывает запись с байтами захваченного отрендеренного документа,",
        "но не устанавливает смысл",
        "прозы и не доказывает корректность выбранных полей. Поля, области действия и требование равенства",
        "заданы авторами; правило сравнивает декларации конфигурации, а не поведение клиента.", "",
        "Редакторские пары используют отдельно заданные вымышленные метаданные набора методов. Они не",
        "оценивают весь текст или лимит попыток. Шаблоны прикреплены точно, человеческое заключение не имитируется.",
        "Имена и типы участников самозаявленные; внешние LLM не подключены, предложенный код не выполняется.",
        "Снимки двух баз последовательны, общей транзакции нет. Анализ читает сохранённый отчёт и не обращается",
        "к живой базе. `current_for_human_review` не разрешает `Store.accept` или публикацию.", "",
        "## Материалы", "",
        "- [Полный отчёт](report.json), [предложения](proposals.json), [композиции](compositions.json).",
        "- [Редакторский журнал](journal.json), [полная опись](full-inventory.json), [полные обязательства](full-spec.json).",
        "- [Опись без связи лимита](partial-inventory.json), [обязательство методов](partial-spec.json).",
        "- [Опись с ошибочной связью](wrong-mapping-inventory.json).",
    ]
    for stage in coverage_reports:
        source_stage = "full" if stage == "wrong-mapping" else stage
        lines.append(f"- `{stage}`: [покрытие]({stage}-coverage.json), [обязательства]({source_stage}-obligations.json).")
    for stage in review_sets:
        lines.append(f"- Комплект `{stage}`: [снимок]({stage}-review-set.json).")
    lines += ["", f"Пул: {workers}; авторских заданий: {len(authors)}; редакторских заданий: {len(editorial_reviews)}.",
              "Размер пула не является измеренной ёмкостью сервиса или числом LLM.", ""]
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
