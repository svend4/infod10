"""Demonstrate fixed catalog membership and current case-control observations.

The ordinary driver never reads private control-record contents. Temporary
directory moves preserve their files and are reversed in finally blocks. Pages
observe case metadata only; ordinary case show provides separate job detail.
"""

from __future__ import annotations

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

from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .store import Store, canonical
from .workspace_restore_case_demo import run_workspace_restore_case_demo
from .workspace_restore_case_catalog_cli import _public_response as _catalog_response
from .workspace_restore_cases_cli import _public_response as _case_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-catalog-1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case catalog demonstration stopped: " + message)


def run_workspace_restore_case_catalog_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Create two explicit catalogs while preserving four private cases."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 287, "the preceding scenario did not produce 287 public files")
    baseline_cases = json.loads((workspace / "restore-cases-cases.json").read_text())
    records = [value["record"] for value in baseline_cases]
    case_directories = [private / f"restore-case-{index:02d}" for index in range(1, 4)]
    ordered = [case_directories[2], case_directories[0], case_directories[1]]
    ordered_records = [records[2], records[0], records[1]]
    catalog_a, catalog_b = private / "restore-case-catalog-main", private / "restore-case-catalog-expanded"
    late_case = private / "restore-case-catalog-late"
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").stat().st_size
    markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "source_bundle", "original_manifest", "restore_manifest",
        "plan", "owner", "staging_path", "sha256", "schema_fingerprint", "request_digest",
        "catalog_path", "case_path", "case_directory", "catalog_directory", "parent", "pins",
    }

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not forbidden.intersection(value), "private metadata reached public output")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in markers), "a credential or its hash reached public output")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(), "access": store.snapshot_access()}

    def matches(before: dict[str, Any], after: dict[str, Any]) -> bool:
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {key: child for key, child in value["governance"].items() if key != "observed_at"}}
        return canonical(comparable(before)) == canonical(comparable(after))

    def controls(path: Path) -> dict[str, Any]:
        """Observe only lstat fields, excluding directory times changed by rename."""
        directory = path.lstat()
        return {"directory": (directory.st_dev, directory.st_ino, directory.st_uid, stat.S_IMODE(directory.st_mode)),
                "files": {entry.name: (details.st_dev, details.st_ino, details.st_uid, details.st_mode,
                                        details.st_nlink, details.st_size, details.st_mtime_ns, details.st_ctime_ns)
                          for entry in sorted(path.iterdir()) for details in [entry.lstat()]}}

    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    pages: dict[str, Any] = {}
    stages: list[dict[str, Any]] = []
    discarded_addresses: dict[str, str] = {}

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"restore-case-catalog-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> dict[str, Any] | None:
        _require(command in {"workspace-restore-case-catalog", "workspace-restore-case", "workspace-restore"},
                 "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore case catalog demonstration stopped: ordinary CLI timed out") from error
            code_match = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
            code = code_match.group(1) if code_match else None
            cli_outcomes.append({"label": label, "command": command, "action": action,
                                 "child_process_id": process.pid, "returncode": process.returncode,
                                 "error_code": code, "stderr_empty": not stderr,
                                 "completed_child_process": True, "shell": False,
                                 "business_response_discarded": discard})
            if expected_error is not None:
                _require(not discard and process.returncode != 0 and code == expected_error and not stdout,
                         "an ordinary command did not return its expected safe refusal")
                refusal = {"label": label, "command": command, "action": action, "code": code, "rejected": True}
                rejections.append(refusal)
                return refusal
            _require(process.returncode == 0 and not stderr, "an ordinary CLI command failed")
            try:
                value = json.loads(stdout)
                if command == "workspace-restore-case-catalog":
                    value = _catalog_response(value, action)
                elif command == "workspace-restore-case":
                    value = _case_response(value, action)
                else:
                    value = _public_status(value)
                value = public(value)
            except (TypeError, ValueError) as error:
                raise RuntimeError("Restore case catalog demonstration stopped: invalid public CLI response") from error
            if discard:
                discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
                return None
            return value

    def create_args(catalog: Path, selected: list[Path], operation: str) -> list[str]:
        return ["--catalog", str(catalog), "--operation-id", operation,
                *(part for path in selected for part in ("--case", str(path)))]

    def page(label: str, catalog: Path, limit: int, cursor: dict[str, Any] | None = None) -> dict[str, Any]:
        arguments = ["--catalog", str(catalog), "--limit", str(limit)]
        if cursor is not None:
            arguments += ["--catalog-id", cursor["catalog_id"], "--after-index", str(cursor["after_index"])]
        result = cli(label, "workspace-restore-case-catalog", "page", arguments)
        pages[label] = result
        return result

    def require_page(value: dict[str, Any], expected: list[dict[str, Any]], states: list[str]) -> None:
        _require([entry["case"] for entry in value["entries"]] == expected
                 and [entry["current_case"]["state"] for entry in value["entries"]] == states
                 and value["original_evidence_checked"] is False and value["live_jobs_checked"] is False,
                 "a page merged historical membership with another current observation")

    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    old_case_controls = [controls(path) for path in case_directories]

    def originals_unchanged() -> bool:
        return (all(controls(path) == before for path, before in zip(case_directories, old_case_controls))
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == before
                        for index, before in old_observations.items()))

    args_a = create_args(catalog_a, ordered, "restore-case-catalog-main")
    cli("create-response-discarded", "workspace-restore-case-catalog", "create", args_a, discard=True)
    receipt_a = cli("create-exact-replay", "workspace-restore-case-catalog", "create", args_a)
    recovered = discarded_addresses.pop("create-response-discarded") == hashlib.sha256(canonical(receipt_a).encode("utf-8")).hexdigest()
    _require(recovered and receipt_a["case_count"] == 3, "exact replay did not recover the discarded catalog receipt")
    first = page("first-two", catalog_a, 2)
    last = page("last-one", catalog_a, 2, first["next_cursor"])
    end = page("terminal-empty", catalog_a, 2, {"catalog_id": receipt_a["catalog_id"], "after_index": 3})
    first_repeated = page("first-two-repeated", catalog_a, 2)
    require_page(first, ordered_records[:2], ["available"] * 2)
    require_page(last, ordered_records[2:], ["available"])
    require_page(end, [], [])
    _require(first_repeated == first and last["next_cursor"] is None and end["next_cursor"] is None
             and [entry["index"] for value in (first, last) for entry in value["entries"]] == [1, 2, 3],
             "pagination did not preserve the explicit order or terminal boundary")
    stage("fixed-membership-and-recovered-receipt",
          "An explicit ordered three-case catalog preserves its receipt after a real discarded response and pages as two plus one entries.",
          {"catalog": receipt_a, "first": first, "last": last, "terminal": end,
           "discarded_response_recovered_exactly": recovered})

    late_record = cli("late-case-record", "workspace-restore-case", "record",
                      ["--job", str(private / "prepare-job-01"), "--case", str(late_case),
                       "--operation-id", "restore-case-catalog-late-record"])
    frozen_after_late = page("original-membership-after-late-case", catalog_a, 3)
    require_page(frozen_after_late, ordered_records, ["available"] * 3)
    receipt_b = cli("expanded-catalog-create", "workspace-restore-case-catalog", "create",
                    create_args(catalog_b, [*ordered, late_case], "restore-case-catalog-expanded"))
    expanded_first = page("expanded-first-three", catalog_b, 3)
    expanded_last = page("expanded-last-unlinked", catalog_b, 3, expanded_first["next_cursor"])
    require_page(expanded_last, [late_record], ["available"])
    _require(receipt_b["case_count"] == 4 and expanded_last["entries"][0]["current_case"]["link"] is None,
             "new membership did not preserve the explicitly included unlinked case")
    late_link = cli("late-case-link-existing-completed-attempt", "workspace-restore-case", "link-attempt",
                    ["--case", str(late_case), "--job", str(private / "case-attempt-job-01"),
                     "--operation-id", "restore-case-catalog-late-link"])
    expanded_linked = page("expanded-after-late-link", catalog_b, 16)
    require_page(expanded_linked, [*ordered_records, late_record], ["available"] * 4)
    _require(late_link["attempt_status"]["completed"] is True
             and expanded_linked["entries"][-1]["current_case"]["link"] == late_link
             and expanded_last["entries"][0]["current_case"]["link"] is None,
             "page metadata did not observe a later immutable link separately from fixed membership")
    stage("late-case-and-later-link",
          "A later case stays outside the original catalog; an explicit second catalog includes it and later observes its link to an already completed attempt.",
          {"original_catalog": receipt_a, "expanded_catalog": receipt_b, "late_case": late_record,
           "before_link": expanded_last, "after_link": expanded_linked, "historical_link": late_link,
           "new_attempt_created": False, "request_equivalence_verified": False})

    invalid_prefix = "workspace_restore_case_catalog_"
    invalid_catalog = private / "restore-case-catalog-invalid"
    invalid_calls = [
        ("duplicate-case-path", "create", create_args(invalid_catalog, [ordered[0], ordered[0]], "catalog-duplicate"), "invalid_input"),
        ("page-limit-zero", "page", ["--catalog", str(catalog_a), "--limit", "0"], "invalid_input"),
        ("page-limit-seventeen", "page", ["--catalog", str(catalog_a), "--limit", "17"], "invalid_input"),
        ("negative-cursor-index", "page", ["--catalog", str(catalog_a), "--catalog-id", receipt_a["catalog_id"], "--after-index", "-1"], "invalid_input"),
        ("cursor-beyond-membership", "page", ["--catalog", str(catalog_a), "--catalog-id", receipt_a["catalog_id"], "--after-index", "4"], "invalid_input"),
        ("cursor-from-another-catalog", "page", ["--catalog", str(catalog_a), "--catalog-id", receipt_b["catalog_id"], "--after-index", "2"], "invalid_input"),
        ("conflicting-operation", "create", create_args(catalog_a, ordered, "restore-case-catalog-other"), "conflict"),
        ("conflicting-explicit-order", "create", create_args(catalog_a, list(reversed(ordered)), "restore-case-catalog-main"), "conflict"),
        ("operation-length-bound", "create", create_args(invalid_catalog, ordered, "b" * 102), "invalid_input"),
        ("malformed-catalog-id", "page", ["--catalog", str(catalog_a), "--catalog-id", "wrcat_invalid", "--after-index", "1"], "invalid_input"),
        ("incomplete-cursor-pair", "page", ["--catalog", str(catalog_a), "--after-index", "1"], "invalid_input"),
    ]
    for label, action, arguments, suffix in invalid_calls:
        cli(label, "workspace-restore-case-catalog", action, arguments, expected_error=invalid_prefix + suffix)
    _require(not invalid_catalog.exists() and originals_unchanged(), "refused input changed existing controls or created an invalid catalog")

    catalog_controls = [controls(catalog_a), controls(catalog_b)]
    missing_held = private / "restore-case-catalog-missing-held"
    moved = False
    try:
        case_directories[0].rename(missing_held)
        moved = True
        unavailable_a = page("missing-case-original-catalog", catalog_a, 3)
        require_page(unavailable_a, ordered_records, ["available", "unavailable", "available"])
        replay_missing = cli("create-replay-with-missing-case", "workspace-restore-case-catalog", "create", args_a)
        unavailable_b = page("missing-case-expanded-catalog", catalog_b, 16)
        require_page(unavailable_b, [*ordered_records, late_record], ["available", "unavailable", "available", "available"])
        _require(replay_missing == receipt_a and unavailable_a["entries"][1]["current_case"]["link"] is None,
                 "a missing case lost its historical catalog membership or changed replay")
    finally:
        if moved:
            missing_held.rename(case_directories[0])
    restored_after_missing = page("missing-case-restored", catalog_a, 3)
    _require(restored_after_missing == frozen_after_late and originals_unchanged(), "restoring a missing path did not recover its same case controls")
    stage("missing-case-preserves-membership",
          "A missing case yields a per-entry unavailable observation while pinned historical membership and exact create replay remain available.",
          {"missing_original": unavailable_a, "missing_expanded": unavailable_b,
           "restored": restored_after_missing, "historical_create_receipt_equal": replay_missing == receipt_a,
           "original_case_control_files_preserved": True, "original_case_directory_identities_preserved": True})

    changed_held = private / "restore-case-catalog-original-held"
    original_held = late_swapped = False
    try:
        case_directories[1].rename(changed_held)
        original_held = True
        late_case.rename(case_directories[1])
        late_swapped = True
        changed = page("different-case-at-pinned-path", catalog_a, 3)
        require_page(changed, ordered_records, ["available", "available", "changed"])
        replay_changed = cli("create-replay-with-changed-case", "workspace-restore-case-catalog", "create", args_a)
        _require(replay_changed == receipt_a and changed["entries"][2]["current_case"]["link"] is None,
                 "a replacement directory was adopted as the pinned historical case")
    finally:
        if late_swapped:
            case_directories[1].rename(late_case)
        if original_held:
            changed_held.rename(case_directories[1])
    restored_after_change = page("different-case-originals-restored", catalog_a, 3)
    _require(restored_after_change == frozen_after_late and originals_unchanged(), "directory swap changed the original case control files")
    stage("changed-case-preserves-membership",
          "An existing later case temporarily occupies another member path; the different directory is reported changed and both originals are restored.",
          {"changed": changed, "restored": restored_after_change,
           "historical_create_receipt_equal": replay_changed == receipt_a,
           "case_files_edited": False, "both_original_paths_restored": True})

    detach_pairs = [(private / f"case-attempt-job-{index:02d}", private / f"restore-case-catalog-attempt-held-{index:02d}")
                    for index in range(1, 4)]
    detach_pairs.append((source_bundle, private / "restore-case-catalog-source-held"))
    detached: list[tuple[Path, Path]] = []
    try:
        for original, held in detach_pairs:
            original.rename(held)
            detached.append((original, held))
        offline_a = page("case-metadata-with-attempts-unavailable", catalog_a, 3)
        offline_detail = cli("separate-case-detail-with-attempt-unavailable", "workspace-restore-case", "show",
                             ["--case", str(case_directories[2])])
        offline_b = page("expanded-metadata-with-attempts-unavailable", catalog_b, 16)
        _require(offline_a == frozen_after_late and offline_b == expanded_linked
                 and offline_detail["original_evidence"]["state"] == "unchanged"
                 and offline_detail["current_attempt"]["state"] == "unavailable"
                 and offline_detail["link"] == baseline_cases[2]["historical_link"],
                 "metadata pages asserted current attempt health or depended on unavailable job/source paths")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    online_again = page("case-metadata-after-attempts-restored", catalog_a, 3)
    _require(online_again == frozen_after_late and originals_unchanged(), "restoring attempt/source paths changed catalog membership")
    stage("metadata-only-pages-and-separate-detail",
          "Case-control pages remain available with linked attempt jobs and source absent; ordinary case show separately reports the unavailable current attempt.",
          {"original_catalog_page": offline_a, "expanded_catalog_page": offline_b,
           "separate_case_detail": offline_detail, "restored_page": online_again,
           "old_blocked_jobs_moved": False, "all_detached_paths_restored": True})

    final_details = []
    for index, path in enumerate(case_directories):
        detail = cli(f"final-case-detail-{index + 1:02d}", "workspace-restore-case", "show", ["--case", str(path)])
        _require(detail == baseline_cases[index]["completed_show"], "final ordinary case detail changed after catalog operations")
        final_details.append(detail)
    final_receipt = cli("final-create-exact-replay", "workspace-restore-case-catalog", "create", args_a)
    final_a = page("final-original-catalog", catalog_a, 16)
    final_b = page("final-expanded-catalog", catalog_b, 16)
    _require(final_receipt == receipt_a and final_a["entries"] == frozen_after_late["entries"]
             and final_b == expanded_linked and [controls(catalog_a), controls(catalog_b)] == catalog_controls,
             "read-only pages changed private catalog controls or historical replay")
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    source_after, live_after = observe(source), observe(live)
    _require(originals_unchanged() and matches(source_before, source_after) and matches(live_before, live_after)
             and len(cli_outcomes) == 44 and len(rejections) == 14 and len(pages) == 18,
             "final controls, domain observations or ordinary command accounting differ from the fixed scenario")
    stage("originals-and-domain-history-preserved",
          "Both catalog controls, three original cases and blocked jobs retain their observations; completed details and R4/R5 domain history remain unchanged.",
          {"original_catalog": final_a, "expanded_catalog": final_b,
           "original_case_details": final_details, "catalog_control_metadata_unchanged": True,
           "original_case_control_files_unchanged": True, "original_case_directory_identities_preserved": True,
           "original_blocked_job_metadata_unchanged": True, "source_public_observations_unchanged": True,
           "live_public_observations_unchanged": True})

    artifacts = {
        "restore-case-catalog-receipts.json": {"original": receipt_a, "expanded": receipt_b, "final_replay": final_receipt},
        "restore-case-catalog-pages.json": pages,
        "restore-case-catalog-cli-outcomes.json": cli_outcomes,
        "restore-case-catalog-rejections.json": rejections,
        "restore-case-catalog-reply-recovery.json": {"actual_public_response_discarded": True,
                                                    "exact_replay_equal": recovered, "comparison_digest_exported": False},
        "restore-case-catalog-membership.json": {"explicit_original_order": ordered_records,
                                                "late_case": late_record, "late_link": late_link,
                                                "original_case_count": 3, "expanded_case_count": 4,
                                                "later_case_automatically_added": False, "new_attempt_created": False},
        "restore-case-catalog-control-observations.json": {"catalog_control_metadata_unchanged": True,
                                                          "original_case_control_files_unchanged": True,
                                                          "original_case_directory_identities_preserved": True,
                                                          "original_case_directory_times_compared": False,
                                                          "original_blocked_job_metadata_unchanged": True,
                                                          "all_detached_paths_restored": True},
        "restore-case-catalog-source-observations.json": {"before": source_before, "after": source_after},
        "restore-case-catalog-live-observations.json": {"before": live_before, "after": live_after},
        "restore-case-catalog-restored-detail-observations.json": {"before": [value["completed_show"] for value in baseline_cases],
                                                                 "after": final_details, "unavailable_attempt_detail": offline_detail},
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Explicit frozen case membership with bounded current metadata pages",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "case_catalog_stage_count": len(stages),
            "private_catalog_count": 2, "original_catalog_case_count": 3, "expanded_catalog_case_count": 4,
            "private_case_count": 4, "private_case_link_count": 4, "new_attempt_count": 0,
            "case_catalog_cli_child_process_count": len(cli_outcomes),
            "case_catalog_successful_cli_count": sum(value["returncode"] == 0 for value in cli_outcomes),
            "case_catalog_refused_cli_count": len(rejections), "page_response_count": len(pages),
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "new_interrupt_worker_process_count": 0, "operator_rejection_count": len(rejections),
            "discarded_catalog_response_count": 1, "discarded_catalog_response_recovered_exactly": recovered,
            "later_case_automatically_added": False, "later_link_observed": True,
            "missing_case_reported_without_losing_membership": True,
            "different_case_directory_reported_changed": True,
            "original_case_control_files_unchanged": True, "original_case_directory_identities_preserved": True,
            "original_blocked_jobs_still_blocked": True, "original_blocked_job_metadata_unchanged": True,
            "catalog_control_metadata_unchanged": True, "catalog_pages_check_original_evidence": False,
            "catalog_pages_check_live_jobs": False, "separate_case_detail_reports_unavailable_attempt": True,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "all_detached_paths_restored": True, "private_control_contents_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "original_identity_reconstructed": False, "request_equivalence_verified": False,
            "old_authority_activated": False, "external_fencing_established": False, "external_llm_used": False,
            "human_review_recorded": False, "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "Catalog membership and order come from explicit bounded input, not directory discovery. Each private catalog is immutable; a new one is needed to include a later case.",
            "Membership captures are sequential under individual case locks, not a common snapshot of all cases. Page entries separately observe current case controls and historical link receipts.",
            "A page reports pinned historical cases even when their path is missing or occupied by a different directory. These are per-entry observations, not repairs or ownership adoption.",
            "Page metadata does not inspect original evidence, current attempt jobs or DBs. Available case controls do not establish healthy linked attempts; ordinary case show provides separate current detail.",
            "One actual validated public create response is parsed and discarded, then recovered by exact replay using an ephemeral comparison address. That address and all private fingerprints stay unpublished.",
            "Temporary directory moves are restored in finally; original case control files and static directory identities are compared, excluding directory times changed by rename. Old blocked job directories never move.",
            "No new restore attempts, domain grants, credentials or decisions are created beyond the repeated baseline. The later case explicitly links an already completed attempt without proving request equivalence or global uniqueness.",
            "Partial catalog writes, interruption inside catalog creation, all concurrency schedules, hardware failure, retention/deletion and production capacity are not exercised.",
            "Programmed participants and local file observations do not establish human independence, semantic truth or external fencing. Public Store comparison excludes only top-level governance observed_at.",
        ],
    }
    write_json("restore-case-catalog-report.json", report)
    lines = [
        "# Фиксированный каталог случаев и текущие наблюдения метаданных", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий исторических случаев](restore-cases-report.md); его 287 файлов сохранены.",
        "Первый каталог получает три случая в явно заданном порядке. Второй явно включает более поздний четвёртый случай.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## История и текущие метаданные", "",
        "Настоящий успешный create-ответ проверен и отброшен; точный replay восстановил ту же квитанцию.",
        "Пагинация первого каталога возвращает 2 + 1 запись и пустую конечную страницу. Поздний случай автоматически не добавляется.",
        "После явного создания второго каталога его поздний случай получает link с уже завершённой попыткой.",
        "При недоступном либо заменённом пути исторический member остаётся; current_case отдельно сообщает unavailable или changed.", "",
        "Доступность case controls не означает доступность связанной попытки. Страницы не читают старое evidence или jobs.",
        "При временно скрытых attempt jobs и source bundle каталог читался, а обычный case show сообщил unavailable текущей попытки.",
        "Все пути возвращены в finally. Старые блокирующие jobs не перемещались и сохранили прежние fingerprints.", "",
        "## Публичные основания", "",
        "- [Квитанции](restore-case-catalog-receipts.json), [страницы](restore-case-catalog-pages.json), [состав](restore-case-catalog-membership.json).",
        "- [CLI](restore-case-catalog-cli-outcomes.json), [отказы](restore-case-catalog-rejections.json), [восстановление ответа](restore-case-catalog-reply-recovery.json).",
        "- [Наблюдения controls](restore-case-catalog-control-observations.json), [подробные case show](restore-case-catalog-restored-detail-observations.json).",
        "- [Исторический источник](restore-case-catalog-source-observations.json), [действующая среда](restore-case-catalog-live-observations.json), [машинный отчёт](restore-case-catalog-report.json).", "",
        "## Границы результата", "",
        f"Этапов: {len(stages)}; новых CLI: {len(cli_outcomes)}; ожидаемых отказов: {len(rejections)}; сохранённых страниц: {len(pages)}.",
        "Новых прерываний нет. Частичные записи каталога, аппаратные отказы, все расписания, удаление и предел нагрузки не воспроизводились.",
        "Частные control records, staging и контрольные суммы демонстратор не разбирает. Публикуются безопасные API/Store ответы и булевы наблюдения.",
        "Неизвестная identity старого plan остаётся неизвестной; операторская связь не доказывает равенство запросов.",
        "Внешние LLM, независимая экспертиза, истинность содержания и внешний fencing не установлены.", "",
    ]
    with (workspace / "restore-case-catalog-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
