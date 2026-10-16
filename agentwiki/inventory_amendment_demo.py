"""Run explicit authored inventory changes on a pinned urllib3 literal adapter.

Source and adapter Python are read as data. Neither is imported or executed.
All file proposals remain proposed, and no runtime acceptance or release occurs.
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

from . import coverage, inventory_amendment, inventory_rebase, obligations
from .dossier_demo import _proposal
from .editorial import EditorialJournal
from .review_set import create_review_set, save_review_set
from .store import Store


NEW_TOTAL_QUOTE = "В urllib3 2.5.0 конструктор Retry задаёт total=10 по умолчанию; единица этого счётчика — повторы."


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete ordinary workflow before saving a success report."""
    if not condition:
        raise RuntimeError("Inventory-amendment demonstration stopped: " + message)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _file(identifier: str, kind: str, template: str) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "template": template, "bindings": []}


def _import_sources(workspace: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    corpus = Path(__file__).resolve().parents[1] / "corpora" / "urllib3-retry-v2.5.0"
    manifest_bytes = (corpus / "manifest.json").read_bytes()
    manifest = json.loads(manifest_bytes)
    verified = []
    for item in manifest["files"]:
        source = (corpus / item["local_path"]).read_bytes()
        actual = hashlib.sha256(source).hexdigest()
        _require(actual == item["sha256"] and len(source) == item["byte_count"],
                 "a pinned upstream source snapshot differs: " + item["local_path"])
        verified.append({**item, "verified_local_sha256": actual,
                         "verified_local_byte_count": len(source)})
    source_inputs = workspace / "source-inputs"
    source_inputs.mkdir()
    imported: dict[str, bytes] = {}
    bound_files = []
    paths = ["manifest.json", "source-facts.json", "authored/contract.md", "authored/retry_config.py",
             "authored/obligations.json", "authored/inventory.json", "authored/artifact-map.json"]
    for relative in paths:
        content = (corpus / relative).read_bytes()
        target = source_inputs / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            stream.write(content)
        imported[relative] = content
        bound_files.append({"corpus_path": "corpora/urllib3-retry-v2.5.0/" + relative,
                            "workspace_path": "source-inputs/" + relative,
                            "byte_count": len(content), "sha256": hashlib.sha256(content).hexdigest()})
    record = {
        "schema_version": 1, "kind": "pinned_sources_and_manually_authored_adapter",
        "upstream_repository": manifest["upstream_repository"], "requested_tag": manifest["requested_tag"],
        "tag_object": manifest["tag_object"], "peeled_commit": manifest["peeled_commit"],
        "verified_upstream_snapshots": verified, "imported_input_files": bound_files,
        "upstream_changed": False, "upstream_code_executed": False, "adapter_executed": False,
        "live_upstream_refetched": False, "independent_human_annotation": False, "benchmark": False,
        "limitations": [
            "Checks compare local captured snapshot bytes with the stored manifest, not current remote responses.",
            "The adapter is manually authored selected literals; source snapshot validity is not a semantic entailment proof.",
            "The versioned documentation URL is mutable; the stored SHA binds the captured bytes only.",
        ],
    }
    _write_json(workspace / "source-import.json", record)
    return record, imported


def _pair(before: str, after: str, source: str, quote: str, members: list[str]) -> dict[str, Any]:
    """Attach manually declared selected-method metadata to exact file templates."""
    source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()

    def statement(text: str) -> dict[str, Any]:
        return {
            "subject": "urllib3-Retry-authored-adapter", "property": "selected-default-allowed-methods",
            "scope": {"version": "2.5.0", "setting": "Retry.DEFAULT_ALLOWED_METHODS",
                      "representation": "manually-authored-selected-literals"},
            "kind": "specification", "modality": "asserted", "polarity": "positive",
            "value": {"type": "set", "members": members, "complete": True},
            "attribution": "programmed adapter scenario author",
            "evidence": {"source_id": "urllib3-adapter-contract", "source_digest": source_hash, "quote": quote},
            "text": text,
        }

    return {"before": statement(before), "after": statement(after),
            "sources": {"urllib3-adapter-contract": {"text": source, "sha256": source_hash}}}


def run_amendment_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a bounded end-to-end authored-adapter workflow in a new directory."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Inventory-amendment demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    source_import, inputs = _import_sources(workspace)
    contract = inputs["authored/contract.md"].decode("utf-8")
    code = inputs["authored/retry_config.py"].decode("utf-8")
    spec = json.loads(inputs["authored/obligations.json"])
    original_inventory = json.loads(inputs["authored/inventory.json"])
    methods_promise, total_promise = original_inventory["promises"]
    doc_id = total_promise["anchor"]["artifact_id"]
    code_id = total_promise["requirement"]["code"]["artifact_id"]
    old_quote = total_promise["anchor"]["quote"]
    _require(contract.count(old_quote) == 1, "the imported total promise is not uniquely anchored")
    baseline_bodies = {
        doc_id: _file(doc_id, "document", contract), code_id: _file(code_id, "code", code),
    }
    candidate_document = "Локальный кандидат авторского адаптера; исходная библиотека не изменяется.\n\n" + contract
    candidate_code = "# Local selected-literal candidate; read as data, never executed.\n" + code
    bodies = {
        "base-document": _file(doc_id, "document", candidate_document),
        "base-code": _file(code_id, "code", candidate_code),
        "reworded-document": _file(doc_id, "document", candidate_document.replace(old_quote, NEW_TOTAL_QUOTE)),
    }
    store = Store(workspace / "state.sqlite3")
    journal = EditorialJournal(workspace / "editorial.sqlite3")
    store.bootstrap(list(baseline_bodies.values()))
    initial_snapshot = store.snapshot()
    for label, body in bodies.items():
        role = "doc_author" if body["kind"] == "document" else "code_author"
        store.enqueue("amendment-" + label, role, {
            "proposal": _proposal(initial_snapshot, body,
                                  "Author a local selected-literal adapter candidate without changing urllib3 upstream.")})
    authors_ready = Barrier(min(workers, len(bodies)))

    def author_job(index: int, label: str) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            authors_ready.wait(timeout=30)
        role = "doc_author" if bodies[label]["kind"] == "document" else "code_author"
        author = "amendment-document-author" if role == "doc_author" else "amendment-code-author"
        task = store.claim_task(author, roles=[role], lease_seconds=60)
        _require(task is not None, "an author task was unavailable")
        actual_label = task["task_id"].removeprefix("amendment-")
        submitted = store.submit(task["task_id"], author, task["token"], task["payload"]["proposal"])
        return {"label": actual_label, "author": author, "thread": current_thread().name,
                "task_id": task["task_id"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="amendment-author") as executor:
        futures = [executor.submit(author_job, index, label) for index, label in enumerate(bodies)]
        authors = {result["label"]: result for result in (future.result() for future in futures)}
    component_reviews = {label: store.review(item["proposal_id"], reviewer="component-formal-agent")
                         for label, item in authors.items()}
    _require(all(item["approved"] for item in component_reviews.values()), "a component formal review failed")
    document_fields = json.loads(contract.split("```json agentwiki:retry-defaults\n", 1)[1].split("```", 1)[0])
    packets, pairs = {}, {}
    for label, body in bodies.items():
        pairs[label] = _pair(baseline_bodies[body["id"]]["template"], body["template"], contract,
                             methods_promise["anchor"]["quote"], document_fields["default_allowed_methods"])
        packets[label] = journal.create_packet(pairs[label], proposer=authors[label]["author"], proposer_kind="agent")
        _write_json(workspace / (label + "-pair.json"), pairs[label])
    jobs = [(label, reviewer) for label in bodies for reviewer in ("amendment-editor-agent-a", "amendment-editor-agent-b")]
    reviewers_ready = Barrier(min(workers, len(jobs)))

    def review_job(index: int, label: str, reviewer: str) -> dict[str, Any]:
        if index < min(workers, len(jobs)):
            reviewers_ready.wait(timeout=30)
        packet = packets[label]
        decision = journal.record_decision(
            packet["packet_id"], reviewer=reviewer, actor_kind="agent", verdict="approve",
            rationale=("Programmed review of manually supplied selected default-method-set metadata. "
                       "Exact templates are attached, but the fields are not inferred from their prose. "
                       "This decision does not assess the total quote, prove equivalence of its rewording, "
                       "validate urllib3 behavior, constitute human review or authorize acceptance."),
            operation_id=label + "-" + reviewer, expected_packet_digest=packet["packet_digest"],
            expected_previous_decision=None,
        )
        return {"label": label, "reviewer": reviewer, "thread": current_thread().name, "decision": decision}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="amendment-editor") as executor:
        futures = [executor.submit(review_job, index, label, reviewer)
                   for index, (label, reviewer) in enumerate(jobs)]
        editorial_reviews = [future.result() for future in futures]
    compositions, composite_reviews, bindings, review_sets, obligation_reports = {}, {}, {}, {}, {}
    for stage, labels in {"base": ("base-document", "base-code"),
                          "reworded": ("reworded-document", "base-code")}.items():
        store.enqueue("amendment-compose-" + stage, "integrator", {
            "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                           for label in labels],
            "rationale": "Compose exact local urllib3 adapter candidates for " + stage + " inventory consideration.",
        })
        task = store.claim_task("amendment-integrator", roles=["integrator"], lease_seconds=60)
        _require(task is not None, "the composition task was unavailable")
        compositions[stage] = store.compose(task["task_id"], "amendment-integrator", task["token"])
        composite_reviews[stage] = store.review(compositions[stage]["proposal_id"], reviewer="composite-formal-agent")
        _require(composite_reviews[stage]["approved"], "a composite formal review failed")
        bindings[stage] = [{"artifact_id": bodies[label]["id"], "packet_id": packets[label]["packet_id"],
                            "pair": pairs[label]} for label in labels]
        review_sets[stage] = create_review_set(store, journal, compositions[stage]["proposal_id"], bindings[stage])
        _require(review_sets[stage]["observation"]["state"] == "current_for_human_review", "a captured review set was blocked")
        obligation_reports[stage] = obligations.analyze_obligations(review_sets[stage], spec)
        _require(obligation_reports[stage]["observation"]["declared_relation_state"] == "declared_relations_match",
                 "the selected literal fields did not match the authored specification")
        save_review_set(review_sets[stage], workspace / (stage + "-review-set.json"))
        obligations.save_report(obligation_reports[stage], workspace / (stage + "-obligations.json"))
        _write_json(workspace / (stage + "-bindings.json"), bindings[stage])
    original_coverage = coverage.analyze_coverage(obligation_reports["base"], original_inventory)
    _require(original_coverage["observation"]["counts"]["covered"] == 2, "the base inventory was incomplete")
    coverage.save_report(original_coverage, workspace / "base-coverage.json")
    rebase = inventory_rebase.plan_rebase(original_coverage, obligation_reports["reworded"])
    rebase_results = {item["id"]: item for item in rebase["observation"]["results"]}
    _require(rebase_results[total_promise["id"]]["state"] == "unresolved"
             and rebase_results[methods_promise["id"]]["state"] == "reusable_descriptor",
             "an absent old quotation did not preserve an explicit unresolved promise")
    inventory_rebase.save_report(rebase, workspace / "reworded-rebase.json")
    revised = deepcopy(total_promise)
    revised["anchor"]["quote"] = NEW_TOTAL_QUOTE
    revised["description"] = "Explicit authored reconsideration: Retry constructor default total remains 10 retries; no conversion to attempts is claimed."
    wrong_unit = deepcopy(revised)
    wrong_unit["requirement"]["scope"]["unit"] = "attempts"
    plans = {
        "pending": [],
        "reaffirm-old-quote": [{"promise_id": total_promise["id"], "action": "reaffirm",
                                "rationale": "Attempt to retain the old descriptor, whose exact quote is absent from the target."}],
        "revised-quote": [{"promise_id": total_promise["id"], "action": "revise", "descriptor": revised,
                           "rationale": "Author explicitly reanchors the promise to the new wording and retains the selected retries unit and literal values."}],
        "wrong-unit": [{"promise_id": total_promise["id"], "action": "revise", "descriptor": wrong_unit,
                        "rationale": "Deliberately incorrect authored scope uses attempts although the target obligation specifies retries."}],
    }
    amendments, final_coverage = {}, {}
    original_ids = [item["id"] for item in original_inventory["promises"]]
    for label, actions in plans.items():
        plan = {"schema_version": 1, "rebase_report_id": rebase["report_id"],
                "rebase_report_digest": rebase["report_digest"], "actions": actions}
        _write_json(workspace / (label + "-changes.json"), plan)
        amendments[label] = inventory_amendment.apply_amendments(rebase, plan)
        final_inventory = amendments[label]["observation"]["final_inventory"]
        _require([item["id"] for item in final_inventory["promises"]] == original_ids,
                 "an authored plan removed or reordered a promise")
        inventory_amendment.save_report(amendments[label], workspace / (label + "-amendment.json"))
        _write_json(workspace / (label + "-inventory.json"), final_inventory)
        final_coverage[label] = inventory_amendment.export_coverage(amendments[label])
        coverage.save_report(final_coverage[label], workspace / (label + "-coverage.json"))
    _require(amendments["revised-quote"]["observation"]["state"] == "current_for_human_review",
             "explicitly revised matching descriptor did not reach human consideration")
    _require(all(amendments[label]["observation"]["state"] == "blocked"
                 for label in ("pending", "reaffirm-old-quote", "wrong-unit")),
             "a pending, absent-quote or wrong-unit descriptor was presented as ready")
    _require(final_coverage["revised-quote"]["observation"]["counts"]["covered"] == 2,
             "the correctly revised inventory did not cover both declared promises")
    proposals = {label: store.proposal(item["proposal_id"]) for label, item in authors.items()}
    proposals.update({"composite-" + stage: store.proposal(item["proposal_id"])
                      for stage, item in compositions.items()})
    _require(all(item["status"] == "proposed" for item in proposals.values()), "a file proposal was accepted")
    journal_snapshot = journal.export_snapshot()
    _write_json(workspace / "journal.json", journal_snapshot)
    _write_json(workspace / "proposals.json", proposals)
    _write_json(workspace / "compositions.json", compositions)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin", "workspace": str(workspace),
        "source_import": "source-import.json", "upstream_commit": source_import["peeled_commit"],
        "concurrency": {"worker_pool_size": workers, "submitted_author_jobs": len(authors),
                        "submitted_editorial_review_jobs": len(editorial_reviews),
                        "author_ids": sorted({item["author"] for item in authors.values()}),
                        "observed_author_threads": sorted({item["thread"] for item in authors.values()}),
                        "observed_editorial_threads": sorted({item["thread"] for item in editorial_reviews}),
                        "integrator_id": "amendment-integrator", "all_actor_kinds": ["agent"]},
        "authors": authors, "component_reviews": component_reviews, "compositions": compositions,
        "composite_reviews": composite_reviews, "editorial_reviews": editorial_reviews,
        "rebase": {"report_id": rebase["report_id"], "counts": rebase["observation"]["counts"]},
        "stages": {label: {"report_id": amendment["report_id"],
                           "state": amendment["observation"]["state"],
                           "counts": amendment["observation"]["counts"],
                           "results": amendment["observation"]["results"],
                           "blockers": amendment["observation"]["blockers"],
                           "coverage_counts": final_coverage[label]["observation"]["counts"]}
                   for label, amendment in amendments.items()},
        "final_runtime_status": store.status(),
        "summary": {"verified_upstream_snapshot_count": len(source_import["verified_upstream_snapshots"]),
                    "imported_input_file_count": len(source_import["imported_input_files"]),
                    "component_proposal_count": len(authors), "composite_proposal_count": len(compositions),
                    "review_set_count": len(review_sets), "obligation_report_count": len(obligation_reports),
                    "coverage_report_count": 1 + len(final_coverage), "rebase_plan_count": 1,
                    "amendment_report_count": len(amendments), "listed_promise_count": len(original_ids),
                    "packet_count": len(journal_snapshot["packets"]), "decision_count": len(journal_snapshot["decisions"]),
                    "journal_event_count": len(journal_snapshot["events"]),
                    "all_original_promises_preserved": True, "semantic_equivalence_proved": False,
                    "upstream_changed": False, "file_proposals_remain_unaccepted": True,
                    "human_review_recorded": False, "auto_accept": False, "release_created": False},
        "limitations": [
            "The two-promise inventory and selected-literal adapter are authored manually; page completeness and source entailment are not established.",
            "The local scenario rewords one adapter quote; it does not demonstrate an upstream urllib3 change or migration.",
            "Explicit revision is a new authored descriptor, not an automatic proof of semantic equivalence between old and new prose.",
            "Programmed editorial decisions concern supplied selected default-method metadata only, not total wording or whole-file semantics.",
            "Actor names and kinds are self-declared; two reviewers are not authenticated people or independent models.",
            "Upstream Python is read and hashed; selected adapter literals are AST-parsed as data. Neither is imported or executed; no external LLM is used.",
            "No file proposal is accepted, no runtime revision is advanced by acceptance, and no release is created.",
            "Captured runtime and journal observations are sequential, not a common transaction or live-state check.",
            "Worker-pool size is not measured capacity, throughput or independent agent quality.",
        ],
    }
    _write_json(workspace / "report.json", report)
    labels = {"pending": "Авторских действий пока нет", "reaffirm-old-quote": "Попытка сохранить отсутствующую цитату",
              "revised-quote": "Явно задана новая цитата; единица retries сохранена", "wrong-unit": "Неверная область: attempts вместо retries"}
    lines = [
        "# Выполненный пример явного пересмотра описи", "",
        "Два программных автора подготовили три вклада для локального адаптера urllib3 2.5.0;",
        "интегратор создал два составных предложения. Все пять файловых предложений остались непринятыми.", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin); технические события используют UTC.", "",
        "## Основания и изменение", "",
        "Три сохранённых upstream-снимка сверены с manifest по SHA-256 и размеру. Семь входных файлов",
        "скопированы побайтно в `source-inputs/` и привязаны хешами в [source-import.json](source-import.json).",
        "Исходная библиотека не изменялась; работа выполнена с авторским литеральным адаптером.", "",
        "В новом кандидате переформулирована только цитата о `Retry.total`. Выбранные поля документа",
        "и кода сохранили значение 10 и область `retries`. Оба обязательства имеют `match`,",
        "однако прежняя цитата отсутствует, поэтому перенос оставляет обещание неразрешённым.", "",
        "## Наблюдавшиеся планы", "",
        "| Авторское действие | Состояние сохранённого отчёта | Обещаний в описи |",
        "|---|---|---:|",
    ]
    for label, amendment in amendments.items():
        lines.append(f"| {labels[label]} | `{amendment['observation']['state']}` | {len(amendment['observation']['final_inventory']['promises'])} |")
    lines += ["", "Корректная правка достигает только `current_for_human_review`. Она не доказывает",
              "смысловую эквивалентность прозы и не записывает человеческое решение. Неверная единица",
              "не совпадает с областью обязательства, хотя числовые литералы по-прежнему равны 10.", "",
              "Во всех планах сохранены два исходных ID и их порядок. Неподтверждённые записи остаются",
              "видимыми. Решения редакторов привязаны к точным пакетам и не превращаются в разрешение принятия.", "",
              "## Материалы", "",
              "- [Полный отчёт](report.json), [предложения](proposals.json), [композиции](compositions.json).",
              "- [Редакторский журнал](journal.json), [исходное покрытие](base-coverage.json), [план переноса](reworded-rebase.json).",
              "- [Исходный комплект](base-review-set.json), [исходные обязательства](base-obligations.json).",
              "- [Новый комплект](reworded-review-set.json), [новые обязательства](reworded-obligations.json)."]
    for label in amendments:
        lines.append(f"- `{label}`: [действия]({label}-changes.json), [отчёт]({label}-amendment.json), [опись]({label}-inventory.json), [покрытие]({label}-coverage.json).")
    lines += ["", "## Границы вывода", "",
              "Опись и привязка прозы к полям составлены вручную. Два пункта не означают полноту документации.",
              "Редакторские поля здесь описывают только выбранный набор методов; они не проверяют весь текст",
              "или поведение Retry. Участники — программные роли с самозаявленными именами; LLM не подключены.",
              "Исходник и адаптер не импортируются и не выполняются. Человеческая экспертиза не имитируется,",
              "принятие и выпуск отсутствуют. Сохранённый отчёт не наблюдает живую базу.", "",
              f"Пул: {workers}; авторских заданий: {len(authors)}; редакторских заданий: {len(editorial_reviews)}.",
              "Размер пула не является измеренной ёмкостью сервиса или числом независимых моделей.", ""]
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return report
