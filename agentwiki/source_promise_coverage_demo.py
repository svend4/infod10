"""Demonstrate declared promises over existing captured source-adapter reports.

The parent reports, their Python texts and the new inventories are data. Ten
ordinary analyze/show workflows create separate advisory coverage reports;
no earlier demonstration, database, credential or input Python is executed.
"""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo

from . import source_adapter, source_promise_coverage
from .store import canonical


RULES_VERSION = "source-promise-coverage-demo-1"
COMMAND = "source-promise-coverage"
METHODS_QUOTE = "Набор Retry.DEFAULT_ALLOWED_METHODS содержит HEAD, GET, PUT, DELETE, OPTIONS и TRACE; POST в нём отсутствует."
TOTAL_QUOTE = "У конструктора Retry значение total по умолчанию равно 10; параметр считает повторы, а не все попытки."
REQUIREMENT_FIELDS = ("scope", "value_type", "source_selector", "adapter_symbol", "document_selector")
PARENT_FILES = {
    "baseline": "inputs/source-baseline.json",
    "adapter-total-11": "inputs/source-adapter-total-11.json",
    "mixed-unresolved-and-mismatch": "inputs/source-mixed-unresolved-and-mismatch.json",
}
CASE_DEFINITIONS = [
    {"id": "baseline", "title": "Два явно объявленных обещания связаны с совпавшими значениями", "parent": "baseline", "mutation": "baseline",
     "expected_counts": {"total": 2, "assigned": 2, "mapped": 2, "anchor_verified": 2, "covered": 2, "matching": 2, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 2, "covered_unique_requirements": 2, "assigned_mapping_ids": 2, "mapped_mapping_ids": 2},
     "expected_state": "current_for_human_review", "expected_fraction": {"numerator": 2, "denominator": 2}, "expected_unique_fraction": {"numerator": 2, "denominator": 2}},
    {"id": "uncovered-total", "title": "Обещание total сохранено без назначенной связи", "parent": "baseline", "mutation": "uncovered-total",
     "expected_counts": {"total": 2, "assigned": 1, "mapped": 1, "anchor_verified": 2, "covered": 1, "matching": 1, "mismatch": 0, "unresolved": 0, "uncovered": 1, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 2, "covered_unique_requirements": 1, "assigned_mapping_ids": 1, "mapped_mapping_ids": 1},
     "expected_state": "blocked", "expected_fraction": {"numerator": 1, "denominator": 2}, "expected_unique_fraction": {"numerator": 1, "denominator": 2}},
    {"id": "invalid-requirements", "title": "Неизвестная ссылка и изменённая scope не создают покрытия", "parent": "baseline", "mutation": "invalid-requirements",
     "expected_counts": {"total": 2, "assigned": 2, "mapped": 0, "anchor_verified": 2, "covered": 0, "matching": 0, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 2, "unresolved_anchor": 0, "unique_requirements": 2, "covered_unique_requirements": 0, "assigned_mapping_ids": 2, "mapped_mapping_ids": 0},
     "expected_state": "blocked", "expected_fraction": {"numerator": 0, "denominator": 2}, "expected_unique_fraction": {"numerator": 0, "denominator": 2}},
    {"id": "missing-quote", "title": "Отсутствующая цитата оставляет обещание без anchor", "parent": "baseline", "mutation": "missing-quote",
     "expected_counts": {"total": 2, "assigned": 2, "mapped": 2, "anchor_verified": 1, "covered": 1, "matching": 1, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 1, "unique_requirements": 2, "covered_unique_requirements": 1, "assigned_mapping_ids": 2, "mapped_mapping_ids": 2},
     "expected_state": "blocked", "expected_fraction": {"numerator": 1, "denominator": 2}, "expected_unique_fraction": {"numerator": 1, "denominator": 2}},
    {"id": "ambiguous-quote", "title": "Повторяющаяся цитата не выбирается по первому совпадению", "parent": "baseline", "mutation": "ambiguous-quote",
     "expected_counts": {"total": 2, "assigned": 2, "mapped": 2, "anchor_verified": 1, "covered": 1, "matching": 1, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 1, "unique_requirements": 2, "covered_unique_requirements": 1, "assigned_mapping_ids": 2, "mapped_mapping_ids": 2},
     "expected_state": "blocked", "expected_fraction": {"numerator": 1, "denominator": 2}, "expected_unique_fraction": {"numerator": 1, "denominator": 2}},
    {"id": "fenced-quote", "title": "Уникальный фрагмент внутри JSON fence не принят за внешний anchor", "parent": "baseline", "mutation": "fenced-quote",
     "expected_counts": {"total": 2, "assigned": 2, "mapped": 2, "anchor_verified": 1, "covered": 1, "matching": 1, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 1, "unique_requirements": 2, "covered_unique_requirements": 1, "assigned_mapping_ids": 2, "mapped_mapping_ids": 2},
     "expected_state": "blocked", "expected_fraction": {"numerator": 1, "denominator": 2}, "expected_unique_fraction": {"numerator": 1, "denominator": 2}},
    {"id": "shared-duplicates", "title": "Повтор требования и общий mapping показаны отдельно", "parent": "baseline", "mutation": "shared-duplicates",
     "expected_counts": {"total": 3, "assigned": 3, "mapped": 3, "anchor_verified": 3, "covered": 3, "matching": 3, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 2, "covered_unique_requirements": 2, "assigned_mapping_ids": 2, "mapped_mapping_ids": 2},
     "expected_state": "current_for_human_review", "expected_fraction": {"numerator": 3, "denominator": 3}, "expected_unique_fraction": {"numerator": 2, "denominator": 2}},
    {"id": "empty", "title": "Пустая опись блокируется без доли покрытия", "parent": "baseline", "mutation": "empty",
     "expected_counts": {"total": 0, "assigned": 0, "mapped": 0, "anchor_verified": 0, "covered": 0, "matching": 0, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 0, "covered_unique_requirements": 0, "assigned_mapping_ids": 0, "mapped_mapping_ids": 0},
     "expected_state": "blocked", "expected_fraction": None, "expected_unique_fraction": None},
    {"id": "omitted-parent-mismatch", "title": "Полное покрытие выбранного обещания сохраняет чужой parent blocker", "parent": "adapter-total-11", "mutation": "omitted-parent-mismatch",
     "expected_counts": {"total": 1, "assigned": 1, "mapped": 1, "anchor_verified": 1, "covered": 1, "matching": 1, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 1, "covered_unique_requirements": 1, "assigned_mapping_ids": 1, "mapped_mapping_ids": 1},
     "expected_state": "blocked", "expected_fraction": {"numerator": 1, "denominator": 1}, "expected_unique_fraction": {"numerator": 1, "denominator": 1}},
    {"id": "mixed-parent-unresolved", "title": "Source unresolved сохраняет известный adapter/document mismatch", "parent": "mixed-unresolved-and-mismatch", "mutation": "mixed-parent-unresolved",
     "expected_counts": {"total": 2, "assigned": 2, "mapped": 2, "anchor_verified": 2, "covered": 2, "matching": 1, "mismatch": 0, "unresolved": 1, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 2, "covered_unique_requirements": 2, "assigned_mapping_ids": 2, "mapped_mapping_ids": 2},
     "expected_state": "blocked", "expected_fraction": {"numerator": 2, "denominator": 2}, "expected_unique_fraction": {"numerator": 2, "denominator": 2}},
]
DATA_FILES = ["source-import.json", "case-observations.json", "cli-outcomes.json", "offline-observations.json", "output-collision.json"]
EXPECTED_SUMMARY = {
    "case_count": 10, "parent_report_count": 3, "new_inventory_count": 10,
    "aggregate_counts": {"total": 18, "assigned": 17, "mapped": 15, "anchor_verified": 15, "covered": 12, "matching": 11, "mismatch": 0, "unresolved": 1, "uncovered": 1, "invalid_mapping": 2, "unresolved_anchor": 3, "unique_requirements": 17, "covered_unique_requirements": 11, "assigned_mapping_ids": 16, "mapped_mapping_ids": 14},
    "case_states": {"current_for_human_review": 2, "blocked": 8},
    "coverage_states": {"declared_promises_covered": 4, "declared_coverage_incomplete": 5, "no_declared_promises": 1},
    "reference_counts": {"total": 17, "valid": 15, "invalid": 2, "unknown": 1},
    "reference_comparison_states": {"match": 15, "mismatch": 0, "unresolved": 1, "unavailable": 1},
    "reference_pair_states": {"match": 45, "mismatch": 1, "unresolved": 2},
    "upstream_comparison_states": {"match": 18, "mismatch": 1, "unresolved": 1},
    "upstream_pair_states": {"match": 55, "mismatch": 3, "unresolved": 2},
    "upstream_blocker_count": 2, "local_blocker_count": 8, "total_blocker_count": 10,
    "shared_mapping_group_count": 1, "duplicate_requirement_group_count": 1,
    "unknown_mapping_id_instance_count": 1, "unmapped_mapping_id_instance_count": 6,
    "new_cli_child_process_count": 22, "new_successful_cli_count": 21, "new_refused_cli_count": 1,
    "analyze_command_count": 11, "show_command_count": 11, "unique_cli_process_count": 22,
    "public_file_count": 31, "public_json_file_count": 29, "public_markdown_file_count": 1, "public_license_file_count": 1,
    "parent_reports_byte_identical_to_previous_example": True, "license_bytes_match_corpus": True,
    "previous_source_example_bytes_unchanged": True, "all_parent_reports_validated_offline": True,
    "new_inventory_explicitly_authored": True, "legacy_inventory_converted": False,
    "all_upstream_blockers_preserved": True, "all_upstream_comparisons_preserved": True,
    "known_pair_mismatch_preserved": True, "full_coverage_parent_mismatch_still_blocked": True,
    "fenced_quote_snippet_and_location_retained": True, "shared_mapping_is_not_independent_evidence": True,
    "empty_inventory_fractions_are_null": True, "report_roundtrip_summaries_equal": True,
    "historical_show_with_inputs_offline": True, "offline_inputs_restored": True,
    "output_collision_refused": True, "output_collision_report_bytes_and_metadata_unchanged": True,
    "upstream_code_executed": False, "proposed_code_executed": False, "store_writes_performed": False,
    "human_review_recorded": False, "external_llm_used": False, "live_upstream_refetched": False,
    "independent_human_annotation": False, "benchmark": False, "measured_capacity": False, "process_overlap_measured": False,
    "private_credentials_created": 0, "database_files_created": 0,
    "requires_human_review": True, "auto_accept": False, "live_state_checked": False,
    "runtime_binding_verified": False, "prose_semantics_verified": False, "atomic_capture": False,
}
LIMITS = [
    "The denominator is a newly and explicitly authored inventory of selected promises, not an automatically inferred complete documentation contract.",
    "Exact quotes outside the supported fenced-block grammar and exact selector metadata bind locations; they do not establish prose semantics or semantic entailment.",
    "Coverage, comparison satisfaction and all upstream blockers remain separate. A fully covered selected inventory cannot remove an unreferenced parent mismatch or unresolved result.",
    "Every known reference and every upstream mapping retains its full Comparison4, including an adapter/document mismatch while source comparisons are unresolved.",
    "Repeated requirements and shared mapping IDs are shown as groups, without claiming independent checks, reviewers or evidence.",
    "Ten inventories are authored illustrations bound to three unchanged captured source-adapter reports; no legacy inventory is silently converted or rewritten.",
    "Historical show reconstructs its own full captured report while the input directory is absent. This does not establish current source freshness or an atomic cross-file capture.",
    "No input Python, database, credential, Store acceptance, external LLM or parallel worker pool is created or executed. Process counts are not capacity, throughput or semantic-quality measurements.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Source-promise-coverage demonstration stopped: " + message)


