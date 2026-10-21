"""Demonstrate immutable advisory retention snapshots over selected cases.

The ordinary driver reads validated public responses and lstat metadata only.
It never reads private control-record bodies or performs archive/deletion work.
Temporary path moves are reversed in finally while old blocked jobs stay put.
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
from .workspace_restore_case_catalog_demo import run_workspace_restore_case_catalog_demo
from .workspace_restore_case_catalog_cli import _public_response as _catalog_response
from .workspace_restore_case_retention_cli import _public_response as _retention_response
from .workspace_restore_cases_cli import _public_response as _case_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-1"
MAX_RETAIN_SECONDS = 315_360_000


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention demonstration stopped: " + message)


def run_workspace_restore_case_retention_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Preserve four advisory plans without changing their source cases or jobs."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_catalog_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 305, "the preceding scenario did not produce 305 public files")
    baseline_cases = json.loads((workspace / "restore-cases-cases.json").read_text())
    catalog_receipts = json.loads((workspace / "restore-case-catalog-receipts.json").read_text())
    membership = json.loads((workspace / "restore-case-catalog-membership.json").read_text())
    catalog_a, catalog_b = private / "restore-case-catalog-main", private / "restore-case-catalog-expanded"
    case_directories = [private / f"restore-case-{index:02d}" for index in range(1, 4)]
    case_directories.append(private / "restore-case-catalog-late")
    attempt_jobs = [private / f"case-attempt-job-{index:02d}" for index in range(1, 4)]
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").stat().st_size
    ordered_cases = [*membership["explicit_original_order"], membership["late_case"]]
    baseline_details = [value["completed_show"] for value in baseline_cases]
    late_detail = {**baseline_details[0], "case": membership["late_case"], "link": membership["late_link"],
                   "current_attempt": {"state": "available", "status": membership["late_link"]["attempt_status"],
                                       "error_code": None}}
    ordered_details = [baseline_details[2], baseline_details[0], baseline_details[1], late_detail]
    plan_directories = {
        "minimum": private / "restore-case-retention-minimum",
        "long": private / "restore-case-retention-long",
        "missing_case": private / "restore-case-retention-missing-case",
        "missing_attempt": private / "restore-case-retention-missing-attempt",
    }
    operations = {name: "restore-case-retention-" + name.replace("_", "-") for name in plan_directories}
    markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "source_bundle", "original_manifest", "restore_manifest",
        "owner", "staging_path", "sha256", "schema_fingerprint", "request_digest", "plan_path",
        "plan_directory", "catalog_path", "case_path", "case_directory", "catalog_directory", "parent", "pins",
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
        """Observe control metadata, excluding directory times changed by rename."""
        directory = path.lstat()
        return {"directory": (directory.st_dev, directory.st_ino, directory.st_uid, stat.S_IMODE(directory.st_mode)),
                "files": {entry.name: (details.st_dev, details.st_ino, details.st_uid, details.st_mode,
                                        details.st_nlink, details.st_size, details.st_mtime_ns, details.st_ctime_ns)
                          for entry in sorted(path.iterdir()) for details in [entry.lstat()]}}

    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    plans: dict[str, Any] = {}
    discarded_addresses: dict[str, str] = {}

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"restore-case-retention-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> dict[str, Any] | None:
        _require(command in {"workspace-restore-case-retention", "workspace-restore-case-catalog",
                             "workspace-restore-case", "workspace-restore"}, "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore case retention demonstration stopped: ordinary CLI timed out") from error
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
                if command == "workspace-restore-case-retention":
                    value = _retention_response(value, action)
                elif command == "workspace-restore-case-catalog":
                    value = _catalog_response(value, action)
                elif command == "workspace-restore-case":
                    value = _case_response(value, action)
                else:
                    value = _public_status(value)
                value = public(value)
            except (TypeError, ValueError) as error:
                raise RuntimeError("Restore case retention demonstration stopped: invalid public CLI response") from error
            if discard:
                discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
                return None
            return value

    def plan_args(path: Path, retain: int, operation: str, *, catalog: Path = catalog_b,
                  after: int = 0, limit: int = 4) -> list[str]:
        return ["--catalog", str(catalog), "--plan", str(path), "--retain-seconds", str(retain),
                "--operation-id", operation, "--after-index", str(after), "--limit", str(limit)]

    def show(label: str, name: str) -> dict[str, Any]:
        return cli(label, "workspace-restore-case-retention", "show", ["--plan", str(plan_directories[name])])

    def require_snapshot(value: dict[str, Any], retain: int, candidates: list[int], *,
                         missing_case: bool = False, missing_attempt: bool = False) -> None:
        _require(value["catalog"] == catalog_receipts["expanded"] and value["policy"] == {"retain_seconds": retain}
                 and value["after_index"] == 0 and value["limit"] == 4
                 and [entry["index"] for entry in value["entries"]] == [1, 2, 3, 4]
                 and [entry["case"] for entry in value["entries"]] == ordered_cases,
                 "a retention snapshot lost the selected historical range")
        for entry, expected_detail in zip(value["entries"], ordered_details):
            index = entry["index"]
            reasons = ["retention_period_not_elapsed"] if retain else []
            if index in {2, 4}:
                reasons.append("original_identity_unknown")
            observation = {"state": "available", "detail": expected_detail, "error_code": None}
            if missing_case and index == 3:
                reasons.append("case_unavailable")
                observation = {"state": "unavailable", "detail": None,
                               "error_code": "workspace_restore_case_retention_case_unavailable"}
            elif missing_attempt and index == 3:
                reasons.append("attempt_unavailable")
                observation["detail"] = {**expected_detail,
                                         "current_attempt": {"state": "unavailable", "status": None,
                                                             "error_code": "workspace_restore_case_attempt_unavailable"}}
            _require(entry["reasons"] == reasons and entry["observation"] == observation
                     and entry["disposition"] == ("review_candidate" if index in candidates else "held"),
                     "a retention row confused fixed policy, current evidence or advisory classification")
        _require(value["advisory_only"] is True and value["inspection_only"] is True
                 and all(value[key] is False for key in ("domain_writes_allowed", "original_files_modified",
                                                        "archival_authorized", "deletion_authorized",
                                                        "old_authority_activated", "external_fencing_established")),
                 "a retention plan granted authority or modified original records")

    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    observed_controls = [*case_directories, catalog_a, catalog_b, *attempt_jobs]
    original_controls = [controls(path) for path in observed_controls]

    def originals_unchanged() -> bool:
        return (all(controls(path) == before for path, before in zip(observed_controls, original_controls))
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == before
                        for index, before in old_observations.items()))

    minimum_args = plan_args(plan_directories["minimum"], 0, operations["minimum"])
    cli("minimum-plan-response-discarded", "workspace-restore-case-retention", "plan", minimum_args, discard=True)
    plans["minimum"] = cli("minimum-plan-exact-replay", "workspace-restore-case-retention", "plan", minimum_args)
    recovered = discarded_addresses.pop("minimum-plan-response-discarded") == hashlib.sha256(canonical(plans["minimum"]).encode("utf-8")).hexdigest()
    minimum_show = show("minimum-plan-show", "minimum")
    require_snapshot(plans["minimum"], 0, [1, 3])
    _require(recovered and minimum_show == plans["minimum"], "historical replay did not recover the discarded plan response")
    stage("captured-policy-and-recovered-response",
          "A zero-retention advisory snapshot holds unknown original identities and identifies two known completed cases for review; exact replay recovers a real discarded response.",
          {"snapshot": plans["minimum"], "show": minimum_show,
           "actual_discarded_response_recovered_exactly": recovered, "archive_or_deletion_performed": False})

    plans["long"] = cli("long-retention-plan", "workspace-restore-case-retention", "plan",
                         plan_args(plan_directories["long"], MAX_RETAIN_SECONDS, operations["long"]))
    long_show = show("long-retention-plan-show", "long")
    require_snapshot(plans["long"], MAX_RETAIN_SECONDS, [])
    _require(long_show == plans["long"], "show recomputed a saved retention threshold")
    stage("minimum-age-holds-all-members",
          "A separately saved long retention policy holds every selected case while preserving unknown-identity reasons and fixed evaluation time.",
          {"snapshot": plans["long"], "show": long_show, "minimum_snapshot_unchanged": minimum_show == plans["minimum"]})

    invalid_plan = private / "restore-case-retention-invalid"
    prefix = "workspace_restore_case_retention_"
    invalid_calls = [
        ("negative-retention", plan_args(invalid_plan, -1, "retention-negative"), "invalid_input"),
        ("retention-above-bound", plan_args(invalid_plan, MAX_RETAIN_SECONDS + 1, "retention-too-long"), "invalid_input"),
        ("zero-range-limit", plan_args(invalid_plan, 0, "retention-zero-limit", limit=0), "invalid_input"),
        ("range-limit-seventeen", plan_args(invalid_plan, 0, "retention-large-limit", limit=17), "invalid_input"),
        ("negative-range-start", plan_args(invalid_plan, 0, "retention-negative-start", after=-1), "invalid_input"),
        ("empty-terminal-range", plan_args(invalid_plan, 0, "retention-empty-range", after=4), "invalid_input"),
        ("operation-length-bound", plan_args(invalid_plan, 0, "b" * 102), "invalid_input"),
        ("plan-equals-catalog", plan_args(catalog_b, 0, "retention-same-directory"), "invalid_input"),
        ("conflicting-operation", plan_args(plan_directories["minimum"], 0, "retention-other-operation"), "conflict"),
        ("conflicting-retention-policy", plan_args(plan_directories["minimum"], 1, operations["minimum"]), "conflict"),
        ("conflicting-source-catalog", plan_args(plan_directories["minimum"], 0, operations["minimum"], catalog=catalog_a), "conflict"),
        ("conflicting-selected-range", plan_args(plan_directories["minimum"], 0, operations["minimum"], limit=3), "conflict"),
    ]
    for label, arguments, suffix in invalid_calls:
        cli(label, "workspace-restore-case-retention", "plan", arguments, expected_error=prefix + suffix)
    _require(not invalid_plan.exists() and originals_unchanged(), "refused input changed originals or reserved an invalid plan")

    missing_held = private / "restore-case-retention-case-held"
    moved = False
    try:
        case_directories[1].rename(missing_held)
        moved = True
        plans["missing_case"] = cli("missing-case-plan", "workspace-restore-case-retention", "plan",
                                     plan_args(plan_directories["missing_case"], 0, operations["missing_case"]))
        missing_case_show = show("missing-case-plan-show", "missing_case")
        require_snapshot(plans["missing_case"], 0, [1], missing_case=True)
        _require(missing_case_show == plans["missing_case"], "a missing case disappeared from the saved selected range")
    finally:
        if moved:
            missing_held.rename(case_directories[1])
    missing_case_after_restore = show("missing-case-plan-show-after-restore", "missing_case")
    _require(missing_case_after_restore == plans["missing_case"] and originals_unchanged(),
             "restoring a case path rewrote its historical retention blocker")
    stage("missing-case-remains-held-in-history",
          "A missing known case becomes a held row with its pinned historical receipt; restoring the path does not rewrite that saved observation.",
          {"snapshot": plans["missing_case"], "show_while_missing": missing_case_show,
           "show_after_path_restored": missing_case_after_restore, "original_case_path_restored": True})

    attempt_held = private / "restore-case-retention-attempt-held"
    moved = False
    try:
        attempt_jobs[1].rename(attempt_held)
        moved = True
        plans["missing_attempt"] = cli("unavailable-attempt-plan", "workspace-restore-case-retention", "plan",
                                        plan_args(plan_directories["missing_attempt"], 0, operations["missing_attempt"]))
        missing_attempt_show = show("unavailable-attempt-plan-show", "missing_attempt")
        unavailable_detail = cli("ordinary-case-detail-with-unavailable-attempt", "workspace-restore-case", "show",
                                 ["--case", str(case_directories[1])])
        require_snapshot(plans["missing_attempt"], 0, [1], missing_attempt=True)
        _require(missing_attempt_show == plans["missing_attempt"]
                 and plans["missing_attempt"]["entries"][2]["observation"]["detail"] == unavailable_detail,
                 "a retention snapshot confused unavailable attempt detail with case availability")
    finally:
        if moved:
            attempt_held.rename(attempt_jobs[1])
    missing_attempt_after_restore = show("unavailable-attempt-plan-show-after-restore", "missing_attempt")
    _require(missing_attempt_after_restore == plans["missing_attempt"] and originals_unchanged(),
             "restoring an attempt path recomputed a historical retention result")
    stage("unavailable-attempt-is-a-separate-blocker",
          "Available case controls retain unchanged original evidence while an unavailable linked attempt holds that row; the ordinary case detail agrees.",
          {"snapshot": plans["missing_attempt"], "ordinary_case_detail": unavailable_detail,
           "show_after_path_restored": missing_attempt_after_restore, "original_attempt_path_restored": True})

    plan_controls = {name: controls(path) for name, path in plan_directories.items()}
    detach_originals = [catalog_a, catalog_b, *case_directories, *attempt_jobs, source_bundle]
    detached: list[tuple[Path, Path]] = []
    offline_shows: dict[str, Any] = {}
    try:
        for index, original in enumerate(detach_originals, 1):
            held = private / f"restore-case-retention-offline-held-{index:02d}"
            original.rename(held)
            detached.append((original, held))
        for name, snapshot in plans.items():
            offline_shows[name] = show("historical-offline-show-" + name.replace("_", "-"), name)
            _require(offline_shows[name] == snapshot, "saved plan show depended on unavailable live source paths")
        offline_replay = cli("historical-offline-exact-plan-replay", "workspace-restore-case-retention", "plan", minimum_args)
        _require(offline_replay == plans["minimum"], "historical plan replay accessed its missing source catalog")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    _require(originals_unchanged() and all(controls(path) == plan_controls[name] for name, path in plan_directories.items()),
             "historical reads changed saved plans or original control files")
    stage("saved-snapshots-outlive-live-path-availability",
          "Show for all four plans and exact replay remain historical with catalogs, cases, linked job directories and source unavailable; every path is restored.",
          {"shows": offline_shows, "minimum_replay": offline_replay,
           "all_detached_paths_restored": True, "old_blocked_jobs_moved": False,
           "plan_control_metadata_unchanged": True})

    final_catalog_a = cli("final-original-catalog-page", "workspace-restore-case-catalog", "page",
                          ["--catalog", str(catalog_a), "--limit", "16"])
    final_catalog_b = cli("final-expanded-catalog-page", "workspace-restore-case-catalog", "page",
                          ["--catalog", str(catalog_b), "--limit", "16"])
    baseline_pages = json.loads((workspace / "restore-case-catalog-pages.json").read_text())
    _require(final_catalog_a == baseline_pages["final-original-catalog"]
             and final_catalog_b == baseline_pages["final-expanded-catalog"],
             "retention planning changed current catalog observations")
    final_details = []
    for index, path in enumerate(case_directories[:3], 1):
        detail = cli(f"final-original-case-detail-{index:02d}", "workspace-restore-case", "show", ["--case", str(path)])
        _require(detail == baseline_details[index - 1], "a retention plan changed current original case detail")
        final_details.append(detail)
    final_minimum = show("final-minimum-plan-show", "minimum")
    _require(final_minimum == plans["minimum"], "final show changed the plan evaluation time or disposition")
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    source_after, live_after = observe(source), observe(live)
    _require(originals_unchanged() and matches(source_before, source_after) and matches(live_before, live_after)
             and all(controls(path) == plan_controls[name] for name, path in plan_directories.items())
             and len(cli_outcomes) == 38 and len(rejections) == 15 and len(plans) == 4,
             "final original controls, domain observations or ordinary command accounting differ from the scenario")
    stage("originals-and-domain-history-preserved",
          "Advisory planning leaves case/catalog/job controls and domain history unchanged; original blocked jobs still refuse and saved plans retain fixed decisions.",
          {"catalogs": {"original": final_catalog_a, "expanded": final_catalog_b},
           "original_case_details": final_details, "minimum_snapshot": final_minimum,
           "original_control_metadata_unchanged": True, "old_blocked_job_metadata_unchanged": True,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "archival_performed": False, "deletion_performed": False})

    artifacts = {
        "restore-case-retention-plans.json": plans,
        "restore-case-retention-cli-outcomes.json": cli_outcomes,
        "restore-case-retention-rejections.json": rejections,
        "restore-case-retention-reply-recovery.json": {"actual_public_response_discarded": True,
                                                       "exact_replay_equal": recovered, "comparison_digest_exported": False},
        "restore-case-retention-policy-observations.json": {
            name: {"policy": snapshot["policy"], "created_at": snapshot["created_at"],
                   "candidate_indices": [entry["index"] for entry in snapshot["entries"] if entry["disposition"] == "review_candidate"],
                   "held_indices": [entry["index"] for entry in snapshot["entries"] if entry["disposition"] == "held"],
                   "reasons_by_index": {str(entry["index"]): entry["reasons"] for entry in snapshot["entries"]}}
            for name, snapshot in plans.items()},
        "restore-case-retention-offline-observations.json": {"shows": offline_shows, "minimum_replay": offline_replay,
                                                             "all_detached_paths_restored": True},
        "restore-case-retention-control-observations.json": {"original_control_metadata_unchanged": True,
                                                             "original_directory_times_compared": False,
                                                             "old_blocked_job_metadata_unchanged": True,
                                                             "plan_control_metadata_unchanged": True,
                                                             "all_detached_paths_restored": True,
                                                             "archival_performed": False, "deletion_performed": False},
        "restore-case-retention-source-observations.json": {"before": source_before, "after": source_after},
        "restore-case-retention-live-observations.json": {"before": live_before, "after": live_after},
        "restore-case-retention-restored-detail-observations.json": {"before": baseline_details, "after": final_details,
                                                                    "unavailable_attempt_detail": unavailable_detail},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "public artifact accounting differs from the fixed scenario")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Advisory retention snapshots with fixed policy, explicit holds and historical replay",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "retention_stage_count": len(stages),
            "private_retention_plan_count": len(plans), "selected_case_count_per_plan": 4,
            "minimum_review_candidate_count": 2, "minimum_held_count": 2,
            "long_retention_review_candidate_count": 0, "long_retention_held_count": 4,
            "missing_case_review_candidate_count": 1, "missing_case_held_count": 3,
            "unavailable_attempt_review_candidate_count": 1, "unavailable_attempt_held_count": 3,
            "retention_cli_child_process_count": len(cli_outcomes),
            "retention_successful_cli_count": sum(value["returncode"] == 0 for value in cli_outcomes),
            "retention_refused_cli_count": len(rejections),
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "new_interrupt_worker_process_count": 0, "operator_rejection_count": len(rejections),
            "discarded_plan_response_count": 1, "discarded_plan_response_recovered_exactly": recovered,
            "show_and_replay_with_live_paths_unavailable": True, "all_detached_paths_restored": True,
            "original_control_metadata_unchanged": True, "old_blocked_job_metadata_unchanged": True,
            "original_blocked_jobs_still_blocked": True, "plan_control_metadata_unchanged": True,
            "original_identity_reconstructed": False, "request_equivalence_verified": False,
            "new_restore_attempt_count": 0, "archival_performed": False, "deletion_performed": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_stage_contents_read_by_demo": False,
            "private_fingerprints_exported": False, "old_authority_activated": False,
            "external_fencing_established": False, "external_llm_used": False,
            "human_review_recorded": False, "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "Every plan captures an explicitly selected nonempty bounded catalog range, one fixed policy and one evaluation time. Member observations remain sequential, not a common filesystem transaction.",
            "Review candidates are advisory classifications. Every response keeps archival_authorized and deletion_authorized false; no archive, deletion or old authority activation occurs.",
            "Unknown original identity is held without reconstruction. A long retention period, unavailable case and unavailable attempt preserve separate deterministic reasons.",
            "A real successful validated plan response is discarded and recovered by exact replay. The comparison address and private control data stay unpublished.",
            "All four saved snapshots remain readable with their live catalogs, cases, linked job directories and source absent. This is historical reading, not a fresh validation of those unavailable objects.",
            "Temporary directory moves are restored in finally. Control-file identities and static directory identities are compared, excluding directory times changed by rename; old blocked job directories never move.",
            "No new jobs, credentials, grants or domain decisions are created beyond the repeated baseline. All original case/catalog observations and source R4/live R5 histories are preserved.",
            "Changed evidence, changed attempts, partial plan writes, interruption during capture, all concurrency schedules, hardware failure, storage removal and production capacity are not exercised.",
            "Local file observations and programmed participants do not establish semantic truth, human independence, external authorization or external fencing. Public Store comparison excludes only upper governance observed_at.",
        ],
    }
    write_json("restore-case-retention-report.json", report)
    lines = [
        "# Совещательные планы хранения и сохранённые причины удержания", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий каталога](restore-case-catalog-report.md); его 305 файлов сохранены.",
        "Четыре плана сохраняют выбранный диапазон, политику и фиксированное время оценки. Операций архивации и удаления нет.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Политика и исторические наблюдения", "",
        "При retain_seconds=0 два известных завершённых случая получают review_candidate; две неизвестные исходные identity остаются held.",
        "Отдельный длинный срок удерживает все четыре случая. Временное отсутствие известного case либо его linked attempt даёт отдельную причину удержания.",
        "Возврат исходных путей не переписывает сохранённые решения. Show и точный replay сохраняют тот же исторический snapshot и время оценки.",
        "Настоящий успешный ответ plan проверен и отброшен; точный повтор восстановил ту же квитанцию со всеми строками.", "",
        "При временно скрытых catalogs, cases, attempt job directories и source bundle все четыре show и точный replay оставались доступны.",
        "Все пути возвращены в finally. Старые blocked jobs не перемещались; текущие catalog pages, case details и R4/R5 история сохранились.",
        "Review candidate требует отдельного рассмотрения. archival_authorized=false и deletion_authorized=false сохраняются во всех планах.", "",
        "## Публичные основания", "",
        "- [Планы](restore-case-retention-plans.json), [политики и причины](restore-case-retention-policy-observations.json).",
        "- [CLI](restore-case-retention-cli-outcomes.json), [отказы](restore-case-retention-rejections.json), [восстановление ответа](restore-case-retention-reply-recovery.json).",
        "- [Историческое чтение без live paths](restore-case-retention-offline-observations.json), [сохранность controls](restore-case-retention-control-observations.json).",
        "- [Подробные case show](restore-case-retention-restored-detail-observations.json), [источник](restore-case-retention-source-observations.json), [живая среда](restore-case-retention-live-observations.json), [машинный отчёт](restore-case-retention-report.json).", "",
        "## Границы результата", "",
        f"Этапов: {len(stages)}; новых CLI: {len(cli_outcomes)}; ожидаемых отказов: {len(rejections)}; сохранённых планов: {len(plans)}.",
        "Новых прерываний нет. Частичные записи, аппаратные отказы, все расписания, удаление и предел нагрузки не воспроизводились.",
        "Частные control records и staging демонстратор не разбирает. Публикуются безопасные API/Store ответы и булевы наблюдения.",
        "Неизвестная исходная identity не реконструируется; план не предоставляет полномочий архивации или удаления.",
        "Внешние LLM, независимая экспертиза, истинность содержания и внешний fencing не установлены.", "",
    ]
    with (workspace / "restore-case-retention-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
