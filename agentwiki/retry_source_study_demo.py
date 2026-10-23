"""Study four pinned Retry declarations and an explicit authored prose boundary.

The existing source-adapter and promise-coverage commands do the analysis.
Captured Python remains data; the selected register is not an exhaustive
inventory, and matching values never certify the meaning of the prose.
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


RULES_VERSION = "retry-source-study-demo-1"
CORPUS = "corpora/urllib3-retry-source-study-v2.5.0"
OLD_CORPUS = "corpora/urllib3-retry-v2.5.0"
SOURCE_COMMIT = "aaab4eccc10c965897540b21e15f11859d0b62e7"
SOURCE_SHA256 = "6e3fb6614a9b9712e5bfc4c78397f1c30f83339e1709b8e0657210ef55e2a026"
LICENSE_SHA256 = "130e3a64d5fdd5d096a752694634a7d9df284469de86e5732100268041e3d686"
CORPUS_FILES = [
    "README.md", "authored/contract-misstated-total-unit.md", "authored/contract.md",
    "authored/profile.json", "authored/promise-specs.json", "authored/retry_config.py",
    "manifest.json", "selection-register.json", "upstream/LICENSE.txt", "upstream/retry.py",
]
INPUT_COPIES = {
    "inputs/source.py": "upstream/retry.py", "inputs/LICENSE.txt": "upstream/LICENSE.txt",
    "inputs/adapter.py": "authored/retry_config.py", "inputs/document.md": "authored/contract.md",
    "inputs/document-misstated-total-unit.md": "authored/contract-misstated-total-unit.md",
    "inputs/profile.json": "authored/profile.json", "inputs/promise-specs.json": "authored/promise-specs.json",
    "inputs/selection-register.json": "selection-register.json",
}
PREVIOUS_SCOPES = [
    "corpora/urllib3-retry-v2.5.0", "examples/source-adapter/results", "examples/source-promise-coverage/results",
]
MAPPING_IDS = ["retry-methods", "retry-total", "retry-remove-headers", "retry-backoff-factor"]
QUOTES = [
    "Набор Retry.DEFAULT_ALLOWED_METHODS содержит HEAD, GET, PUT, DELETE, OPTIONS и TRACE; POST в нём отсутствует.",
    "У конструктора Retry значение total по умолчанию равно 10; параметр считает повторы, а не все попытки.",
    "Набор Retry.DEFAULT_REMOVE_HEADERS_ON_REDIRECT содержит Cookie, Authorization и Proxy-Authorization; это имена в объявлении константы, до преобразования в нижний регистр.",
    "У конструктора Retry выражение по умолчанию для backoff_factor — целочисленный литерал 0, хотя параметр аннотирован как float.",
]
MISSTATED_TOTAL_QUOTE = "У конструктора Retry значение total по умолчанию равно 10; параметр задаёт общее число попыток, включая первоначальную."
WRONG_HEADER_SCOPE = "Authored different header scope; exact metadata must not match the selected declaration."
MISSING_HEADER_QUOTE = "Авторская отсутствующая цитата заголовков: этот текст не содержится в захваченном документе."
SHARED_HEADER_DESCRIPTION = "A second authored label for the same header declaration and shared mapping; no independent evidence."
MISSTATED_TOTAL_DESCRIPTION = "Deliberately incorrect project-authored retries-versus-all-attempts prose; structural comparison does not verify this meaning."
REQUIREMENT_FIELDS = ["scope", "value_type", "source_selector", "adapter_symbol", "document_selector"]
SOURCE_REPORTS = {"baseline": "source-reports/baseline.json", "misstated-total-unit": "source-reports/misstated-total-unit.json"}
EXCLUDED = [
    {"id": "retry-after-status-codes", "member": "RETRY_AFTER_STATUS_CODES", "parameter": None, "reason_code": "integer_frozenset_not_supported"},
    {"id": "retry-backoff-max-constant", "member": "DEFAULT_BACKOFF_MAX", "parameter": None, "reason_code": "class_integer_not_supported"},
    {"id": "retry-backoff-max-default", "member": "__init__", "parameter": "backoff_max", "reason_code": "name_default_not_evaluated"},
    {"id": "retry-backoff-jitter", "member": "__init__", "parameter": "backoff_jitter", "reason_code": "float_default_not_supported"},
]
CASE_DEFINITIONS = [
    {"id": "baseline", "title": "Четыре выбранных обещания и объявления", "parent": "baseline",
     "expected_counts": {"total": 4, "assigned": 4, "mapped": 4, "anchor_verified": 4, "covered": 4, "matching": 4, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 4, "covered_unique_requirements": 4, "assigned_mapping_ids": 4, "mapped_mapping_ids": 4},
     "expected_state": "current_for_human_review", "expected_fraction": {"numerator": 4, "denominator": 4}, "expected_unique_fraction": {"numerator": 4, "denominator": 4}},
    {"id": "uncovered-headers", "title": "Новая запись заголовков без назначенной ссылки", "parent": "baseline",
     "expected_counts": {"total": 4, "assigned": 3, "mapped": 3, "anchor_verified": 4, "covered": 3, "matching": 3, "mismatch": 0, "unresolved": 0, "uncovered": 1, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 4, "covered_unique_requirements": 3, "assigned_mapping_ids": 3, "mapped_mapping_ids": 3},
     "expected_state": "blocked", "expected_fraction": {"numerator": 3, "denominator": 4}, "expected_unique_fraction": {"numerator": 3, "denominator": 4}},
    {"id": "invalid-headers-scope", "title": "Иная scope не принимается за выбранное объявление", "parent": "baseline",
     "expected_counts": {"total": 4, "assigned": 4, "mapped": 3, "anchor_verified": 4, "covered": 3, "matching": 3, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 1, "unresolved_anchor": 0, "unique_requirements": 4, "covered_unique_requirements": 3, "assigned_mapping_ids": 4, "mapped_mapping_ids": 3},
     "expected_state": "blocked", "expected_fraction": {"numerator": 3, "denominator": 4}, "expected_unique_fraction": {"numerator": 3, "denominator": 4}},
    {"id": "missing-headers-quote", "title": "Отсутствующая цитата заголовков остаётся видимой", "parent": "baseline",
     "expected_counts": {"total": 4, "assigned": 4, "mapped": 4, "anchor_verified": 3, "covered": 3, "matching": 3, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 1, "unique_requirements": 4, "covered_unique_requirements": 3, "assigned_mapping_ids": 4, "mapped_mapping_ids": 4},
     "expected_state": "blocked", "expected_fraction": {"numerator": 3, "denominator": 4}, "expected_unique_fraction": {"numerator": 3, "denominator": 4}},
    {"id": "shared-headers", "title": "Повтор новой записи не создаёт независимое основание", "parent": "baseline",
     "expected_counts": {"total": 5, "assigned": 5, "mapped": 5, "anchor_verified": 5, "covered": 5, "matching": 5, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 4, "covered_unique_requirements": 4, "assigned_mapping_ids": 4, "mapped_mapping_ids": 4},
     "expected_state": "current_for_human_review", "expected_fraction": {"numerator": 5, "denominator": 5}, "expected_unique_fraction": {"numerator": 4, "denominator": 4}},
    {"id": "misstated-total-unit", "title": "Заведомо неверная единица в авторской прозе при совпавших значениях", "parent": "misstated-total-unit",
     "expected_counts": {"total": 4, "assigned": 4, "mapped": 4, "anchor_verified": 4, "covered": 4, "matching": 4, "mismatch": 0, "unresolved": 0, "uncovered": 0, "invalid_mapping": 0, "unresolved_anchor": 0, "unique_requirements": 4, "covered_unique_requirements": 4, "assigned_mapping_ids": 4, "mapped_mapping_ids": 4},
     "expected_state": "current_for_human_review", "expected_fraction": {"numerator": 4, "denominator": 4}, "expected_unique_fraction": {"numerator": 4, "denominator": 4}},
]
DATA_FILES = ["source-import.json", "case-observations.json", "cli-outcomes.json", "offline-observations.json", "output-collision.json"]
EXPECTED_SUMMARY = {
    "case_count": 6, "new_source_report_count": 2, "new_inventory_count": 6,
    "selected_mapping_count": 4, "declared_register_entry_count": 8, "excluded_before_analysis_count": 4, "source_caveat_count": 4,
    "source_report_comparison_states": {"match": 8, "mismatch": 0, "unresolved": 0},
    "source_report_pair_states": {"match": 24, "mismatch": 0, "unresolved": 0},
    "source_report_endpoints": {"total": 24, "resolved": 24, "unresolved": 0},
    "aggregate_counts": {"total": 25, "assigned": 24, "mapped": 23, "anchor_verified": 24, "covered": 22, "matching": 22, "mismatch": 0, "unresolved": 0, "uncovered": 1, "invalid_mapping": 1, "unresolved_anchor": 1, "unique_requirements": 24, "covered_unique_requirements": 21, "assigned_mapping_ids": 23, "mapped_mapping_ids": 22},
    "case_states": {"current_for_human_review": 3, "blocked": 3},
    "coverage_states": {"declared_promises_covered": 3, "declared_coverage_incomplete": 3},
    "reference_counts": {"total": 24, "valid": 23, "invalid": 1, "unknown": 0},
    "reference_comparison_states": {"match": 24, "mismatch": 0, "unresolved": 0, "unavailable": 0},
    "reference_pair_states": {"match": 72, "mismatch": 0, "unresolved": 0},
    "upstream_comparison_states": {"match": 24, "mismatch": 0, "unresolved": 0},
    "upstream_pair_states": {"match": 72, "mismatch": 0, "unresolved": 0},
    "upstream_blocker_count": 0, "local_blocker_count": 3, "total_blocker_count": 3,
    "shared_mapping_group_count": 1, "duplicate_requirement_group_count": 1,
    "unknown_mapping_id_instance_count": 0, "unmapped_mapping_id_instance_count": 2,
    "new_cli_child_process_count": 18, "new_successful_cli_count": 17, "new_refused_cli_count": 1,
    "source_adapter_cli_count": 4, "source_promise_coverage_cli_count": 14,
    "analyze_command_count": 9, "show_command_count": 9, "unique_cli_process_count": 18,
    "public_file_count": 29, "public_json_file_count": 23, "public_markdown_file_count": 3,
    "public_python_data_file_count": 2, "public_license_file_count": 1,
    "new_corpus_file_count": 10, "old_public_files_checked": 68,
    "source_and_license_bytes_match_original_corpus": True, "new_corpus_bytes_unchanged": True, "old_public_files_unchanged": True,
    "all_new_source_reports_validated": True, "new_inventories_explicitly_authored": True, "legacy_inventory_converted": False,
    "all_upstream_comparisons_preserved": True, "known_invalid_reference_comparison_preserved": True,
    "excluded_candidates_submitted_to_analyzers": False, "declared_register_is_exhaustive": False,
    "misstated_prose_case_is_authored": True, "misstated_prose_case_formally_ready": True, "automatic_semantic_detection_performed": False,
    "shared_mapping_is_not_independent_evidence": True, "report_roundtrip_summaries_equal": True,
    "historical_show_with_inputs_and_source_reports_offline": True, "offline_directories_restored": True,
    "output_collision_refused": True, "output_collision_report_bytes_and_metadata_unchanged": True,
    "upstream_code_executed": False, "adapter_code_executed": False, "old_demonstrations_rerun": False,
    "store_writes_performed": False, "human_review_recorded": False, "external_llm_used": False, "live_upstream_refetched": False,
    "independent_human_annotation": False, "benchmark": False, "measured_capacity": False, "process_overlap_measured": False,
    "private_credentials_created": 0, "database_files_created": 0,
    "requires_human_review": True, "auto_accept": False, "live_state_checked": False,
    "runtime_binding_verified": False, "prose_semantics_verified": False, "atomic_capture": False,
}
LIMITS = [
    "Four mappings and eight candidate register entries are explicitly authored selections, not an exhaustive inventory or an eight-promise coverage denominator.",
    "The pinned source and MIT license are unchanged copies of the earlier captured corpus; no current upstream version, authenticity or fresh network retrieval is established.",
    "The four excluded forms are recorded before analysis; they are not submitted under incorrect integer or string-set types and are not analyzer unresolved observations.",
    "The backoff_factor projection is the syntactic integer literal zero despite a float annotation; allowed runtime values and scheduling behavior are not established.",
    "Raw header declaration names precede the later lowercase transformation; matching selected strings does not establish all redirect behavior.",
    "A deliberately incorrect project-authored total-unit sentence still has full structural coverage and matching values. The existing analyzers do not verify its prose meaning.",
    "Repeated promises and reused source parents are not independent evidence, human annotation, semantic-quality measurements or an agent-capacity benchmark.",
    "Historical show reconstructs the full saved report while both input and separate source-report directories are absent; current freshness and atomic capture are not established.",
    "No captured Python, external LLM, database, credential, Store acceptance or worker pool is executed or created; advisory results still require human review.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Retry-source study stopped: " + message)


def _hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            _require(key not in result, "JSON contained a duplicate key")
            result[key] = value
        return result
    def reject(_value: str) -> None:
        raise ValueError("Unsupported non-finite study value")
    return json.loads(text, object_pairs_hook=pairs, parse_constant=reject)


def _read_json(path: Path) -> Any:
    return _strict_json(source_adapter.read_text(path, source_adapter.MAX_REPORT_BYTES))


def _json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _metadata(path: Path) -> tuple[int, ...]:
    details = path.lstat()
    _require(stat.S_ISREG(details.st_mode) and not path.is_symlink(), "a report is not a regular file")
    return tuple(getattr(details, key) for key in ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _snapshot(directory: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(directory.rglob("*")):
        _require(not path.is_symlink(), "a public corpus contains an unsupported symlink")
        if path.is_dir():
            continue
        _require(stat.S_ISREG(path.lstat().st_mode), "a public corpus contains a nonregular file")
        raw = path.read_bytes()
        records.append({"path": path.relative_to(directory).as_posix(), "byte_count": len(raw), "sha256": _hash(raw)})
    return records


def _evidence(evidence: Any, source: str) -> None:
    _require(type(evidence) is dict and set(evidence) == {"quote", "sha256", "location"}, "source evidence fields differ")
    quote, location = evidence["quote"], evidence["location"]
    _require(type(quote) is str and bool(quote) and type(location) is dict
             and set(location) == {"start_offset", "end_offset", "start_line", "end_line"}
             and all(type(value) is int for value in location.values()), "source evidence location differs")
    start, end = location["start_offset"], location["end_offset"]
    _require(0 <= start < end <= len(source) and source[start:end] == quote
             and location == source_adapter._location(source, start, end)
             and evidence["sha256"] == _hash(quote.encode("utf-8")), "source evidence bytes or location differ")


def _corpus_contract(repository: Path, corpus: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    snapshot = _snapshot(corpus)
    _require([row["path"] for row in snapshot] == CORPUS_FILES, "the authored corpus tree differs")
    manifest = _read_json(corpus / "manifest.json")
    _require(type(manifest) is dict and set(manifest) == {"schema_version", "corpus_kind", "upstream_repository", "peeled_commit", "license", "origin_manifest", "files", "independent_human_annotation", "benchmark", "upstream_code_executed", "live_upstream_refetched", "limits"}
             and type(manifest["schema_version"]) is int and manifest["schema_version"] == 1
             and manifest["corpus_kind"] == "authored_retry_source_study"
             and manifest["upstream_repository"] == "https://github.com/urllib3/urllib3"
             and manifest["peeled_commit"] == SOURCE_COMMIT and manifest["license"] == "MIT"
             and type(manifest["limits"]) is list and bool(manifest["limits"])
             and all(type(item) is str and bool(item) for item in manifest["limits"])
             and all(manifest[key] is False for key in ("independent_human_annotation", "benchmark", "upstream_code_executed", "live_upstream_refetched")), "corpus manifest identity differs")
    origin = manifest["origin_manifest"]
    old_manifest_path = OLD_CORPUS + "/manifest.json"
    old_manifest_raw = (repository / old_manifest_path).read_bytes()
    _require(type(origin) is dict and set(origin) == {"path", "sha256", "acquisition_note"}
             and origin["path"] == old_manifest_path and origin["sha256"] == _hash(old_manifest_raw)
             and type(origin["acquisition_note"]) is str and bool(origin["acquisition_note"]), "origin manifest binding differs")
    old_files = {item["local_path"]: item for item in _strict_json(old_manifest_raw.decode("utf-8"))["files"]}
    records = manifest["files"]
    _require(type(records) is list and len(records) == 9
             and [item["local_path"] for item in records] == [name for name in CORPUS_FILES if name != "manifest.json"], "manifest membership differs")
    for record in records:
        _require(type(record) is dict and set(record) == {"local_path", "kind", "byte_count", "sha256", "origin_path", "origin_retrieved_at_utc"}, "manifest record fields differ")
        relative = record["local_path"]
        raw = (corpus / relative).read_bytes()
        _require(type(record["byte_count"]) is int and record["byte_count"] == len(raw) and record["sha256"] == _hash(raw), "manifest content pin differs")
        if relative in {"upstream/retry.py", "upstream/LICENSE.txt"}:
            original = OLD_CORPUS + "/" + relative
            _require(raw == (repository / original).read_bytes() and record["origin_path"] == original
                     and record["origin_retrieved_at_utc"] == old_files[relative]["retrieved_at_utc"]
                     and record["kind"] == ("upstream_source" if relative.endswith("retry.py") else "license"), "copied upstream provenance differs")
        else:
            _require(record["kind"] == "authored" and record["origin_path"] is None and record["origin_retrieved_at_utc"] is None, "authored provenance differs")
    _require(_hash((corpus / "upstream/retry.py").read_bytes()) == SOURCE_SHA256
             and _hash((corpus / "upstream/LICENSE.txt").read_bytes()) == LICENSE_SHA256, "pinned source or license differs")
    register = _read_json(corpus / "selection-register.json")
    _require(type(register) is dict and set(register) == {"schema_version", "source_path", "source_sha256", "source_commit", "entries", "caveats", "limits", "authorship"}
             and type(register["schema_version"]) is int and register["schema_version"] == 1
             and register["source_path"] == "upstream/retry.py" and register["source_sha256"] == SOURCE_SHA256
             and register["source_commit"] == SOURCE_COMMIT and type(register["entries"]) is list and len(register["entries"]) == 8
             and type(register["caveats"]) is list and len(register["caveats"]) == 4
             and type(register["limits"]) is list and bool(register["limits"])
             and all(type(item) is str and bool(item) for item in register["limits"])
             and type(register["authorship"]) is str and bool(register["authorship"]), "selection register identity differs")
    source = source_adapter.read_text(corpus / "upstream/retry.py")
    for index, entry in enumerate(register["entries"]):
        _require(type(entry) is dict and set(entry) == {"id", "selection", "mapping_id", "declaration_form", "source_selector", "reason_code", "evidence", "annotation"}, "register entry fields differ")
        selector = entry["source_selector"]
        _require(type(selector) is dict and set(selector) == {"class_name", "member", "parameter"}
                 and selector["class_name"] == "Retry" and type(entry["annotation"]) is str and bool(entry["annotation"]), "register selector differs")
        if index < 4:
            member, parameter = [("DEFAULT_ALLOWED_METHODS", None), ("__init__", "total"), ("DEFAULT_REMOVE_HEADERS_ON_REDIRECT", None), ("__init__", "backoff_factor")][index]
            _require(entry["id"] == entry["mapping_id"] == MAPPING_IDS[index] and entry["selection"] == "selected"
                     and entry["reason_code"] is None and selector["member"] == member and selector["parameter"] == parameter, "selected register member differs")
        else:
            expected = EXCLUDED[index - 4]
            _require(entry["id"] == expected["id"] and entry["selection"] == "excluded_before_analysis"
                     and entry["mapping_id"] is None and entry["reason_code"] == expected["reason_code"]
                     and selector["member"] == expected["member"] and selector["parameter"] == expected["parameter"], "excluded register member differs")
        _require(entry["declaration_form"] == ("class_assignment" if selector["parameter"] is None else "parameter_default"), "register declaration form differs")
        _evidence(entry["evidence"], source)
    _require([row["id"] for row in register["caveats"]] == ["total-retries-unit", "allowed-methods-none", "header-lowercase-expression", "backoff-factor-annotation"], "declared caveat membership differs")
    for caveat in register["caveats"]:
        _require(type(caveat) is dict and set(caveat) == {"id", "annotation", "evidence", "source_url"}
                 and type(caveat["id"]) is str and bool(caveat["id"])
                 and type(caveat["annotation"]) is str and bool(caveat["annotation"]), "caveat fields differ")
        _evidence(caveat["evidence"], source)
        _require(caveat["source_url"] == "https://github.com/urllib3/urllib3/blob/" + SOURCE_COMMIT
                 + "/src/urllib3/util/retry.py#L" + str(caveat["evidence"]["location"]["start_line"]), "caveat source URL differs")
    specs = _read_json(corpus / "authored/promise-specs.json")
    profile = source_adapter.load_profile(corpus / "authored/profile.json")
    _require(type(specs) is dict and set(specs) == {"schema_version", "authorship", "promises", "misstated_total_quote"}
             and type(specs["schema_version"]) is int and specs["schema_version"] == 1
             and type(specs["authorship"]) is str and bool(specs["authorship"])
             and specs["misstated_total_quote"] == MISSTATED_TOTAL_QUOTE
             and [mapping["id"] for mapping in profile["mappings"]] == MAPPING_IDS
             and type(specs["promises"]) is list and len(specs["promises"]) == 4, "authored promise specification differs")
    for promise, mapping, quote in zip(specs["promises"], profile["mappings"], QUOTES):
        _require(type(promise) is dict and set(promise) == {"id", "description", "anchor", "requirement", "mapping_ids"}
                 and promise["id"] == mapping["id"] + "-promise" and promise["mapping_ids"] == [mapping["id"]]
                 and promise["anchor"] == {"quote": quote}
                 and promise["requirement"] == {key: mapping[key] for key in REQUIREMENT_FIELDS}, "explicit promise mapping differs")
    baseline = source_adapter.read_text(corpus / "authored/contract.md")
    misstated = source_adapter.read_text(corpus / "authored/contract-misstated-total-unit.md")
    _require(all(baseline.count(quote) == 1 for quote in QUOTES)
             and MISSTATED_TOTAL_QUOTE not in baseline
             and misstated == baseline.replace(QUOTES[1], MISSTATED_TOTAL_QUOTE, 1)
             and misstated.count(MISSTATED_TOTAL_QUOTE) == 1 and QUOTES[1] not in misstated, "authored prose variant differs from its one declared replacement")
    return specs, register


def _inventory(parent: dict[str, Any], specs: dict[str, Any], case_id: str) -> dict[str, Any]:
    promises = deepcopy(specs["promises"])
    if case_id == "uncovered-headers":
        promises[2]["mapping_ids"] = []
    elif case_id == "invalid-headers-scope":
        promises[2]["requirement"]["scope"] = WRONG_HEADER_SCOPE
    elif case_id == "missing-headers-quote":
        promises[2]["anchor"]["quote"] = MISSING_HEADER_QUOTE
    elif case_id == "shared-headers":
        repeated = deepcopy(promises[2])
        repeated["id"], repeated["description"] = "retry-remove-headers-copy", SHARED_HEADER_DESCRIPTION
        promises.append(repeated)
    elif case_id == "misstated-total-unit":
        promises[1]["anchor"]["quote"] = MISSTATED_TOTAL_QUOTE
        promises[1]["description"] = MISSTATED_TOTAL_DESCRIPTION
    else:
        _require(case_id == "baseline", "unknown fixed study case")
    return source_promise_coverage.validate_inventory({"schema_version": 1, "source_report_id": parent["report_id"],
        "source_report_digest": parent["report_digest"], "promises": promises})


def run_retry_source_study_demo(workspace: str | Path) -> dict[str, Any]:
    """Run the six fixed authored illustrations through the existing commands."""
    requested = Path(workspace).expanduser()
    _require(not requested.is_symlink(), "workspace must not be a symlink")
    workspace = requested.resolve()
    _require(not workspace.exists() or workspace.is_dir() and not any(workspace.iterdir()), "workspace must be new and empty")
    workspace.mkdir(parents=True, exist_ok=True)
    inputs, source_reports, coverage_reports = workspace / "inputs", workspace / "source-reports", workspace / "coverage-reports"
    for directory in (inputs, source_reports, coverage_reports):
        directory.mkdir()
    repository = Path(__file__).resolve().parents[1]
    corpus = repository / CORPUS
    specs, register = _corpus_contract(repository, corpus)
    corpus_before = _snapshot(corpus)
    previous_before = [{"scope": name, "files": _snapshot(repository / name)} for name in PREVIOUS_SCOPES]
    _require([len(row["files"]) for row in previous_before] == [11, 26, 31], "previous public study scopes differ")
    for relative, original in INPUT_COPIES.items():
        raw = (corpus / original).read_bytes()
        with (workspace / relative).open("xb") as stream:
            stream.write(raw)
        _require((workspace / relative).read_bytes() == raw, "an explicit input copy differs")
    cli_outcomes: list[dict[str, Any]] = []

    def cli(label: str, command: str, action: str, arguments: list[str], *, case_id: str, expected_error: str | None = None) -> Any:
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            try:
                stdout, stderr = child.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                child.kill()
                child.communicate(timeout=15)
                raise RuntimeError("Retry-source study stopped: ordinary CLI timed out") from error
        found = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
        error_code = found.group(1) if found else None
        cli_outcomes.append({"label": label, "command": command, "action": action, "case_id": case_id,
            "child_process_id": child.pid, "returncode": child.returncode, "error_code": error_code,
            "stderr_empty": not stderr, "completed_child_process": True, "shell": False})
        if expected_error is not None:
            _require(child.returncode == 1 and error_code == expected_error and not stdout, "ordinary CLI did not return the expected safe refusal")
            return {"label": label, "code": error_code, "rejected": True}
        _require(child.returncode == 0 and not stderr, "ordinary CLI failed")
        return _strict_json(stdout)

    parents, parent_observations = {}, []
    parent_states, parent_pairs, parent_endpoints = Counter(), Counter(), Counter()
    for name, relative in SOURCE_REPORTS.items():
        document = "inputs/document.md" if name == "baseline" else "inputs/document-misstated-total-unit.md"
        arguments = ["--source", str(inputs / "source.py"), "--adapter", str(inputs / "adapter.py"),
                     "--document", str(workspace / document), "--profile", str(inputs / "profile.json"), "--output", str(workspace / relative)]
        analyzed = cli(name + "-source-analyze", "source-adapter", "analyze", arguments, case_id=name)
        shown = cli(name + "-source-show", "source-adapter", "show", ["--input", str(workspace / relative)], case_id=name)
        parent = source_adapter.load_report(workspace / relative)
        _require(canonical(analyzed) == canonical(shown) == canonical(source_adapter.summary(parent))
                 and analyzed["state"] == "current_for_human_review" and analyzed["counts"] == {"mappings": 4, "match": 4, "mismatch": 0, "unresolved": 0}
                 and not analyzed["blockers"] and [row["id"] for row in analyzed["results"]] == MAPPING_IDS, "new source report differs from its fixed selected comparison")
        for row in analyzed["results"]:
            parent_states[row["comparison"]["state"]] += 1
            parent_pairs.update(row["comparison"][key] for key in ("source_adapter", "source_document", "adapter_document"))
            parent_endpoints.update("resolved" if row[key]["resolved"] else "unresolved" for key in ("source", "adapter", "document"))
        parents[name] = parent
        parent_observations.append({"name": name, "document": document, "report": relative, "analyze": analyzed, "show": shown, "captured_summary_verified": True})
    _require(parents["baseline"]["captured"]["texts"]["source"] == parents["misstated-total-unit"]["captured"]["texts"]["source"]
             and parents["baseline"]["captured"]["texts"]["adapter"] == parents["misstated-total-unit"]["captured"]["texts"]["adapter"]
             and parents["baseline"]["captured"]["profile"] == parents["misstated-total-unit"]["captured"]["profile"], "the prose illustration changed source, adapter or selected mappings")
    for case in CASE_DEFINITIONS:
        _json(inputs / ("inventory-" + case["id"] + ".json"), _inventory(parents[case["parent"]], specs, case["id"]))
    input_files = sorted([*INPUT_COPIES, *("inputs/inventory-" + case["id"] + ".json" for case in CASE_DEFINITIONS)])
    input_pins = [{"path": name, "byte_count": (workspace / name).stat().st_size, "sha256": _hash((workspace / name).read_bytes())} for name in input_files]

    def analyze_arguments(case: dict[str, Any], target: Path) -> list[str]:
        return ["--source-report", str(workspace / SOURCE_REPORTS[case["parent"]]), "--inventory", str(inputs / ("inventory-" + case["id"] + ".json")), "--output", str(target)]

    observations, summaries = [], {}
    aggregate, states, coverage_states = Counter(), Counter(), Counter()
    reference_counts, reference_states, reference_pairs = Counter(), Counter(), Counter()
    upstream_states, upstream_pairs = Counter(), Counter()
    upstream_blockers = local_blockers = shared_groups = duplicate_groups = unknown_instances = unmapped_instances = 0
    for case in CASE_DEFINITIONS:
        case_id = case["id"]
        relative = "coverage-reports/" + case_id + ".json"
        analyzed = cli(case_id + "-analyze", "source-promise-coverage", "analyze", analyze_arguments(case, workspace / relative), case_id=case_id)
        shown = cli(case_id + "-show", "source-promise-coverage", "show", ["--input", str(workspace / relative)], case_id=case_id)
        full = source_promise_coverage.load_report(workspace / relative)
        parent = parents[case["parent"]]
        expected_upstream = [{"mapping_id": row["id"], "comparison": row["comparison"]} for row in parent["observation"]["results"]]
        _require(canonical(analyzed) == canonical(shown) == canonical(source_promise_coverage.summary(full))
                 and analyzed["counts"] == case["expected_counts"] and analyzed["state"] == case["expected_state"]
                 and analyzed["coverage_fraction"] == case["expected_fraction"] and analyzed["unique_requirement_fraction"] == case["expected_unique_fraction"]
                 and analyzed["upstream_state"] == parent["observation"]["state"] and analyzed["upstream_blockers"] == parent["observation"]["blockers"]
                 and analyzed["upstream_comparisons"] == expected_upstream, "captured coverage summary or full parent evidence differs")
        _require(analyzed["requires_human_review"] is True and all(analyzed[key] is False for key in
                 ("auto_accept", "live_state_checked", "runtime_binding_verified", "prose_semantics_verified", "atomic_capture")), "advisory coverage flags differ")
        aggregate.update(analyzed["counts"]); states[analyzed["state"]] += 1; coverage_states[analyzed["coverage_state"]] += 1
        upstream_blockers += len(analyzed["upstream_blockers"]); local_blockers += len(analyzed["blockers"]) - len(analyzed["upstream_blockers"])
        shared_groups += len(analyzed["shared_mappings"]); duplicate_groups += len(analyzed["duplicate_requirements"])
        unknown_instances += len(analyzed["unknown_mapping_ids"]); unmapped_instances += len(analyzed["unmapped_mapping_ids"])
        for upstream in analyzed["upstream_comparisons"]:
            comparison = upstream["comparison"]; upstream_states[comparison["state"]] += 1
            upstream_pairs.update(comparison[key] for key in ("source_adapter", "source_document", "adapter_document"))
        for row in analyzed["results"]:
            for reference in row["references"]:
                reference_counts["total"] += 1; reference_counts["valid" if reference["valid"] else "invalid"] += 1
                comparison = reference["comparison"]
                reference_states[comparison["state"] if comparison is not None else "unavailable"] += 1
                if comparison is None:
                    reference_counts["unknown"] += 1
                else:
                    reference_pairs.update(comparison[key] for key in ("source_adapter", "source_document", "adapter_document"))
        if case_id == "invalid-headers-scope":
            reference = analyzed["results"][2]["references"][0]
            _require(reference["valid"] is False and reference["actual_requirement_digest"] is not None
                     and reference["comparison"] == parent["observation"]["results"][2]["comparison"], "invalid known reference lost its full comparison")
        elif case_id == "shared-headers":
            _require(len(analyzed["shared_mappings"]) == len(analyzed["duplicate_requirements"]) == 1, "shared declaration evidence was hidden")
        elif case_id == "misstated-total-unit":
            _require(analyzed["coverage_fraction"] == {"numerator": 4, "denominator": 4}
                     and analyzed["state"] == "current_for_human_review" and analyzed["prose_semantics_verified"] is False
                     and analyzed["results"][1]["anchor"]["snippet"] == MISSTATED_TOTAL_QUOTE,
                     "the deliberately authored prose boundary changed its declared structural outcome")
        summaries[case_id] = analyzed
        observations.append({**case, "source_report": SOURCE_REPORTS[case["parent"]], "inventory": "inputs/inventory-" + case_id + ".json",
            "report": relative, "analyze": analyzed, "show": shown, "captured_summary_verified": True})

    baseline_report = coverage_reports / "baseline.json"
    original_bytes, original_metadata = baseline_report.read_bytes(), _metadata(baseline_report)
    hidden_inputs, hidden_parents = workspace / ".retry-source-study-offline-inputs", workspace / ".retry-source-study-offline-source-reports"
    _require(not hidden_inputs.exists() and not hidden_parents.exists(), "an offline directory already exists")
    try:
        inputs.rename(hidden_inputs)
        source_reports.rename(hidden_parents)
        offline_summary = cli("offline-baseline-show", "source-promise-coverage", "show", ["--input", str(baseline_report)], case_id="baseline")
        _require(not inputs.exists() and not source_reports.exists() and canonical(offline_summary) == canonical(summaries["baseline"])
                 and baseline_report.read_bytes() == original_bytes and _metadata(baseline_report) == original_metadata, "historical show needed live inputs or changed its own report")
    finally:
        if hidden_parents.exists():
            hidden_parents.rename(source_reports)
        if hidden_inputs.exists():
            hidden_inputs.rename(inputs)
    _require(inputs.is_dir() and source_reports.is_dir() and not hidden_inputs.exists() and not hidden_parents.exists(), "offline directories did not return")
    collision = cli("baseline-output-collision", "source-promise-coverage", "analyze", analyze_arguments(CASE_DEFINITIONS[0], baseline_report), case_id="baseline", expected_error="source_promise_coverage_io_failed")
    _require(baseline_report.read_bytes() == original_bytes and _metadata(baseline_report) == original_metadata, "refused collision changed the original report")
    _require(_snapshot(corpus) == corpus_before
             and [{"scope": name, "files": _snapshot(repository / name)} for name in PREVIOUS_SCOPES] == previous_before, "a new corpus input or previous public artifact changed")
    _require(input_pins == [{"path": name, "byte_count": (workspace / name).stat().st_size, "sha256": _hash((workspace / name).read_bytes())} for name in input_files], "an authored study input changed during execution")
    measured = {
        "source_report_comparison_states": {key: parent_states[key] for key in ("match", "mismatch", "unresolved")},
        "source_report_pair_states": {key: parent_pairs[key] for key in ("match", "mismatch", "unresolved")},
        "source_report_endpoints": {"total": sum(parent_endpoints.values()), "resolved": parent_endpoints["resolved"], "unresolved": parent_endpoints["unresolved"]},
        "aggregate_counts": dict(aggregate), "case_states": dict(states), "coverage_states": dict(coverage_states),
        "reference_counts": {key: reference_counts[key] for key in ("total", "valid", "invalid", "unknown")},
        "reference_comparison_states": {key: reference_states[key] for key in ("match", "mismatch", "unresolved", "unavailable")},
        "reference_pair_states": {key: reference_pairs[key] for key in ("match", "mismatch", "unresolved")},
        "upstream_comparison_states": {key: upstream_states[key] for key in ("match", "mismatch", "unresolved")},
        "upstream_pair_states": {key: upstream_pairs[key] for key in ("match", "mismatch", "unresolved")},
        "upstream_blocker_count": upstream_blockers, "local_blocker_count": local_blockers, "total_blocker_count": upstream_blockers + local_blockers,
        "shared_mapping_group_count": shared_groups, "duplicate_requirement_group_count": duplicate_groups,
        "unknown_mapping_id_instance_count": unknown_instances, "unmapped_mapping_id_instance_count": unmapped_instances,
    }
    _require(all(measured[key] == EXPECTED_SUMMARY[key] for key in measured)
             and len(cli_outcomes) == len({row["child_process_id"] for row in cli_outcomes}) == 18
             and sum(row["returncode"] == 0 for row in cli_outcomes) == 17
             and Counter(row["action"] for row in cli_outcomes) == {"analyze": 9, "show": 9}
             and Counter(row["command"] for row in cli_outcomes) == {"source-adapter": 4, "source-promise-coverage": 14}, "case or CLI accounting differs from the frozen ordinary scenario")

    source_import = {"schema_version": 1, "kind": "authored_retry_source_study", "corpus": CORPUS,
        "corpus_files": corpus_before, "previous_public_scopes": previous_before, "input_pins": input_pins, "source_reports": parent_observations,
        "source_and_license_bytes_match_original_corpus": True, "new_corpus_bytes_unchanged": True, "old_public_files_unchanged": True,
        "new_inventories_explicitly_authored": True, "legacy_inventory_converted": False, "excluded_candidates_submitted_to_analyzers": False,
        "misstated_prose_case_is_authored": True, "automatic_semantic_detection_performed": False,
        "old_demonstrations_rerun": False, "upstream_code_executed": False, "adapter_code_executed": False, "live_upstream_refetched": False,
        "independent_human_annotation": False, "benchmark": False}
    offline_observations = {"case_id": "baseline", "summary": offline_summary, "input_directory_absent_during_show": True,
        "source_report_directory_absent_during_show": True, "all_inputs_and_parent_reports_absent": True, "own_report_online": True,
        "own_report_bytes_and_metadata_unchanged": True, "all_directories_restored": True, "live_sources_checked": False}
    collision_observation = {"refusal": collision, "target": "coverage-reports/baseline.json", "existing_report_bytes_and_metadata_unchanged": True,
        "report_overwritten": False, "partial_report_repair_performed": False}
    for name, value in zip(DATA_FILES, (source_import, observations, cli_outcomes, offline_observations, collision_observation)):
        _json(workspace / name, value)
    public_files = sorted([*input_files, *SOURCE_REPORTS.values(), *("coverage-reports/" + case["id"] + ".json" for case in CASE_DEFINITIONS), *DATA_FILES, "report.json", "report.md"])
    _require(len(public_files) == len(set(public_files)) == 29
             and {row["path"] for row in _snapshot(workspace)} == set(public_files) - {"report.json", "report.md"}, "the public study tree differs before final report")
    report = {"schema_version": 1, "rules_version": RULES_VERSION, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "timezone": "Europe/Berlin", "scenario": "Four selected Retry declarations, explicit selection gaps and an authored prose-unit boundary",
        "summary": dict(EXPECTED_SUMMARY), "cases": [{"id": case["id"], "title": case["title"], "report": "coverage-reports/" + case["id"] + ".json",
            "state": case["expected_state"], "counts": case["expected_counts"], "coverage_fraction": case["expected_fraction"], "unique_requirement_fraction": case["expected_unique_fraction"]} for case in CASE_DEFINITIONS],
        "artifacts": public_files, "limits": LIMITS}
    _json(workspace / "report.json", report)
    lines = [
        "# Четыре объявления Retry, явные исключения и граница проверки прозы", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Полные source/coverage reports используют UTC.", "",
        "[Происхождение и ответы source analyze/show](source-import.json), [закреплённый исходник](inputs/source.py), [MIT license](inputs/LICENSE.txt). "
        "[Selection register](inputs/selection-register.json) явно содержит четыре selected и четыре excluded_before_analysis; восемь записей не являются полной описью источника или знаменателем coverage.", "",
        "[Профиль](inputs/profile.json), [новые авторские promise specs](inputs/promise-specs.json), [baseline document](inputs/document.md), "
        "[заведомо ошибочная авторская проза](inputs/document-misstated-total-unit.md), [baseline inventory](inputs/inventory-baseline.json). "
        "Полные source reports: [baseline](source-reports/baseline.json), [misstated total unit](source-reports/misstated-total-unit.json).", "",
        "## Шесть обычных случаев", "", "| Случай | Covered / total | Unique covered / total | Matching | State |", "|---|---:|---:|---:|---|",
    ]
    for case in report["cases"]:
        counts = case["counts"]
        lines.append(f"| [{case['id']}]({case['report']}) | {counts['covered']} / {counts['total']} | {counts['covered_unique_requirements']} / {counts['unique_requirements']} | {counts['matching']} | {case['state']} |")
    lines += [
        "", "## Что наблюдалось", "",
        "Два новых полных source reports сравнили по четыре объявления: методы, total=10, исходный набор имён удаляемых заголовков и AST default backoff_factor=0. "
        "Все восемь mapping comparisons, 24 pair comparisons и 24 endpoints разрешились с совпавшими значениями. Float annotation не превращает выбранный integer AST literal в утверждение о разрешённых runtime values.",
        "В шести авторских иллюстрациях 25 promise rows: 22 covered и matching; отдельно одна uncovered, одна invalid_mapping и одна unresolved_anchor. "
        "Три случая current_for_human_review, три blocked. Известная неверная scope сохранила фактический requirement digest и полный Comparison4. "
        "Повтор headers promise дал 5/5 covered при 4/4 distinct requirements; shared mapping не является независимым основанием.",
        "Ключевой содержательный случай: заведомо неверная авторская фраза подменяет retries общим числом попыток. JSON total=10, селекторы и уникальная цитата остаются согласованы, "
        "поэтому существующие анализаторы возвращают 4/4 coverage и current_for_human_review с prose_semantics_verified=false. Ошибку смысла они не обнаруживают; правильная единица не подтверждена этим результатом. "
        "Upstream source не изменялся. Это авторская иллюстрация границы, а не ошибочный текст официальной документации, независимый эталон или измеренная semantic accuracy.",
        "Четыре иных объявления сохранены в register до анализа: integer frozenset, class integer, Name default и float default. Они не поданы под неверными типами и не выдаются за analyzer unresolved results. "
        "Четыре source-backed caveats сохраняют точные цитаты, позиции и commit-pinned ссылки для рассмотрения человеком.",
        "Выполнены две source analyze/show пары и шесть coverage пар. Затем show прошёл при отсутствии inputs и source-reports целиком; "
        "finally вернул оба каталога. Existing output получил безопасный отказ без изменения baseline report bytes/metadata.",
        "", "## Публичные основания", "",
        "[Все случаи и ответы](case-observations.json), [18 CLI outcomes](cli-outcomes.json), [исторический show](offline-observations.json), "
        "[отказ перезаписи](output-collision.json), [машинная сводка](report.json).", "",
        "## Границы", "",
        "Выполнены 18 последовательных CLI: 17 successful и один ожидаемый refusal, 18 различных PID; concurrency и предельное число агентов не измерялись. "
        "Публичное дерево — 29 файлов: 23 JSON, три Markdown, два Python DATA и одна лицензия. Python inputs не исполнялись и не импортировались.",
        "Четыре выбранных declarations и восемь register entries составлены авторами проекта. Суммы повторяющихся случаев не являются долей полноты документации. "
        "Raw header names не заменяют анализа последующего lowercase преобразования и всех redirect веток. Total integer не устанавливает смысл попыток; source literal не устанавливает runtime binding. "
        "Прежние 11 corpus/26 source-adapter/31 source-promise public files и новый десятифайловый corpus остались byte-identical. Новых DB, credentials, Store writes, external LLM или worker pool нет. "
        "Текущая свежесть upstream, атомарный capture, человеческая экспертиза и независимость участников не установлены. Human review обязателен; auto_accept=false.", "",
    ]
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    actual_files = _snapshot(workspace)
    _require([row["path"] for row in actual_files] == public_files
             and Counter(Path(row["path"]).suffix for row in actual_files) == {".json": 23, ".md": 3, ".py": 2, ".txt": 1}, "the final public tree or suffix counts differ")
    return report
