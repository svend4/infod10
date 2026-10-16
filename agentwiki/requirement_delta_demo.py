"""Inspect seven amendments built from pinned, previously published inputs.

The pool contains analysis workers, not new authors, editorial reviewers or
models. Stored review context is reused without opening a Store, producing
votes, accepting proposals, executing captured code or creating a release.
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

from . import coverage, inventory_amendment, inventory_rebase, obligations, requirement_delta


PUBLISHED_INPUT_COMMIT = "dd5f63b0bef5ded8f0a4b3a5f55475b70241d1a9"
SOURCE_INPUTS = [
    ("base", "examples/inventory-amendment/results/base-coverage.json",
     "2fda5ec5a9ecdc409bdc3c7d671f804f5f5285788a4795987a52d1432113e3f6", 152884),
    ("prose", "examples/inventory-amendment/results/revised-quote-amendment.json",
     "f85e400999b3dbcbd4da20c045a8a84383f4a3d0c7bdca52965fcc3d8cc02a3d", 356562),
    ("unit", "examples/inventory-amendment/results/wrong-unit-amendment.json",
     "bd18ccf09918d08ad1b4b3f04fc0e318dd561ccc28927508c0da5e99d3cd7882", 357386),
    ("changed", "examples/inventory-rebase/results/changed-rebase.json",
     "74d6b07aa14fff125179bc6f8d6cc6f51d843e51e25185fc7ea8665cb04089ac", 446484),
    ("stale", "examples/inventory-rebase/results/stale-rebase.json",
     "ad086155f6393de6955a506452c06640ba18e9a68529a2d856aa836a0d88787f", 450631),
]


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete ordinary workflow before saving its success report."""
    if not condition:
        raise RuntimeError("Requirement-delta demonstration stopped: " + message)


def _write_json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _import_sources(workspace: Path) -> dict[str, dict[str, Any]]:
    repository = Path(__file__).resolve().parents[1]
    reports, imported = {}, []
    for label, relative, expected_hash, expected_size in SOURCE_INPUTS:
        source = repository / relative
        raw = source.read_bytes()
        actual_hash = hashlib.sha256(raw).hexdigest()
        _require(actual_hash == expected_hash and len(raw) == expected_size,
                 "a pinned published input differs: " + relative)
        loader = (coverage.load_report if label == "base" else
                  inventory_amendment.load_report if label in {"prose", "unit"} else
                  inventory_rebase.load_report)
        reports[label] = loader(source)
        imported.append({
            "label": label, "repository_path": relative,
            "sha256": actual_hash, "byte_count": len(raw),
            "report_id": reports[label]["report_id"],
            "report_digest": reports[label]["report_digest"],
            "github_blob_url": ("https://github.com/svend4/infod10/blob/" +
                                PUBLISHED_INPUT_COMMIT + "/" + relative),
        })
    _write_json(workspace / "source-import.json", {
        "schema_version": 1, "published_input_commit": PUBLISHED_INPUT_COMMIT,
        "files": imported,
        "capture_mode": "previously_published_report_snapshots",
        "fresh_runtime_capture": False, "live_state_checked": False,
        "new_editorial_decisions": 0,
    })
    return reports


def _plan(rebase: dict[str, Any], actions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "schema_version": 1, "rebase_report_id": rebase["report_id"],
        "rebase_report_digest": rebase["report_digest"], "actions": actions,
    }


def _revise(descriptor: dict[str, Any], rationale: str) -> dict[str, Any]:
    return {"promise_id": descriptor["id"], "action": "revise",
            "rationale": rationale, "descriptor": descriptor}


def _amend(workspace: Path, label: str, rebase: dict[str, Any],
           actions: list[dict[str, Any]]) -> dict[str, Any]:
    changes = _plan(rebase, actions)
    _write_json(workspace / (label + "-changes.json"), changes)
    return inventory_amendment.apply_amendments(rebase, changes)


