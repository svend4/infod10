"""Demonstrate captured syntax provenance without executing any input Python.

Eight ordinary analyze/show workflows compare a pinned public source snapshot
with explicitly authored adapter/document fixtures. This is an independent
scenario: it does not replay the recovery demonstrations or open a database.
"""

from __future__ import annotations

from collections import Counter
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

from . import source_adapter
from .store import canonical


RULES_VERSION = "source-adapter-demo-1"
PROFILE = {
    "schema_version": 1,
    "mappings": [
        {
            "id": "retry-methods", "value_type": "string_set",
            "scope": "urllib3 2.5.0 Retry.DEFAULT_ALLOWED_METHODS captured declaration",
            "annotation": "Selected allowlist syntax only. Absence of POST does not prohibit every POST retry branch.",
            "source_selector": {"kind": "class_frozenset_strings", "class_name": "Retry", "symbol": "DEFAULT_ALLOWED_METHODS"},
            "adapter_symbol": "DEFAULT_ALLOWED_METHODS",
            "document_selector": {"fence_id": "retry-defaults", "field": "default_allowed_methods"},
        },
        {
            "id": "retry-total", "value_type": "integer",
            "scope": "urllib3 2.5.0 Retry constructor total default expression",
            "annotation": "The retries unit comes from authored interpretation of pinned prose; integer syntax does not infer that meaning or a universal request limit.",
            "source_selector": {"kind": "parameter_default", "class_name": "Retry", "method_name": "__init__", "parameter": "total"},
            "adapter_symbol": "TOTAL_DEFAULT",
            "document_selector": {"fence_id": "retry-defaults", "field": "total_default"},
        },
    ],
}
CASE_DEFINITIONS = [
    {"id": "baseline", "title": "Закреплённый исходник и прежний авторский адаптер",
     "source": "inputs/upstream-retry.py", "adapter": "inputs/adapter.py", "document": "inputs/contract.md",
     "fixture_note": "All three texts are byte-identical copies of the existing pinned corpus.",
     "expected_counts": {"mappings": 2, "match": 2, "mismatch": 0, "unresolved": 0}},
    {"id": "adapter-total-11", "title": "Авторский адаптер меняет TOTAL_DEFAULT на 11",
     "source": "inputs/upstream-retry.py", "adapter": "inputs/adapter-total-11.py", "document": "inputs/contract.md",
     "fixture_note": "Only the authored adapter fixture changes; upstream and document remain captured baseline data.",
     "expected_counts": {"mappings": 2, "match": 1, "mismatch": 1, "unresolved": 0}},
    {"id": "computed-total", "title": "Вычисляемое выражение 5 + 5 не исполняется",
     "source": "inputs/source-computed-total.py", "adapter": "inputs/adapter.py", "document": "inputs/contract.md",
     "fixture_note": "Synthetic source fixture replaces the selected integer expression with 5 + 5; it is not an upstream change.",
     "expected_counts": {"mappings": 2, "match": 1, "mismatch": 0, "unresolved": 1}},
    {"id": "shadowed-frozenset", "title": "Видимое связывание имени frozenset",
     "source": "inputs/source-shadowed-frozenset.py", "adapter": "inputs/adapter.py", "document": "inputs/contract.md",
     "fixture_note": "Synthetic source fixture binds frozenset after its future import; no name or constructor is executed.",
     "expected_counts": {"mappings": 2, "match": 1, "mismatch": 0, "unresolved": 1}},
    {"id": "duplicate-class", "title": "Два объявления выбранного класса",
     "source": "inputs/source-duplicate-class.py", "adapter": "inputs/adapter.py", "document": "inputs/contract.md",
     "fixture_note": "Synthetic source fixture appends a second top-level Retry class; selection is ambiguous.",
     "expected_counts": {"mappings": 2, "match": 0, "mismatch": 0, "unresolved": 2}},
    {"id": "ambiguous-fence", "title": "Два одинаковых JSON fence документа",
     "source": "inputs/upstream-retry.py", "adapter": "inputs/adapter.py", "document": "inputs/document-ambiguous-fence.md",
     "fixture_note": "Synthetic authored-document fixture duplicates the selected whole fenced block.",
     "expected_counts": {"mappings": 2, "match": 0, "mismatch": 0, "unresolved": 2}},
    {"id": "bool-total", "title": "True не принимается за integer default",
     "source": "inputs/source-bool-total.py", "adapter": "inputs/adapter.py", "document": "inputs/contract.md",
     "fixture_note": "Synthetic source fixture replaces the selected integer with True; bool/int equality is not used.",
     "expected_counts": {"mappings": 2, "match": 1, "mismatch": 0, "unresolved": 1}},
    {"id": "mixed-unresolved-and-mismatch", "title": "Неразрешимый source сохраняет mismatch других сторон",
     "source": "inputs/source-computed-total.py", "adapter": "inputs/adapter-total-11.py", "document": "inputs/contract.md",
     "fixture_note": "Two existing synthetic fixtures are combined; the adapter/document mismatch remains visible.",
     "expected_counts": {"mappings": 2, "match": 1, "mismatch": 0, "unresolved": 1}},
]
INPUT_FILES = [
    "inputs/upstream-retry.py", "inputs/adapter.py", "inputs/contract.md", "inputs/profile.json", "inputs/LICENSE.txt",
    "inputs/adapter-total-11.py", "inputs/source-computed-total.py", "inputs/source-shadowed-frozenset.py",
    "inputs/source-duplicate-class.py", "inputs/document-ambiguous-fence.md", "inputs/source-bool-total.py",
]
DATA_FILES = ["source-import.json", "case-observations.json", "cli-outcomes.json", "offline-observations.json", "output-collision.json"]
EXPECTED_SUMMARY = {
    "case_count": 8, "mapping_count": 16, "mapping_match_count": 7, "mapping_mismatch_count": 1,
    "mapping_unresolved_count": 8, "pair_match_count": 29, "pair_mismatch_count": 3, "pair_unresolved_count": 16,
    "current_for_human_review_case_count": 1, "blocked_case_count": 7,
    "resolved_endpoint_count": 40, "unresolved_endpoint_count": 8,
    "source_endpoint_unresolved_count": 6, "adapter_endpoint_unresolved_count": 0, "document_endpoint_unresolved_count": 2,
    "new_cli_child_process_count": 18, "new_successful_cli_count": 17, "new_refused_cli_count": 1,
    "analyze_command_count": 9, "show_command_count": 9, "unique_cli_process_count": 18,
    "public_file_count": 26, "public_json_file_count": 15, "public_markdown_file_count": 3,
    "public_python_input_file_count": 7, "public_license_file_count": 1, "baseline_input_file_count": 3,
    "pinned_upstream_source_sha256_verified": True, "baseline_three_input_bytes_match_corpus": True,
    "license_bytes_match_corpus": True, "corpus_bytes_unchanged": True, "report_roundtrip_summaries_equal": True,
    "historical_show_with_inputs_offline": True, "offline_inputs_restored": True,
    "output_collision_refused": True, "output_collision_report_bytes_and_metadata_unchanged": True,
    "computed_source_not_evaluated": True, "bool_default_not_treated_as_integer": True,
    "mixed_adapter_document_mismatch_preserved": True,
    "upstream_code_executed": False, "adapter_code_executed": False, "proposed_code_executed": False,
    "live_upstream_refetched": False, "live_state_checked": False, "runtime_binding_verified": False,
    "human_review_recorded": False, "auto_accept": False, "store_writes_performed": False,
    "external_llm_used": False, "independent_human_annotation": False, "benchmark": False,
    "measured_capacity": False, "process_overlap_measured": False, "syntax_projection_only": True,
    "selection_completeness_established": False, "semantic_entailment_established": False,
    "private_credentials_created": 0, "database_files_created": 0,
}
LIMITS = [
    "Only the two explicitly selected declarations are projected from supported syntax; no library code, adapter code or proposed Python is executed.",
    "A parsed frozenset call and absence of detected shadowing do not establish runtime name binding, constructor behavior, metaclass effects or later mutation.",
    "Scope, units and prose-to-field correspondence are authored annotations. Integer 10 does not infer retries rather than attempts, and absence of POST is not a universal prohibition on POST retries.",
    "Unsupported expressions and ambiguous selections remain unresolved. An unresolved source does not hide a mismatch between the separately resolved adapter and document.",
    "Hashes and captured line spans bind the stored local texts; no upstream refetch, signature verification, author authentication or independent human annotation is performed.",
    "The seven nonbaseline scenarios are explicitly authored illustrations using separate derived fixtures. They are not upstream changes, a benchmark or a measurement of error detection quality.",
    "Historical show recomputes a saved report from its own captured inputs. Its original input directory may be absent; this does not establish current live source freshness or semantic truth.",
    "The demonstration creates no database, credentials, Store acceptance, external publication, LLM calls or parallel worker pool. Child process counts do not measure concurrency, capacity or independent expertise.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Source-adapter demonstration stopped: " + message)