def _json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_metadata(path: Path) -> tuple[int, ...]:
    value = path.lstat()
    _require(stat.S_ISREG(value.st_mode) and not path.is_symlink(), "a report is not a regular file")
    return tuple(getattr(value, field) for field in ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _public_snapshot(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.rglob("*")):
        _require(not path.is_symlink(), "the previous public example contains an unsupported symlink")
        if path.is_dir():
            continue
        _require(stat.S_ISREG(path.lstat().st_mode), "the previous public example contains a nonregular file")
        raw = path.read_bytes()
        records.append({"path": path.relative_to(directory).as_posix(), "byte_count": len(raw), "sha256": _hash(raw)})
    return records


def _inventory(parent: dict[str, Any], mutation: str) -> dict[str, Any]:
    """Author the ten declared fixtures explicitly; no old inventory is read."""
    mappings = {row["id"]: row for row in parent["captured"]["profile"]["mappings"]}
    _require(set(mappings) == {"retry-methods", "retry-total"}, "the selected parent profile differs")
    descriptions = {
        "retry-methods": "Selected declaration for the documented default method set; not a universal POST retry rule.",
        "retry-total": "Selected constructor total default; retries-versus-attempts meaning remains authored prose metadata.",
    }
    promises = [
        {"id": mapping_id + "-promise", "description": descriptions[mapping_id], "anchor": {"quote": quote},
         "requirement": {key: deepcopy(mappings[mapping_id][key]) for key in REQUIREMENT_FIELDS}, "mapping_ids": [mapping_id]}
        for mapping_id, quote in (("retry-methods", METHODS_QUOTE), ("retry-total", TOTAL_QUOTE))
    ]
    if mutation == "uncovered-total":
        promises[1]["mapping_ids"] = []
    elif mutation == "invalid-requirements":
        promises[0]["mapping_ids"] = ["unknown-id"]
        promises[1]["requirement"]["scope"] = "Authored different scope; exact metadata must not match the selected parent requirement."
    elif mutation == "missing-quote":
        promises[1]["anchor"]["quote"] = "Авторская отсутствующая цитата: этот текст не содержится в захваченном документе."
    elif mutation == "ambiguous-quote":
        promises[1]["anchor"]["quote"] = "Retry"
    elif mutation == "fenced-quote":
        promises[1]["anchor"]["quote"] = '"total_default": 10'
    elif mutation == "shared-duplicates":
        repeated = deepcopy(promises[1])
        repeated["id"] = "retry-total-copy"
        repeated["description"] = "A second authored promise label for the same exact requirement and shared mapping; no independent evidence."
        promises.append(repeated)
    elif mutation == "empty":
        promises = []
    elif mutation == "omitted-parent-mismatch":
        promises = promises[:1]
    else:
        _require(mutation in {"baseline", "mixed-parent-unresolved"}, "unknown fixed inventory fixture")
    return {"schema_version": 1, "source_report_id": parent["report_id"],
            "source_report_digest": parent["report_digest"], "promises": promises}


def run_source_promise_coverage_demo(workspace: str | Path) -> dict[str, Any]:
    """Run ten ordinary coverage workflows over three already captured parents."""
    requested = Path(workspace).expanduser()
    _require(not requested.is_symlink(), "workspace must not be a symlink")
    workspace = requested.resolve()
    _require(not workspace.exists() or workspace.is_dir() and not any(workspace.iterdir()), "workspace must be new and empty")
    workspace.mkdir(parents=True, exist_ok=True)
    inputs, reports = workspace / "inputs", workspace / "reports"
    inputs.mkdir()
    reports.mkdir()
    repository = Path(__file__).resolve().parents[1]
    previous = repository / "examples/source-adapter/results"
    previous_before = _public_snapshot(previous)
    license_source = repository / "corpora/urllib3-retry-v2.5.0/upstream/LICENSE.txt"
    license_raw = license_source.read_bytes()
    parents, parent_imports = {}, []
    for name, relative in PARENT_FILES.items():
        original = previous / "reports" / (name + ".json")
        raw = original.read_bytes()
        with (workspace / relative).open("xb") as stream:
            stream.write(raw)
        _require((workspace / relative).read_bytes() == raw, "a parent report copy differs from its saved public source")
        parent = source_adapter.load_report(workspace / relative)
        parents[name] = parent
        parent_imports.append({"name": name, "source": "examples/source-adapter/results/reports/" + name + ".json",
                               "path": relative, "byte_count": len(raw), "sha256": _hash(raw),
                               "report_id": parent["report_id"], "report_digest": parent["report_digest"]})
    with (inputs / "LICENSE.txt").open("xb") as stream:
        stream.write(license_raw)
    baseline_document = parents["baseline"]["captured"]["texts"]["document"]
    _require(baseline_document.count(METHODS_QUOTE) == baseline_document.count(TOTAL_QUOTE) == 1
             and baseline_document.count("Retry") > 1 and baseline_document.count('"total_default": 10') == 1,
             "the fixed quote examples no longer describe the captured document")
    _require(all(parent["captured"]["texts"]["document"] == baseline_document
                 and parent["captured"]["profile"] == parents["baseline"]["captured"]["profile"] for parent in parents.values()),
             "the saved parent reports do not share the expected selected document and profile")
    inventories = {}
    for case in CASE_DEFINITIONS:
        inventory = _inventory(parents[case["parent"]], case["mutation"])
        inventories[case["id"]] = inventory
        _json(inputs / ("inventory-" + case["id"] + ".json"), inventory)
    input_files = sorted([*PARENT_FILES.values(), *("inputs/inventory-" + case["id"] + ".json" for case in CASE_DEFINITIONS), "inputs/LICENSE.txt"])
    _require(len(input_files) == 14, "the public input tree differs")
    input_pins = [{"path": relative, "byte_count": (workspace / relative).stat().st_size,
                   "sha256": _hash((workspace / relative).read_bytes())} for relative in input_files]
    cli_outcomes: list[dict[str, Any]] = []

    def cli(label: str, action: str, arguments: list[str], *, case_id: str, expected_error: str | None = None) -> Any:
        with subprocess.Popen([sys.executable, "-m", "agentwiki", COMMAND, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            try:
                stdout, stderr = child.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                child.kill()
                child.communicate(timeout=15)
                raise RuntimeError("Source-promise-coverage demonstration stopped: ordinary CLI timed out") from error
        found = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
        error_code = found.group(1) if found else None
        cli_outcomes.append({"label": label, "command": COMMAND, "action": action, "case_id": case_id,
                             "child_process_id": child.pid, "returncode": child.returncode, "error_code": error_code,
                             "stderr_empty": not stderr, "completed_child_process": True, "shell": False})
        if expected_error is not None:
            _require(child.returncode == 1 and error_code == expected_error and not stdout,
                     "ordinary CLI did not return the expected safe refusal")
            return {"label": label, "code": error_code, "rejected": True}
        _require(child.returncode == 0 and not stderr, "ordinary CLI failed")
        def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
            value: dict[str, Any] = {}
            for key, item in items:
                _require(key not in value, "CLI response contained a duplicate JSON key")
                value[key] = item
            return value
        def reject(_value: str) -> None:
            raise ValueError("Unsupported non-finite CLI value")
        return json.loads(stdout, object_pairs_hook=pairs, parse_constant=reject)

    def analyze_arguments(case: dict[str, Any], target: Path) -> list[str]:
        return ["--source-report", str(workspace / PARENT_FILES[case["parent"]]), "--inventory",
                str(inputs / ("inventory-" + case["id"] + ".json")), "--output", str(target)]

    observations: list[dict[str, Any]] = []
    aggregate, states, coverage_states = Counter(), Counter(), Counter()
    reference_counts, reference_states, reference_pairs = Counter(), Counter(), Counter()
    upstream_states, upstream_pairs = Counter(), Counter()
    upstream_blockers = local_blockers = shared_groups = duplicate_groups = unknown_instances = unmapped_instances = 0
    baseline_summary = None
    for case in CASE_DEFINITIONS:
        target = reports / (case["id"] + ".json")
        analyzed = cli(case["id"] + "-analyze", "analyze", analyze_arguments(case, target), case_id=case["id"])
        shown = cli(case["id"] + "-show", "show", ["--input", str(target)], case_id=case["id"])
        full = source_promise_coverage.load_report(target)
        _require(canonical(analyzed) == canonical(shown) == canonical(source_promise_coverage.summary(full)),
                 "analyze/show changed the exact captured coverage summary")
        parent = parents[case["parent"]]
        expected_upstream = [{"mapping_id": row["id"], "comparison": row["comparison"]} for row in parent["observation"]["results"]]
        _require(analyzed["counts"] == case["expected_counts"] and analyzed["state"] == case["expected_state"]
                 and analyzed["coverage_fraction"] == case["expected_fraction"]
                 and analyzed["unique_requirement_fraction"] == case["expected_unique_fraction"]
                 and analyzed["upstream_blockers"] == parent["observation"]["blockers"]
                 and analyzed["upstream_state"] == parent["observation"]["state"]
                 and analyzed["upstream_comparisons"] == expected_upstream
                 and analyzed["blockers"][:len(analyzed["upstream_blockers"])] == analyzed["upstream_blockers"],
                 "fixed coverage counts, fractions or complete upstream evidence changed")
        _require(analyzed["requires_human_review"] is True and all(analyzed[key] is False for key in
                 ("auto_accept", "live_state_checked", "runtime_binding_verified", "prose_semantics_verified", "atomic_capture")),
                 "advisory coverage flags changed")
        aggregate.update(analyzed["counts"])
        states[analyzed["state"]] += 1
        coverage_states[analyzed["coverage_state"]] += 1
        upstream_blockers += len(analyzed["upstream_blockers"])
        local_blockers += len(analyzed["blockers"]) - len(analyzed["upstream_blockers"])
        shared_groups += len(analyzed["shared_mappings"])
        duplicate_groups += len(analyzed["duplicate_requirements"])
        unknown_instances += len(analyzed["unknown_mapping_ids"])
        unmapped_instances += len(analyzed["unmapped_mapping_ids"])
        for upstream in analyzed["upstream_comparisons"]:
            comparison = upstream["comparison"]
            upstream_states[comparison["state"]] += 1
            upstream_pairs.update(comparison[key] for key in ("source_adapter", "source_document", "adapter_document"))
        for row in analyzed["results"]:
            for reference in row["references"]:
                reference_counts["total"] += 1
                reference_counts["valid" if reference["valid"] else "invalid"] += 1
                comparison = reference["comparison"]
                reference_states[comparison["state"] if comparison is not None else "unavailable"] += 1
                if comparison is None:
                    reference_counts["unknown"] += 1
                else:
                    reference_pairs.update(comparison[key] for key in ("source_adapter", "source_document", "adapter_document"))
        if case["id"] == "baseline":
            baseline_summary = analyzed
        elif case["id"] == "fenced-quote":
            anchor = analyzed["results"][1]["anchor"]
            _require(anchor["resolved"] is False and anchor["outside_fenced_blocks"] is False
                     and anchor["snippet"] == '"total_default": 10' and anchor["location"] is not None
                     and anchor["reasons"] == [{"code": "quote_in_fenced_block"}], "fenced quote lost its partial evidence")
        elif case["id"] == "shared-duplicates":
            _require(len(analyzed["shared_mappings"]) == len(analyzed["duplicate_requirements"]) == 1,
                     "shared mapping or duplicate requirement was hidden")
        elif case["id"] == "omitted-parent-mismatch":
            _require(analyzed["coverage_fraction"] == {"numerator": 1, "denominator": 1}
                     and analyzed["state"] == "blocked" and analyzed["upstream_comparisons"][1]["comparison"]["state"] == "mismatch",
                     "selected full coverage removed an unreferenced parent mismatch")
        elif case["id"] == "mixed-parent-unresolved":
            comparison = analyzed["results"][1]["references"][0]["comparison"]
            _require(comparison == {"source_adapter": "unresolved", "source_document": "unresolved", "adapter_document": "mismatch", "state": "unresolved"}
                     and analyzed["upstream_comparisons"][1]["comparison"] == comparison,
                     "parent source unresolved hid a known adapter/document mismatch")
        observations.append({**case, "source_report": PARENT_FILES[case["parent"]],
                             "inventory": "inputs/inventory-" + case["id"] + ".json", "report": "reports/" + case["id"] + ".json",
                             "analyze": analyzed, "show": shown, "captured_summary_verified": True})

    _require(baseline_summary is not None, "baseline summary was not retained")
    baseline_report = reports / "baseline.json"
    original_bytes, original_metadata = baseline_report.read_bytes(), _file_metadata(baseline_report)
    hidden = workspace / ".source-promise-coverage-offline-inputs"
    _require(not hidden.exists(), "a temporary offline input directory already exists")
    try:
        inputs.rename(hidden)
        offline_summary = cli("offline-baseline-show", "show", ["--input", str(baseline_report)], case_id="baseline")
        _require(not inputs.exists() and canonical(offline_summary) == canonical(baseline_summary)
                 and baseline_report.read_bytes() == original_bytes and _file_metadata(baseline_report) == original_metadata,
                 "historical show needed live inputs or changed its own report")
    finally:
        if hidden.exists():
            hidden.rename(inputs)
    _require(inputs.is_dir() and not hidden.exists(), "offline inputs did not return")
    collision = cli("baseline-output-collision", "analyze", analyze_arguments(CASE_DEFINITIONS[0], baseline_report),
                    case_id="baseline", expected_error="source_promise_coverage_io_failed")
    _require(baseline_report.read_bytes() == original_bytes and _file_metadata(baseline_report) == original_metadata,
             "refused output collision changed the original report")
    _require(_public_snapshot(previous) == previous_before and license_source.read_bytes() == license_raw,
             "a previous public example or license changed")
    _require(input_pins == [{"path": relative, "byte_count": (workspace / relative).stat().st_size,
                            "sha256": _hash((workspace / relative).read_bytes())} for relative in input_files],
             "an input report or authored inventory changed during the ordinary workflow")
    measured = {
        "aggregate_counts": dict(aggregate), "case_states": dict(states), "coverage_states": dict(coverage_states),
        "reference_counts": dict(reference_counts),
        "reference_comparison_states": {key: reference_states[key] for key in ("match", "mismatch", "unresolved", "unavailable")},
        "reference_pair_states": dict(reference_pairs), "upstream_comparison_states": dict(upstream_states),
        "upstream_pair_states": dict(upstream_pairs), "upstream_blocker_count": upstream_blockers,
        "local_blocker_count": local_blockers, "total_blocker_count": upstream_blockers + local_blockers,
        "shared_mapping_group_count": shared_groups, "duplicate_requirement_group_count": duplicate_groups,
        "unknown_mapping_id_instance_count": unknown_instances, "unmapped_mapping_id_instance_count": unmapped_instances,
    }
    _require(all(measured[key] == EXPECTED_SUMMARY[key] for key in measured)
             and len(cli_outcomes) == len({row["child_process_id"] for row in cli_outcomes}) == 22
             and sum(row["returncode"] == 0 for row in cli_outcomes) == 21,
             "case, reference, upstream, group or child-process accounting differs")

    source_import = {"schema_version": 1, "kind": "explicit_source_promise_coverage_demonstration",
                     "parents": parent_imports, "input_pins": input_pins, "previous_public_files": previous_before,
                     "license": {"source": "corpora/urllib3-retry-v2.5.0/upstream/LICENSE.txt", "path": "inputs/LICENSE.txt",
                                 "byte_count": len(license_raw), "sha256": _hash(license_raw)},
                     "parent_reports_byte_identical": True, "previous_public_files_unchanged": True,
                     "new_inventories_explicitly_authored": True, "legacy_inventory_converted": False,
                     "parent_source_adapter_demo_rerun": False, "upstream_code_executed": False,
                     "live_upstream_refetched": False, "independent_human_annotation": False, "benchmark": False}
    offline_observations = {"case_id": "baseline", "summary": offline_summary, "input_directory_absent_during_show": True,
                            "all_parent_reports_and_inventories_absent": True, "own_report_online": True,
                            "own_report_bytes_and_metadata_unchanged": True, "all_inputs_restored": True,
                            "live_sources_checked": False}
    collision_observation = {"refusal": collision, "target": "reports/baseline.json", "existing_report_bytes_and_metadata_unchanged": True,
                             "report_overwritten": False, "partial_report_repair_performed": False}
    for name, value in zip(DATA_FILES, (source_import, observations, cli_outcomes, offline_observations, collision_observation)):
        _json(workspace / name, value)
    public_files = sorted([*input_files, *("reports/" + case["id"] + ".json" for case in CASE_DEFINITIONS), *DATA_FILES, "report.json", "report.md"])
    _require(len(public_files) == len(set(public_files)) == 31
             and {path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file()}
             == set(public_files) - {"report.json", "report.md"}, "the public file tree differs before final report")
    report = {"schema_version": 1, "rules_version": RULES_VERSION,
              "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
              "scenario": "Explicit promise coverage over three unchanged captured source-adapter reports",
              "summary": dict(EXPECTED_SUMMARY),
              "cases": [{"id": case["id"], "title": case["title"], "report": "reports/" + case["id"] + ".json",
                         "state": case["expected_state"], "counts": case["expected_counts"],
                         "coverage_fraction": case["expected_fraction"], "unique_requirement_fraction": case["expected_unique_fraction"]}
                        for case in CASE_DEFINITIONS], "artifacts": public_files, "limits": LIMITS}
    _json(workspace / "report.json", report)
    lines = [
        "# Объявленные обещания и проверяемое происхождение выбранных значений", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Полные coverage reports используют UTC.", "",
        "Новая явная опись связывается с точными ID/digest полного source-adapter report. "
        "[Запись происхождения](source-import.json) фиксирует три неизменённых родительских отчёта и отдельные авторские inventories.", "",
        "Родительские inputs: [baseline](inputs/source-baseline.json), [adapter total=11](inputs/source-adapter-total-11.json), "
        "[mixed unresolved](inputs/source-mixed-unresolved-and-mismatch.json), [baseline inventory](inputs/inventory-baseline.json), "
        "[лицензия MIT](inputs/LICENSE.txt).", "",
        "## Десять обычных случаев", "", "| Случай | Covered / total | Matching | Unresolved | State |", "|---|---:|---:|---:|---|",
    ]
    for case in report["cases"]:
        counts = case["counts"]
        lines.append(f"| [{case['id']}]({case['report']}) | {counts['covered']} / {counts['total']} | {counts['matching']} | {counts['unresolved']} | {case['state']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "Два исходных обещания имеют точные уникальные цитаты вне fenced blocks и правильные полные метаданные mapping. "
        "Удалённая ссылка, неизвестный ID, изменённая scope, отсутствующая и неоднозначная цитаты не создают покрытия. "
        "Уникальный фрагмент JSON fence сохраняет snippet/location, но anchor остаётся unresolved.",
        "Повтор требования даёт 3/3 covered promises и 2/2 unique requirements; shared mapping и duplicate requirement показаны отдельно. "
        "Пустая опись блокируется с null fractions. При исключении проблемного total из описи healthy methods дают 1/1 coverage, "
        "но parent mismatch и полные upstream comparisons сохраняют blocked. Mixed source unresolved сохраняет известный adapter_document mismatch.",
        "Каждый analyze прошёл show с тем же summary. Затем inputs отсутствовали целиком: historical show пересчитал baseline "
        "по собственному полному отчёту, directory возвращена в finally. Analyze в существующий output отказал; bytes/metadata исходного report не изменились.",
        "", "## Публичные основания", "",
        "[Случаи и ответы](case-observations.json), [22 CLI outcomes](cli-outcomes.json), [чтение без inputs](offline-observations.json), "
        "[отказ перезаписи](output-collision.json), [машинный отчёт](report.json).", "",
        "## Границы", "",
        "В десяти авторских иллюстрациях 18 promise rows: 12 covered, из них 11 matching и одна unresolved. "
        "Это повторное использование выбранных обещаний и связей; сумма по случаям не является долей полноты документации. "
        "Два случая current_for_human_review, восемь blocked. В 20 upstream mapping comparisons сохранены 18 match, один mismatch и один unresolved; "
        "все 60 pair outcomes видимы: 55 match, 3 mismatch, 2 unresolved.",
        "Выполнены 22 ordinary CLI: 21 successful и один ожидаемый отказ, 22 различных PID без измерения concurrency. "
        "Публичное дерево: 31 файл — 29 JSON, один Markdown и одна лицензия. Базы, credentials, worker pool, принятие и внешние LLM не создавались.",
        "Outside_fenced_blocks относится только к ограниченной грамматике Markdown fences; HTML, inline и indented code и смысл прозы не распознаются. "
        "Точная цитата и совпадение всех селекторов не доказывают соответствие смысла текста полю, полноту описи, runtime behavior или независимость участников. "
        "Полные captured sources и inventories являются историческими данными; current freshness и atomic capture не установлены. "
        "Новые inventories составлены явно, прежние coverage-1 файлы не преобразовывались. Human review требуется; auto_accept=false.", "",
    ]
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    actual_files = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file())
    _require(actual_files == public_files and all(not (workspace / relative).is_symlink() for relative in actual_files)
             and Counter(Path(relative).suffix for relative in actual_files) == {".json": 29, ".md": 1, ".txt": 1},
             "the final public tree or suffix accounting differs")
    return report