def _cases(workspace: Path, inputs: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    baseline = inputs["base"]
    source_obligations = baseline["obligation_report"]
    captured = source_obligations["review_set"]
    original_spec = source_obligations["spec"]
    original_inventory = baseline["inventory"]
    methods_id = original_inventory["promises"][0]["id"]
    methods_obligation_id = original_inventory["promises"][0]["obligation_ids"][0]

    relaxed_spec = deepcopy(original_spec)
    for item in relaxed_spec["obligations"]:
        if item["id"] == methods_obligation_id:
            item["relation"] = "code_subset_of_document"
    relaxed_obligations = obligations.analyze_obligations(captured, relaxed_spec)
    relaxed_rebase = inventory_rebase.plan_rebase(baseline, relaxed_obligations)
    relaxed_descriptor = deepcopy(original_inventory["promises"][0])
    relaxed_descriptor["requirement"]["relation"] = "code_subset_of_document"
    relaxed_descriptor["description"] = (
        "Authored requirement permits the selected code method set to be a subset of the document set; "
        "captured literals and quote remain unchanged.")
    cases = {"prose-reanchor": inputs["prose"]}
    cases["relation-relaxed"] = _amend(workspace, "relation-relaxed", relaxed_rebase, [
        _revise(relaxed_descriptor, "Author explicitly changes only the declared set relation and its description; "
                "no direction of a prose or program guarantee is established."),
    ])

    subset_inventory = deepcopy(original_inventory)
    subset_inventory["promises"][0] = deepcopy(relaxed_descriptor)
    subset_coverage = coverage.analyze_coverage(relaxed_obligations, subset_inventory)
    strengthened_rebase = inventory_rebase.plan_rebase(subset_coverage, source_obligations)
    strengthened_descriptor = deepcopy(original_inventory["promises"][0])
    strengthened_descriptor["description"] = (
        "Authored requirement restores equality between the selected code and document method sets; "
        "captured literals and quote remain unchanged.")
    cases["relation-strengthened"] = _amend(workspace, "relation-strengthened", strengthened_rebase, [
        _revise(strengthened_descriptor, "Author explicitly restores declared equality from the subset predicate "
                "over the same captured selection, without importing a previous editorial approval."),
    ])

    scoped_spec = deepcopy(original_spec)
    for item in scoped_spec["obligations"]:
        if item["id"] == methods_obligation_id:
            item["scope"]["review_frame"] = "authored-candidate"
    scoped_obligations = obligations.analyze_obligations(captured, scoped_spec)
    scoped_rebase = inventory_rebase.plan_rebase(baseline, scoped_obligations)
    scoped_descriptor = deepcopy(original_inventory["promises"][0])
    scoped_descriptor["requirement"]["scope"]["review_frame"] = "authored-candidate"
    scoped_descriptor["description"] = "Authored candidate review adds an explicit scope label to the same selected method sets."
    cases["scope-change"] = _amend(workspace, "scope-change", scoped_rebase, [
        _revise(scoped_descriptor, "Author adds a scope label; equal selected values do not establish structural equivalence."),
    ])

    fictional_rebase = inputs["changed"]
    attempt_descriptor = deepcopy(next(item for item in fictional_rebase["previous_coverage"]["inventory"]["promises"]
                                       if item["id"] == "attempt-limit-promise"))
    attempt_descriptor["anchor"]["quote"] = "Клиент делает не более 4 попыток."
    attempt_descriptor["description"] = (
        "Fictional client scenario: the declared attempt limit is now 4; this is not urllib3 Retry.total.")
    cases["fictional-attempt-limit"] = _amend(workspace, "fictional-attempt-limit", fictional_rebase, [
        _revise(attempt_descriptor, "Author explicitly updates the fictional 3-to-4 attempt limit; "
                "the document and code literals both changed, and this is unrelated to urllib3 retries."),
    ])
    cases["blocked-unit"] = inputs["unit"]
    stale_rebase = inputs["stale"]
    stale_actions = [{
        "promise_id": item["id"], "action": "reaffirm",
        "rationale": "Fictional client: restore the unchanged descriptor for inspection; "
                     "stale captured dependencies remain blocking despite matching selected values.",
    } for item in stale_rebase["observation"]["results"] if item["state"] != "reusable_descriptor"]
    cases["stale-reaffirm"] = _amend(workspace, "stale-reaffirm", stale_rebase, stale_actions)
    _require(methods_id == "retry-default-methods-promise", "unexpected published adapter method identity")
    return cases


def run_requirement_delta_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a bounded analysis workflow over seven self-contained amendments."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Requirement-delta demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    inputs = _import_sources(workspace)
    cases = _cases(workspace, inputs)
    for label, amendment in cases.items():
        old_ids = [item["id"] for item in amendment["rebase_report"]["previous_coverage"]["inventory"]["promises"]]
        new_ids = [item["id"] for item in amendment["observation"]["final_inventory"]["promises"]]
        _require(old_ids == new_ids and len(old_ids) == 2, "an authored case lost or reordered a promise")
        inventory_amendment.save_report(amendment, workspace / (label + "-amendment.json"))

    pool_size = min(workers, len(cases))
    initial_workers = Barrier(pool_size)

    def analyze(job: tuple[int, tuple[str, dict[str, Any]]]) -> tuple[str, dict[str, Any], str]:
        index, (label, amendment) = job
        if index < pool_size:
            initial_workers.wait(timeout=30)
        delta = requirement_delta.inspect_amendment(amendment)
        requirement_delta.save_report(delta, workspace / (label + "-delta.json"))
        return label, delta, current_thread().name

    with ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix="requirement-delta-analysis") as executor:
        analyzed = list(executor.map(analyze, enumerate(cases.items())))
    deltas = {label: delta for label, delta, _thread in analyzed}
    for label, delta in deltas.items():
        observed = delta["observation"]
        source = cases[label]["observation"]
        _require(observed["blockers"] == source["blockers"] and observed["state"] == source["state"],
                 "an analysis changed amendment blockers or readiness")
        _require(observed["counts"]["total"] == 2 and observed["identity_sequence_preserved"],
                 "an analysis changed the identity sequence")
    _require(deltas["relation-relaxed"]["observation"]["counts"]["declared_relations_relaxed"] == 1,
             "the declared equality-to-subset change was not represented")
    _require(deltas["relation-strengthened"]["observation"]["counts"]["declared_relations_strengthened"] == 1,
             "the declared subset-to-equality change was not represented")
    _require(deltas["scope-change"]["observation"]["counts"]["structural_changes"] == 1,
             "the scope change was not represented as structural")
    _require(deltas["fictional-attempt-limit"]["observation"]["counts"]["selected_value_changed_promises"] == 1,
             "the fictional selected-value change was not represented")
    _require(all(deltas[label]["observation"]["state"] == "current_for_human_review"
                 for label in ["prose-reanchor", "relation-relaxed", "relation-strengthened",
                               "scope-change", "fictional-attempt-limit"]),
             "a matching authored case did not reach human consideration")
    _require(all(deltas[label]["observation"]["state"] == "blocked" for label in ["blocked-unit", "stale-reaffirm"]),
             "a wrong-unit or stale case became ready")

    case_summaries = {}
    for label, delta in deltas.items():
        observed = delta["observation"]
        case_summaries[label] = {
            "domain": "fictional-client" if label in {"fictional-attempt-limit", "stale-reaffirm"} else "manual-urllib3-adapter",
            "amendment_file": label + "-amendment.json", "delta_file": label + "-delta.json",
            "changes_file": label + "-changes.json" if label not in {"prose-reanchor", "blocked-unit"} else None,
            "amendment_report_id": cases[label]["report_id"], "delta_report_id": delta["report_id"],
            "state": observed["state"], "attention_state": observed["attention_state"],
            "counts": observed["counts"], "review_attention": observed["review_attention"],
            "blockers": observed["blockers"],
            "promise_ids": [item["id"] for item in observed["results"]],
            "results": [{
                "id": item["id"], "requirement_change": item["requirement_change"],
                "relation_comparison": item["relation_comparison"],
                "document_value_change": item["document"]["selected_value_change"],
                "code_value_change": item["code"]["selected_value_change"],
                "document_dependencies_changed": item["document"]["render_dependencies_changed"],
                "code_dependencies_changed": item["code"]["render_dependencies_changed"],
                "amendment_outcome": item["amendment_outcome"],
            } for item in observed["results"]],
        }
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin", "workspace": str(workspace),
        "source_import": "source-import.json", "published_input_commit": PUBLISHED_INPUT_COMMIT,
        "concurrency": {"requested_worker_pool_size": workers, "analysis_worker_pool_size": pool_size,
                        "submitted_analysis_jobs": len(cases),
                        "observed_analysis_threads": sorted({thread for _label, _delta, thread in analyzed}),
                        "external_llm_calls": 0},
        "counts": {"source_reports": len(inputs), "case_amendments": len(cases),
                   "authored_change_plans": 5, "delta_reports": len(deltas),
                   "promises_per_case": 2,
                   "ready_for_human_review_cases": sum(item["state"] == "current_for_human_review" for item in case_summaries.values()),
                   "blocked_cases": sum(item["state"] == "blocked" for item in case_summaries.values()),
                   "new_runtime_proposals": 0, "new_editorial_decisions": 0,
                   "accepted_proposals": 0, "releases": 0},
        "cases": case_summaries,
        "requires_human_review": True, "auto_accept": False, "live_state_checked": False,
        "fresh_runtime_capture": False, "captured_code_executed": False,
        "semantic_prose_equivalence_established": False,
        "guarantee_direction_established": False,
        "performance_or_quality_measured": False,
    }
    _write_json(workspace / "report.json", report)
    labels = {
        "prose-reanchor": "Новая формулировка цитаты",
        "relation-relaxed": "equals → code_subset_of_document",
        "relation-strengthened": "code_subset_of_document → equals",
        "scope-change": "Добавлена метка области",
        "fictional-attempt-limit": "Вымышленный клиент: 3 → 4 попытки",
        "blocked-unit": "Неверная единица attempts вместо retries",
        "stale-reaffirm": "Вымышленный клиент: reaffirm при stale основании",
    }
    lines = [
        "# Анализ изменений явно заданных требований", "",
        f"Создано: `{report['created_at']}`; часовой пояс отчёта — `Europe/Berlin`.", "",
        "Прочитаны пять ранее опубликованных отчётов. Их байты и внутренние доказательства проверяются",
        "перед использованием; SHA256, размеры, ID и ссылки на закреплённый коммит находятся в",
        "[source-import.json](source-import.json). Полный отчёт: [report.json](report.json).", "",
        "Из сохранённых оснований получены семь авторских пересмотров через обычные API. Пять новых",
        "планов меняют описания, метаданные или предлагают прежние записи повторно; два пересмотра взяты из прошлого этапа.",
        "Контекст кандидатов и журнала не изменяется. Новые runtime-предложения и редакторские решения отсутствуют.", "",
        "## Наблюдавшиеся результаты", "",
        "| Случай | Область примера | Состояние | Записей с вниманием / всего |",
        "|---|---|---|---:|",
    ]
    for label, item in case_summaries.items():
        lines.append(f"| [{labels[label]}]({label}-delta.json) | `{item['domain']}` | `{item['state']}` | {item['counts']['attention_promises']} / 2 |")
    lines += [
        "", "Равенство множеств влечёт `code_subset_of_document` для одинаковых области, типа и селекторов;",
        "обратное следование не является общим законом. Это отношение между схемами предикатов над",
        "произвольными парами конечных множеств. В этих снимках выбранные множества совпадают, поэтому",
        "таблица не измеряет фактически расширенное или суженное поведение программы.", "",
        "Добавленная метка области даёт структурное изменение без выведенного направления следования.",
        "В вымышленном клиенте обе выбранные величины изменились с 3 на 4, хотя схема `equals` сохранилась.",
        "Этот сценарий не относится к urllib3 и не переименовывает её счётчик повторов в число попыток.", "",
        "Неверная единица остаётся заблокированной. В `stale-reaffirm` оба описания снова покрыты и",
        "выбранные значения совпадают, но изменившиеся зависимости и исходные блокеры сохраняют остановку.",
        "Сам анализ не добавляет и не снимает ни одного блокера исходного пересмотра.", "",
        "## Сохранённые основания", "",
    ]
    for label, item in case_summaries.items():
        change_link = f", [авторский план]({item['changes_file']})" if item["changes_file"] else ""
        lines.append(f"- `{label}`: [пересмотр]({item['amendment_file']}), [анализ]({item['delta_file']}){change_link}.")
    lines += [
        "", "Каждый анализ включает полный исходный пересмотр и его цепочку сохранённых оснований.",
        "Во всех семи случаях сохранены два исходных ID, их порядок и число записей. Это не доказывает",
        "равенства смысла обещаний или полноты описи относительно всей документации.", "",
        "## Участники и границы", "",
        f"Пул анализа: **{pool_size} Python workers**; заданий анализа: **{len(cases)}**.",
        "Наблюдавшиеся потоки: " + ", ".join("`" + name + "`" for name in report["concurrency"]["observed_analysis_threads"]) + ".", "",
        "Это работники анализа сохранённых данных. Внешние LLM, новые редакторские решения, принятие",
        "предложений и выпуск отсутствуют. Исходники и предложенный код не импортируются и не выполняются.",
        "Живая база не наблюдается; исторические решения не обновляются и не выдаются за свежую проверку.",
        "Размер пула не является измерением ёмкости сервиса, производительности, качества или независимости экспертизы.", "",
        "Изменения описания и цитаты фиксируются как различия текста. Соответствие текста выбранным полям,",
        "направление гарантии для читателя и семантическая эквивалентность прозы не устанавливаются.",
        "Все результаты требуют рассмотрения человеком и не разрешают автоматическое принятие или публикацию.", "",
    ]
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return report