def _json(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _file_metadata(path: Path) -> tuple[int, ...]:
    value = path.lstat()
    _require(stat.S_ISREG(value.st_mode) and not path.is_symlink(), "a report is not a regular file")
    return tuple(getattr(value, field) for field in ("st_dev", "st_ino", "st_uid", "st_mode", "st_nlink", "st_size", "st_mtime_ns", "st_ctime_ns"))


def _corpus_snapshot(corpus: Path) -> list[dict[str, Any]]:
    records = []
    for path in sorted(corpus.rglob("*")):
        _require(not path.is_symlink(), "the pinned corpus contains an unsupported symlink")
        if path.is_dir():
            continue
        _require(stat.S_ISREG(path.lstat().st_mode), "the pinned corpus contains a nonregular file")
        raw = path.read_bytes()
        records.append({"path": path.relative_to(corpus).as_posix(), "byte_count": len(raw), "sha256": _hash(raw)})
    return records


def run_source_adapter_demo(workspace: str | Path) -> dict[str, Any]:
    """Run eight ordinary captured-input workflows and publish their evidence."""
    requested = Path(workspace).expanduser()
    _require(not requested.is_symlink(), "workspace must not be a symlink")
    workspace = requested.resolve()
    _require(not workspace.exists() or workspace.is_dir() and not any(workspace.iterdir()), "workspace must be new and empty")
    workspace.mkdir(parents=True, exist_ok=True)
    inputs, reports = workspace / "inputs", workspace / "reports"
    inputs.mkdir()
    reports.mkdir()
    repository = Path(__file__).resolve().parents[1]
    corpus = repository / "corpora" / "urllib3-retry-v2.5.0"
    corpus_before = _corpus_snapshot(corpus)
    manifest_raw = (corpus / "manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    source_raw = (corpus / "upstream/retry.py").read_bytes()
    adapter_raw = (corpus / "authored/retry_config.py").read_bytes()
    document_raw = (corpus / "authored/contract.md").read_bytes()
    license_raw = (corpus / "upstream/LICENSE.txt").read_bytes()
    upstream_entry = [row for row in manifest["files"] if row["local_path"] == "upstream/retry.py"]
    license_entry = [row for row in manifest["files"] if row["local_path"] == "upstream/LICENSE.txt"]
    _require(len(upstream_entry) == len(license_entry) == 1, "the source manifest is ambiguous")
    _require(upstream_entry[0]["sha256"] == _hash(source_raw) and upstream_entry[0]["byte_count"] == len(source_raw)
             and license_entry[0]["sha256"] == _hash(license_raw) and license_entry[0]["byte_count"] == len(license_raw),
             "pinned source or license bytes differ from the stored manifest")
    source, adapter, document = (raw.decode("utf-8") for raw in (source_raw, adapter_raw, document_raw))
    selected_default = "total: bool | int | None = 10,"
    future = "from __future__ import annotations\n"
    _require(source.count(selected_default) == 1 and source.count(future) == 1 and adapter.count("TOTAL_DEFAULT = 10") == 1,
             "the selected baseline declarations are not unique")
    fence_match = re.findall(r"```json agentwiki:retry-defaults\n[\s\S]*?\n```", document)
    _require(len(fence_match) == 1, "the baseline document fence is not unique")
    values: dict[str, bytes] = {
        "inputs/upstream-retry.py": source_raw, "inputs/adapter.py": adapter_raw,
        "inputs/contract.md": document_raw, "inputs/LICENSE.txt": license_raw,
        "inputs/adapter-total-11.py": adapter.replace("TOTAL_DEFAULT = 10", "TOTAL_DEFAULT = 11").encode("utf-8"),
        "inputs/source-computed-total.py": source.replace(selected_default, "total: bool | int | None = 5 + 5,").encode("utf-8"),
        "inputs/source-shadowed-frozenset.py": source.replace(future, future + "\n# Synthetic source fixture; never executed.\nfrozenset = tuple\n").encode("utf-8"),
        "inputs/source-duplicate-class.py": (source + "\n# Synthetic source fixture; never executed.\nclass Retry:\n    pass\n").encode("utf-8"),
        "inputs/document-ambiguous-fence.md": (document + "\n<!-- Synthetic authored fixture: duplicate selected fence. -->\n" + fence_match[0] + "\n").encode("utf-8"),
        "inputs/source-bool-total.py": source.replace(selected_default, "total: bool | int | None = True,").encode("utf-8"),
    }
    _require(set(values) == set(INPUT_FILES) - {"inputs/profile.json"}, "the fixture file set differs")
    for relative, content in values.items():
        with (workspace / relative).open("xb") as stream:
            stream.write(content)
    _json(inputs / "profile.json", PROFILE)
    _require((inputs / "upstream-retry.py").read_bytes() == source_raw and (inputs / "adapter.py").read_bytes() == adapter_raw
             and (inputs / "contract.md").read_bytes() == document_raw and (inputs / "LICENSE.txt").read_bytes() == license_raw,
             "a baseline or license copy differs")
    input_pins = [{"path": path, "byte_count": (workspace / path).stat().st_size, "sha256": _hash((workspace / path).read_bytes())}
                  for path in INPUT_FILES]
    cli_outcomes: list[dict[str, Any]] = []

    def cli(label: str, action: str, arguments: list[str], *, case_id: str, expected_error: str | None = None) -> Any:
        with subprocess.Popen([sys.executable, "-m", "agentwiki", "source-adapter", action, *arguments],
                              cwd=repository, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            try:
                stdout, stderr = child.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                child.kill()
                child.communicate(timeout=15)
                raise RuntimeError("Source-adapter demonstration stopped: ordinary CLI timed out") from error
        found = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
        error_code = found.group(1) if found else None
        cli_outcomes.append({"label": label, "command": "source-adapter", "action": action, "case_id": case_id,
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
        return ["--source", str(workspace / case["source"]), "--adapter", str(workspace / case["adapter"]),
                "--document", str(workspace / case["document"]), "--profile", str(inputs / "profile.json"), "--output", str(target)]

    case_observations: list[dict[str, Any]] = []
    mapping_counts, pair_counts, endpoint_counts, states = Counter(), Counter(), Counter(), Counter()
    baseline_summary = None
    for case in CASE_DEFINITIONS:
        target = reports / (case["id"] + ".json")
        analyzed = cli(case["id"] + "-analyze", "analyze", analyze_arguments(case, target), case_id=case["id"])
        shown = cli(case["id"] + "-show", "show", ["--input", str(target)], case_id=case["id"])
        full = source_adapter.load_report(target)
        expected = source_adapter.summary(full)
        _require(canonical(analyzed) == canonical(shown) == canonical(expected), "analyze/show changed the exact captured summary")
        _require(analyzed["counts"] == case["expected_counts"]
                 and analyzed["state"] == ("current_for_human_review" if case["id"] == "baseline" else "blocked")
                 and analyzed["requires_human_review"] is True and analyzed["auto_accept"] is False
                 and analyzed["live_state_checked"] is False and analyzed["runtime_binding_verified"] is False,
                 "a case changed its fixed counts, advisory flags or expected state")
        mapping_counts.update(analyzed["counts"])
        states[analyzed["state"]] += 1
        for result in analyzed["results"]:
            for key in ("source_adapter", "source_document", "adapter_document"):
                pair_counts[result["comparison"][key]] += 1
            for role in ("source", "adapter", "document"):
                endpoint_counts[role + ("_resolved" if result[role]["resolved"] else "_unresolved")] += 1
        if case["id"] == "baseline":
            baseline_summary = analyzed
            _require(analyzed["results"][0]["source"]["normalized_value"] == ["DELETE", "GET", "HEAD", "OPTIONS", "PUT", "TRACE"]
                     and analyzed["results"][1]["source"]["normalized_value"] == 10,
                     "the baseline did not project the two selected captured values")
        if case["id"] == "mixed-unresolved-and-mismatch":
            total = analyzed["results"][1]
            _require(total["comparison"] == {"source_adapter": "unresolved", "source_document": "unresolved", "adapter_document": "mismatch", "state": "unresolved"},
                     "an unresolved source hid the adapter/document mismatch")
        case_observations.append({**case, "report": "reports/" + case["id"] + ".json",
                                  "analyze": analyzed, "show": shown, "captured_summary_verified": True})

    _require(baseline_summary is not None, "baseline summary was not retained")
    baseline_report = reports / "baseline.json"
    original_report_bytes, original_report_metadata = baseline_report.read_bytes(), _file_metadata(baseline_report)
    hidden = workspace / ".source-adapter-offline-inputs"
    _require(not hidden.exists(), "a temporary offline input directory already exists")
    try:
        inputs.rename(hidden)
        offline_summary = cli("offline-baseline-show", "show", ["--input", str(baseline_report)], case_id="baseline")
        _require(not inputs.exists() and canonical(offline_summary) == canonical(baseline_summary)
                 and baseline_report.read_bytes() == original_report_bytes and _file_metadata(baseline_report) == original_report_metadata,
                 "historical show needed live inputs or changed its own report")
    finally:
        if hidden.exists():
            hidden.rename(inputs)
    _require(inputs.is_dir() and not hidden.exists(), "offline inputs did not return")
    collision = cli("baseline-output-collision", "analyze", analyze_arguments(CASE_DEFINITIONS[0], baseline_report),
                    case_id="baseline", expected_error="source_adapter_io_failed")
    _require(baseline_report.read_bytes() == original_report_bytes and _file_metadata(baseline_report) == original_report_metadata,
             "refused output collision changed the original report")
    _require(_corpus_snapshot(corpus) == corpus_before, "the original corpus bytes changed")
    _require(input_pins == [{"path": path, "byte_count": (workspace / path).stat().st_size, "sha256": _hash((workspace / path).read_bytes())}
                            for path in INPUT_FILES], "an input fixture changed during the ordinary workflow")
    _require(mapping_counts == {"mappings": 16, "match": 7, "mismatch": 1, "unresolved": 8}
             and pair_counts == {"match": 29, "mismatch": 3, "unresolved": 16}
             and states == {"current_for_human_review": 1, "blocked": 7}
             and endpoint_counts == {"source_resolved": 10, "source_unresolved": 6, "adapter_resolved": 16, "document_resolved": 14, "document_unresolved": 2}
             and len(cli_outcomes) == len({row["child_process_id"] for row in cli_outcomes}) == 18
             and sum(row["returncode"] == 0 for row in cli_outcomes) == 17,
             "final case, pair, endpoint or ordinary child accounting differs")

    baseline_inputs = [
        {"role": role, "corpus_path": corpus_path, "path": relative, "byte_count": len(content), "sha256": _hash(content)}
        for role, corpus_path, relative, content in (
            ("source", "upstream/retry.py", "inputs/upstream-retry.py", source_raw),
            ("adapter", "authored/retry_config.py", "inputs/adapter.py", adapter_raw),
            ("document", "authored/contract.md", "inputs/contract.md", document_raw),
        )
    ]
    source_import = {
        "schema_version": 1, "kind": "captured_syntax_source_adapter_demonstration",
        "pinned_source": {"repository": manifest["upstream_repository"], "requested_tag": manifest["requested_tag"],
                          "commit": manifest["peeled_commit"], "corpus_path": "corpora/urllib3-retry-v2.5.0/upstream/retry.py",
                          "byte_count": len(source_raw), "sha256": _hash(source_raw), "manifest_sha256": _hash(manifest_raw)},
        "baseline_inputs": baseline_inputs,
        "license": {"corpus_path": "upstream/LICENSE.txt", "path": "inputs/LICENSE.txt", "byte_count": len(license_raw), "sha256": _hash(license_raw)},
        "input_pins": input_pins, "original_corpus_files": corpus_before, "corpus_bytes_unchanged": True,
        "upstream_code_executed": False, "adapter_code_executed": False, "live_upstream_refetched": False,
        "independent_human_annotation": False, "benchmark": False,
    }
    offline_observations = {"case_id": "baseline", "summary": offline_summary, "input_directory_absent_during_show": True,
                            "source_adapter_document_and_profile_absent": True, "own_report_online": True,
                            "own_report_bytes_and_metadata_unchanged": True, "all_inputs_restored": True,
                            "live_sources_checked": False}
    collision_observation = {"refusal": collision, "target": "reports/baseline.json", "existing_report_bytes_and_metadata_unchanged": True,
                             "report_overwritten": False, "partial_report_repair_performed": False}
    for name, value in zip(DATA_FILES, (source_import, case_observations, cli_outcomes, offline_observations, collision_observation)):
        _json(workspace / name, value)
    public_files = sorted([*INPUT_FILES, *("reports/" + case["id"] + ".json" for case in CASE_DEFINITIONS), *DATA_FILES, "report.json", "report.md"])
    _require(len(public_files) == len(set(public_files)) == 26
             and {path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file()}
             == set(public_files) - {"report.json", "report.md"}, "the public file tree differs before final report")
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Captured syntax provenance for two selected urllib3 Retry defaults and seven authored fixture variations",
        "summary": dict(EXPECTED_SUMMARY),
        "cases": [{"id": case["id"], "title": case["title"], "report": "reports/" + case["id"] + ".json",
                   "state": "current_for_human_review" if case["id"] == "baseline" else "blocked", "counts": case["expected_counts"]}
                  for case in CASE_DEFINITIONS],
        "artifacts": public_files, "limits": LIMITS,
    }
    _json(workspace / "report.json", report)
    lines = [
        "# Происхождение двух выбранных значений urllib3 Retry", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Отдельные captured reports используют UTC.", "",
        "Сценарий читает исходник и Python-адаптер как данные; их код не импортируется и не исполняется. "
        "[Запись происхождения](source-import.json) связывает сохранённый upstream коммит и локальные байты корпуса.", "",
        "Baseline: [upstream source](inputs/upstream-retry.py), [ручной адаптер](inputs/adapter.py), "
        "[авторский документ](inputs/contract.md), [явный профиль](inputs/profile.json), [лицензия MIT](inputs/LICENSE.txt).", "",
        "## Восемь обычных случаев", "", "| Случай | Match | Mismatch | Unresolved | Состояние |", "|---|---:|---:|---:|---|",
    ]
    for case in report["cases"]:
        counts = case["counts"]
        lines.append(f"| [{case['id']}]({case['report']}) | {counts['match']} | {counts['mismatch']} | {counts['unresolved']} | {case['state']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "В исходном снимке извлечены шесть методов из Retry.DEFAULT_ALLOWED_METHODS и integer default total=10; "
        "два выбранных значения совпали с прежним ручным адаптером и JSON fence документа. Это синтаксическая проекция выбранных объявлений.",
        "Отдельный авторский адаптер с TOTAL_DEFAULT=11 дал mismatch. Выражение 5 + 5 не вычислялось; "
        "связывание frozenset, повтор класса, повтор fence и True вместо integer оставили соответствующие endpoints unresolved. "
        "В смешанном случае неразрешимый source сохранил mismatch adapter/document.",
        "Каждый analyze прошёл последующий show с тем же summary. Затем inputs временно отсутствовали целиком: "
        "исторический show пересчитал baseline по captured inputs собственного отчёта. Директория возвращена в finally. "
        "Попытка analyze в существующий baseline report отказала; его bytes и metadata не изменились.",
        "", "## Публичные основания", "",
        "[Случаи и ответы](case-observations.json), [18 CLI outcomes](cli-outcomes.json), "
        "[чтение без inputs](offline-observations.json), [отказ перезаписи](output-collision.json), [машинный отчёт](report.json).", "",
        "## Границы", "",
        "Всего 16 выбранных mapping результатов: 7 match, 1 mismatch, 8 unresolved. "
        "Попарных сравнений 48: 29 match, 3 mismatch, 16 unresolved. Endpoints 48: 40 разрешены, 8 не разрешены. "
        "Один случай current_for_human_review, семь blocked. Принятия либо публикации содержания нет.",
        "Выполнены 18 ordinary CLI (17 успешных, один ожидаемый отказ), 18 различных PID без измерения перекрытия. "
        "Публичное дерево: 26 файлов — 15 JSON, 3 Markdown, 7 Python inputs и одна лицензия. "
        "Базы, credentials, worker pool и внешние LLM не создавались.",
        "Грамматика не устанавливает runtime binding frozenset, поведение конструктора и сетевых веток, полноту документации, "
        "истинность прозы либо независимость проверяющих. Единица retries сохраняется как авторская аннотация; "
        "отсутствие POST в наборе не означает запрета всех его повторов. Семь производных случаев являются явными авторскими fixtures, "
        "а не изменениями upstream, эталонной разметкой или бенчмарком.", "",
    ]
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    actual_files = sorted(path.relative_to(workspace).as_posix() for path in workspace.rglob("*") if path.is_file())
    _require(actual_files == public_files and all(not (workspace / relative).is_symlink() for relative in actual_files)
             and Counter(Path(relative).suffix for relative in actual_files) == {".json": 15, ".md": 3, ".py": 7, ".txt": 1},
             "the final public tree or suffix accounting differs")
    return report
