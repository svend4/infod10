"""Resume a quarantined restore through ordinary file-sized CLI checkpoints.

Public Store observations and fixed safe operation summaries are the only
published inputs. Private plans, staging files, manifests, checksums and origin
rows are never parsed by this demonstration. Proposed Python remains data.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .source_transfer_chain_demo import CODE_AUTHOR
from .store import Store, WorkbenchError, canonical
from .workspace_backup_cli import _public_summary
from .workspace_backup_demo import run_workspace_backup_demo
from .workspace_restore_job_cli import _public_status


STATUS_FIELDS = {
    "rules_version", "job_id", "operation_id", "created_at", "workspace_id", "state",
    "copied_file_count", "total_file_count", "completed", "inspection_only",
    "domain_writes_allowed", "full_history_preserved", "old_authority_activated",
    "external_fencing_established", "result",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore job demonstration stopped: " + message)


def run_workspace_restore_job_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Prepare, pause, resume, recover a reply and inspect a complete restore."""
    from . import source_transfer, workspace_backup, workspace_restore_job

    workspace = Path(workspace).expanduser().resolve()
    baseline_report = run_workspace_backup_demo(workspace, workers)
    private = workspace / "private"
    source_bundle = private / "full-backup"
    destination = private / "job-restore"
    job_directory = private / "restore-job"
    operation_id = "restore-job-demo-main"
    source = Store(private / "next-workspace" / "state.sqlite3")
    source_backup = Store(source_bundle / "state.sqlite3")
    old_restore = Store(private / "full-restore" / "state.sqlite3")
    repository = Path(__file__).resolve().parents[1]
    administrator = read_credential(private / "next-admin.credential")
    old_administrator = read_credential(private / "admin.credential")
    code_author = read_credential(private / "next-code-author.credential")
    baseline_public_files = sorted(path.name for path in workspace.iterdir()
                                   if path.is_file() and path.suffix in {".json", ".md"})
    private_markers: list[str] = []
    for path in private.glob("*.credential"):
        value = read_credential(path)
        private_markers.extend((value, hashlib.sha256(value.encode("ascii")).hexdigest()))
    forbidden_fields = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "source_bundle", "original_manifest", "restore_manifest",
        "plan", "staging_path", "sha256", "schema_fingerprint",
    }

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not forbidden_fields.intersection(value), "a private restoration field reached public output")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers),
                     "a credential or its hash reached public output")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def safe_status(value: Any) -> dict[str, Any]:
        _require(isinstance(value, dict) and set(value) == STATUS_FIELDS,
                 "the restore API returned unsupported public fields")
        return public(_public_status(value))

    def safe_summary(value: Any) -> dict[str, Any]:
        return public(_public_summary(value, [], "inspect"))

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(),
                "access": store.snapshot_access()}

    def observations_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {key: child for key, child in value["governance"].items()
                                             if key != "observed_at"}}
        return canonical(comparable(before)) == canonical(comparable(after))

    stages: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    statuses: dict[str, dict[str, Any]] = {}
    discarded_reply_address: str | None = None

    def stage(label: str, explanation: str, value: Any) -> None:
        name = f"restore-job-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(name, {"label": label, "explanation": explanation, "observations": value})
        stages.append({"label": label, "explanation": explanation, "artifact": name})

    def rejected(label: str, action: Callable[[], Any], expected: str, watched: Store,
                 *, category: str) -> None:
        before = observe(watched)
        try:
            action()
        except WorkbenchError as error:
            _require(error.code == expected, "unexpected rejection for " + label + ": " + error.code)
            _require(observations_match(before, observe(watched)),
                     "a rejected operation changed public Store observations")
            rejections.append({"label": label, "category": category, "code": error.code,
                               "rejected": True, "public_observations_unchanged": True,
                               "comparison_excludes_only_governance_wrapper_observed_at": True})
            return
        raise RuntimeError("Restore job demonstration stopped: expected rejection for " + label)

    def arguments(action: str, job: Path, target: Path, operation: str) -> list[str]:
        if action == "prepare":
            return ["--bundle", str(source_bundle), "--destination", str(target),
                    "--job", str(job), "--operation-id", operation]
        if action == "step":
            return ["--job", str(job), "--limit", "1"]
        _require(action == "status", "unsupported demonstration command")
        return ["--job", str(job)]

    def start_cli(action: str, job: Path, target: Path, operation: str) -> subprocess.Popen[str]:
        return subprocess.Popen([sys.executable, "-m", "agentwiki", "workspace-restore", action,
                                 *arguments(action, job, target, operation)],
                                cwd=repository, shell=False, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)

    def finish_cli(process: subprocess.Popen[str], label: str, action: str,
                   *, discard_business_response: bool = False) -> dict[str, Any] | None:
        nonlocal discarded_reply_address
        try:
            stdout, stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate()
            raise RuntimeError("Restore job demonstration stopped: CLI child timed out") from error
        matched = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
        code = matched.group(1) if matched else None
        cli_outcomes.append({"label": label, "command": "workspace-restore", "action": action,
                             "child_process_id": process.pid, "returncode": process.returncode,
                             "error_code": code, "stderr_empty": not stderr, "shell": False,
                             "completed_child_process": True,
                             "business_response_discarded": discard_business_response})
        _require(process.returncode == 0 and not stderr,
                 "a restore CLI child failed with " + (code or "an unspecified error"))
        try:
            value = safe_status(json.loads(stdout))
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Restore job demonstration stopped: invalid CLI JSON") from error
        if discard_business_response:
            _require(value["completed"] is True, "the discarded business response was not a completed result")
            # This is an ephemeral comparison of an already public response.
            # Neither its address nor its business body is exported here.
            discarded_reply_address = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        return value

    def cli(label: str, action: str, *, job: Path = job_directory, target: Path = destination,
            operation: str = operation_id, discard_business_response: bool = False) -> dict[str, Any] | None:
        with start_cli(action, job, target, operation) as process:
            return finish_cli(process, label, action, discard_business_response=discard_business_response)

    captured = observe(source_backup)
    live_before = observe(source)
    old_restore_before = observe(old_restore)
    source_summary = safe_summary(workspace_backup.inspect_bundle(source_bundle))
    _require(captured["state"]["revision"] == 4 and captured["status"]["events"] == 36
             and live_before["state"]["revision"] == 5 and source_summary["archive_count"] == 2,
             "the full backup baseline is not the expected captured R4 and continued live R5")
    stage("complete-source-and-current-live-state",
          "A complete historical R4 bundle with two archives is available; the live source has continued to R5.",
          {"source_summary": source_summary, "captured_status": captured["status"],
           "live_status": live_before["status"], "captured_access": captured["access"]})

    prepared = cli("prepare-fixed-restore-operation", "prepare")
    _require(isinstance(prepared, dict) and prepared["state"] == "prepared"
             and prepared["copied_file_count"] == 0 and prepared["total_file_count"] == 3
             and prepared["completed"] is False and prepared["result"] is None,
             "prepare did not persist a fixed empty restore operation")
    statuses["prepared"] = prepared
    statuses["prepared_status"] = cli("read-prepared-status", "status")
    statuses["prepared_replay"] = cli("repeat-exact-prepare", "prepare")
    _require(statuses["prepared_status"] == statuses["prepared_replay"] == prepared,
             "prepare replay changed the immutable job identity or progress")
    rejected("missing-target-db-cannot-initialize-active-store", lambda: Store(destination / "state.sqlite3"),
             "store_snapshot_incomplete", source_backup, category="incomplete_boundary")
    _require(not (destination / "state.sqlite3").exists(),
             "opening a prepared missing database created a Store file")
    rejected("prepared-full-bundle-is-incomplete", lambda: workspace_backup.inspect_bundle(destination),
             "workspace_backup_incomplete", source_backup, category="incomplete_boundary")
    stage("prepared-job-blocks-uninitialized-store",
          "Prepare records one operation; no current DB is copied yet, and Store cannot initialize an active database at that path.",
          {"prepared": prepared, "rejections": rejections[-2:], "target_database_created": False})

    for label, action in (
        ("different-operation-cannot-rebind-job", lambda: workspace_restore_job.prepare_restore(
            source_bundle, destination, job_directory, operation_id="restore-job-other-operation")),
        ("different-destination-cannot-rebind-job", lambda: workspace_restore_job.prepare_restore(
            source_bundle, private / "job-other-target", job_directory, operation_id=operation_id)),
        ("different-source-cannot-rebind-job", lambda: workspace_restore_job.prepare_restore(
            private / "full-restore", destination, job_directory, operation_id=operation_id)),
    ):
        rejected(label, action, "workspace_restore_job_conflict", source_backup, category="request_binding")
    rejected("completed-unrelated-destination-is-not-adopted", lambda: workspace_restore_job.prepare_restore(
        source_bundle, private / "full-restore", private / "restore-job-unrelated-target",
        operation_id="restore-job-unrelated-target"), "workspace_restore_job_destination_exists", old_restore,
        category="destination")
    unrelated_job = private / "restore-job-unrelated"
    unrelated_job.mkdir(mode=0o700)
    unrelated_job.chmod(0o700)
    note = unrelated_job / "note.txt"
    note.write_text("An unrelated private directory is not a restore job.\n", encoding="utf-8")
    note.chmod(0o600)
    rejected("unrelated-existing-job-directory-is-not-adopted", lambda: workspace_restore_job.prepare_restore(
        source_bundle, private / "job-unrelated-existing", unrelated_job,
        operation_id="restore-job-unrelated-existing"), "workspace_restore_job_incomplete", source_backup,
        category="destination")
    _require({path.name for path in unrelated_job.iterdir()} == {"note.txt"}
             and note.read_text(encoding="utf-8") == "An unrelated private directory is not a restore job.\n"
             and not (private / "job-unrelated-existing").exists(),
             "an unrelated directory was changed or adopted as a restoration job")
    for limit in (0, 17):
        rejected("unsupported-file-limit-" + str(limit), lambda limit=limit: workspace_restore_job.step_restore(
            job_directory, limit=limit), "workspace_restore_job_invalid_input", source_backup,
            category="input_bound")
    _require(safe_status(workspace_restore_job.restore_status(job_directory)) == prepared,
             "rejected requests changed the prepared operation")
    stage("exact-request-binding-and-bounds",
          "Another request or preexisting unrelated destination is rejected; step limits remain within the explicit file bound.",
          {"prepared_status_unchanged": True, "rejections": rejections[2:]})

    first = cli("copy-one-current-database", "step")
    _require(isinstance(first, dict) and first["state"] == "copying"
             and first["copied_file_count"] == 1 and first["completed"] is False,
             "the first step did not copy exactly one current database")
    statuses["after_database"] = first
    partial = Store(destination / "state.sqlite3")
    partial_observation = observe(partial)
    _require(observations_match(captured, partial_observation)
             and partial_observation["access"]["quarantined"] is True,
             "the first committed file did not retain the historical captured Store")
    rejected("partial-db-enqueue-remains-quarantined", lambda: partial.enqueue(
        "restore-job-partial-write", "code_author", {}), "store_snapshot_quarantined", partial,
        category="partial_quarantine")
    rejected("partial-history-full-inspection-remains-blocked", lambda: workspace_backup.inspect_bundle(destination),
             "workspace_backup_incomplete", partial, category="incomplete_boundary")
    statuses["paused_after_database"] = cli("read-paused-database-checkpoint", "status")
    _require(statuses["paused_after_database"] == first,
             "status changed progress at the deliberate file boundary")
    stage("first-file-is-readable-quarantined-history",
          "One CLI process copies only the current DB. A new Store reads R4 in quarantine, while full history inspection remains incomplete.",
          {"status": first, "partial_store": partial_observation, "rejections": rejections[-2:]})

    second = cli("resume-with-one-ancestor-archive", "step")
    _require(isinstance(second, dict) and second["state"] == "copying"
             and second["copied_file_count"] == 2 and second["completed"] is False,
             "the second CLI process did not preserve the earlier file checkpoint")
    statuses["after_first_archive"] = second
    statuses["paused_after_first_archive"] = cli("read-paused-archive-checkpoint", "status")
    _require(statuses["paused_after_first_archive"] == second,
             "the second checkpoint did not survive a separate status process")
    stage("separate-process-resumes-next-file",
          "A later CLI process resumes from the copied DB and adds one older archive; it does not recreate the restore identity.",
          {"status": second, "same_job_id": second["job_id"] == prepared["job_id"],
           "current_store_unchanged": observations_match(partial_observation, observe(partial))})

    _require(cli("final-step-with-discarded-business-response", "step", discard_business_response=True) is None,
             "the deliberately discarded business reply was retained")
    completed = cli("recover-completed-result-through-status", "status")
    _require(isinstance(completed, dict) and completed["state"] == "completed"
             and completed["copied_file_count"] == completed["total_file_count"] == 3
             and completed["completed"] is True and completed["full_history_preserved"] is True
             and discarded_reply_address == hashlib.sha256(canonical(completed).encode("utf-8")).hexdigest(),
             "status did not recover the exact validated completed public response")
    statuses["recovered_completed"] = completed
    _require(all(value[key] == prepared[key] for value in (first, second, completed)
                 for key in ("job_id", "operation_id", "created_at", "workspace_id", "total_file_count")),
             "resuming a file checkpoint changed the fixed restore identity")
    replayed_step = cli("repeat-already-completed-step", "step")
    statuses["completed_step_replay"] = replayed_step
    _require(replayed_step == completed, "completed step replay changed the historical result")
    restored = Store(destination / "state.sqlite3")
    restored_observation = observe(restored)
    full_summary = safe_summary(workspace_backup.inspect_bundle(destination))
    _require(observations_match(captured, restored_observation)
             and completed["result"] == full_summary
             and full_summary["kind"] == "restore" and full_summary["revision"] == 4
             and full_summary["created_at"] == prepared["created_at"],
             "the completed target differs from capture or its recovered result")
    summary_pins = set(source_summary) - {"kind", "created_at"}
    _require(all(source_summary[key] == full_summary[key] for key in summary_pins),
             "the completed restore changed captured history or ancestor receipts")
    reply_recovery = {"model": "validated_completed_local_CLI_business_response_discarded",
                      "business_response_discarded": True, "recovered_response_exactly_equal": True,
                      "comparison_address_exported": False, "same_fixed_job_identity": True,
                      "process_or_network_crash_simulated": False}
    stage("lost-business-reply-recovered-without-new-restore",
          "The final step completes the full bundle; its business reply is discarded, and a new status process recovers the exact same public result.",
          {"recovery": reply_recovery, "completed_status": completed, "full_summary": full_summary})

    detachment_locations = (
        (source_bundle, private / "restore-job-detached-bundle"),
        (private / "source", private / "restore-job-detached-original"),
        (private / "new-workspace", private / "restore-job-detached-previous"),
        (private / "next-workspace", private / "restore-job-detached-current"),
    )
    old_stores = {"original": Store(private / "source" / "state.sqlite3"),
                  "previous": Store(private / "new-workspace" / "state.sqlite3"), "current": source}
    old_observations = {label: observe(store) for label, store in old_stores.items()}
    archive_before = {"oldest": observe(Store(source_bundle / "archives" / "history-0001.sqlite3")),
                      "nearest": observe(Store(source_bundle / "archives" / "history.sqlite3"))}
    moved: list[tuple[Path, Path]] = []
    try:
        for original, detached in detachment_locations:
            original.rename(detached)
            moved.append((original, detached))
        detached_status = cli("completed-status-without-any-original-source-path", "status")
        detached_step = cli("completed-step-without-any-original-source-path", "step")
        detached_store = observe(Store(destination / "state.sqlite3"))
        detached_summary = safe_summary(workspace_backup.inspect_bundle(destination))
        detached_archives = {"oldest": observe(Store(destination / "archives" / "history-0001.sqlite3")),
                             "nearest": observe(Store(destination / "archives" / "history.sqlite3"))}
        _require(detached_status == detached_step == completed and detached_summary == full_summary
                 and observations_match(restored_observation, detached_store)
                 and all(observations_match(archive_before[label], value)
                         for label, value in detached_archives.items()),
                 "a completed job or target depends on an unavailable original path")
    finally:
        for original, detached in reversed(moved):
            detached.rename(original)
    _require(all(observations_match(old_observations[label], observe(store))
                 for label, store in old_stores.items())
             and observations_match(captured, observe(source_backup)),
             "restoring renamed source paths changed historical or live records")
    detached_result = {"unavailable_directory_count": len(detachment_locations),
                       "mechanism": "reversible directory rename with finally restoration",
                       "completed_status_equal": detached_status == completed,
                       "completed_step_equal": detached_step == completed,
                       "full_summary_equal": detached_summary == full_summary,
                       "restored_records_equal": True, "archive_records_equal": True,
                       "all_original_paths_restored": True, "process_crash_simulated": False}
    statuses["completed_status_sources_unavailable"] = detached_status
    statuses["completed_step_sources_unavailable"] = detached_step
    stage("completed-operation-needs-no-original-source-path",
          "The source bundle and all three former workspace directories are temporarily unavailable. Completed status, replay and all target history still read successfully.",
          {"observations": detached_result, "target_store": detached_store,
           "target_archives": detached_archives})

    concurrent_job = private / "restore-job-concurrent"
    concurrent_destination = private / "job-restore-concurrent"
    concurrent_operation = "restore-job-demo-concurrent"
    concurrent_prepared = cli("prepare-concurrent-restore-job", "prepare", job=concurrent_job,
                              target=concurrent_destination, operation=concurrent_operation)
    processes: list[subprocess.Popen[str]] = []
    try:
        for _ in range(2):
            processes.append(start_cli("step", concurrent_job, concurrent_destination, concurrent_operation))
        concurrent_steps = [finish_cli(process, f"concurrent-one-file-step-{index}", "step")
                            for index, process in enumerate(processes, 1)]
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate()
    _require(all(isinstance(value, dict) for value in concurrent_steps)
             and sorted(value["copied_file_count"] for value in concurrent_steps) == [1, 2]
             and all(value["job_id"] == concurrent_prepared["job_id"] for value in concurrent_steps),
             "concurrent callers did not observe distinct serialized file checkpoints")
    concurrent_completed = cli("complete-concurrent-restore-job", "step", job=concurrent_job,
                               target=concurrent_destination, operation=concurrent_operation)
    concurrent_status = cli("read-completed-concurrent-status", "status", job=concurrent_job,
                            target=concurrent_destination, operation=concurrent_operation)
    _require(concurrent_status == concurrent_completed and concurrent_completed["completed"] is True
             and observations_match(captured, observe(Store(concurrent_destination / "state.sqlite3"))),
             "the concurrent restore did not produce the captured quarantined history")
    concurrency = {"prepared": concurrent_prepared, "steps": concurrent_steps,
                   "completed": concurrent_completed, "status": concurrent_status,
                   "cli_processes_launched_before_waiting": 2,
                   "observed_completed_file_counts": sorted(value["copied_file_count"] for value in concurrent_steps),
                   "lock_acquisition_overlap_measured": False, "forced_scheduling_order": False}
    stage("concurrent-callers-share-one-file-progress",
          "Two separate CLI processes are launched before waiting. Their one-file steps return successive checkpoints, followed by one complete quarantined restore.",
          concurrency)

    rejected("restored-job-enqueue-remains-blocked", lambda: restored.enqueue(
        "restore-job-forbidden-write", "code_author", {}), "store_snapshot_quarantined", restored,
        category="quarantine")
    rejected("restored-job-submit-remains-blocked", lambda: restored.submit(
        "restore-job-forbidden-write", CODE_AUTHOR, 1, {}, credential=code_author),
        "store_snapshot_quarantined", restored, category="quarantine")
    rejected("restored-job-publish-remains-blocked", lambda: restored.publish(
        workspace / "restore-job-forbidden-release"), "store_snapshot_quarantined", restored,
        category="quarantine")
    _require(not (workspace / "restore-job-forbidden-release").exists(),
             "publication from a quarantined target created a release directory")
    rejected("restored-job-cannot-authorize-new-full-backup", lambda: workspace_backup.create_backup(
        destination / "state.sqlite3", private / "restore-job-forbidden-backup", credential=administrator),
        "workspace_backup_source_invalid", restored, category="quarantine")
    rejected("restored-job-cannot-authorize-new-transfer", lambda: source_transfer.transfer_source(
        destination / "state.sqlite3", private / "restore-job-forbidden-transfer", credential=administrator,
        new_administrator_id="restore-job-forbidden-admin", new_credential=old_administrator,
        operation_id="restore-job-forbidden-transfer"), "source_transfer_source_invalid", restored,
        category="quarantine")
    statuses["completed_final"] = cli("read-final-unchanged-completed-status", "status")
    final_summary = safe_summary(workspace_backup.inspect_bundle(destination))
    final_observation = observe(restored)
    _require(statuses["completed_final"] == completed and final_summary == full_summary
             and observations_match(restored_observation, final_observation)
             and observations_match(live_before, observe(source))
             and observations_match(old_restore_before, observe(old_restore))
             and observations_match(captured, observe(source_backup)),
             "completed restoration or rejected operations changed copied or original records")
    stage("completed-history-stays-inspection-only",
          "Repeated recovery and refused writes preserve the completed target, the source bundle, the live R5 workspace and the earlier restore.",
          {"completed_status": statuses["completed_final"], "full_summary": final_summary,
           "rejections": [value for value in rejections if value["category"] == "quarantine"],
           "live_status": source.status(), "restored_status": restored.status()})

    data_artifacts = {
        "restore-job-statuses.json": statuses,
        "restore-job-cli-outcomes.json": cli_outcomes,
        "restore-job-rejections.json": rejections,
        "restore-job-reply-recovery.json": reply_recovery,
        "restore-job-source-summary.json": source_summary,
        "restore-job-source-observations.json": {"captured": captured, "final": observe(source_backup)},
        "restore-job-live-observations.json": {"before": live_before, "after": observe(source)},
        "restore-job-partial-observations.json": partial_observation,
        "restore-job-target-observations.json": {"initial": restored_observation, "final": final_observation},
        "restore-job-archive-observations.json": {"source": archive_before, "target_without_sources": detached_archives},
        "restore-job-completed-summary.json": full_summary,
        "restore-job-detached-path-observations.json": detached_result,
        "restore-job-concurrency.json": concurrency,
    }
    for name, value in data_artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": "workspace-restore-job-1",
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "File-sized resumable restore with stable result recovery and historical quarantine",
        "summary": {
            "baseline_public_file_count": len(baseline_public_files), "restore_job_stage_count": len(stages),
            "restore_job_cli_child_process_count": len(cli_outcomes),
            "total_cli_child_process_count": baseline_report["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "main_job_total_data_file_count": completed["total_file_count"],
            "observed_serial_file_counts": [prepared["copied_file_count"], first["copied_file_count"],
                                           second["copied_file_count"], completed["copied_file_count"]],
            "captured_revision": captured["state"]["revision"], "restored_revision": final_observation["state"]["revision"],
            "live_revision": live_before["state"]["revision"], "captured_event_count": captured["status"]["events"],
            "restored_event_count": final_observation["status"]["events"], "live_event_count": live_before["status"]["events"],
            "complete_archive_count": full_summary["archive_count"], "operator_rejection_count": len(rejections),
            "discarded_business_response_count": sum(value["business_response_discarded"] for value in cli_outcomes),
            "completed_response_recovered_exactly": True, "completed_step_replayed_without_source": True,
            "directories_temporarily_unavailable": len(detachment_locations), "all_original_paths_restored": True,
            "concurrent_cli_child_process_count": len(processes), "concurrent_file_counts": concurrency["observed_completed_file_counts"],
            "restored_workspace_quarantined": True, "old_authority_activated": False,
            "full_history_preserved": True, "partial_checkpoint_is_ordinary_completed_step": True,
            "partial_staging_file_or_hardlink_alias_simulated": False, "process_or_hardware_crash_simulated": False,
            "private_origin_contents_read_by_demo": False, "private_manifest_contents_read_by_demo": False,
            "private_plan_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "external_fencing_established": False, "external_llm_used": False,
            "human_review_recorded": False, "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(data_artifacts),
        "limits": [
            "The demonstration pauses at successful ordinary one-file step boundaries; it does not kill a process or reproduce an in-flight filesystem crash.",
            "The final CLI response is parsed and validated, then its business value is discarded. An internal address of that already public response is compared with recovery and is never exported.",
            "Two CLI callers are launched before waiting; lock-acquisition overlap and all possible schedules are not measured or forced.",
            "The source bundle and three old workspace directories are reversibly renamed only after completion, and restored in finally.",
            "The immutable private plan and known staging cache are not parsed by the demonstration. Partial staging files, hardlink aliases and malformed metadata are not simulated.",
            "Existing unknown destinations and mismatching committed data are never adopted or overwritten by the operation API; only the refusal of ordinary existing/request-conflict cases is exercised here.",
            "The target remains a historical quarantined bundle. Copied credentials, leases and opinions are not active authority.",
            "Declared dependency provenance is retained, but query maps remain empty and no separate stale imported read/query case is induced.",
            "Public equality excludes only the changing top-level governance observed_at wrapper, preserving expired flags and full addressed proof bodies.",
            "Programmed roles, private checksums and local writer controls do not establish human independence, factual truth, external fencing or measured production capacity.",
        ],
    }
    write_json("restore-job-report.json", report)
    lines = [
        "# Пошаговое восстановление полного исторического комплекта", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий полного backup](workspace-report.md); его публичные основания",
        "сохранены без перезаписи. Новый процесс восстанавливает тот же снимок R4 отдельными файловыми",
        "шагами. Действующий источник остаётся на R5; новые credentials или роли не выдаются.", "",
        "## Последовательность", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Подготовка и продолжение", "",
        "Prepare закрепляет один запрос, назначение и постоянную идентичность результата. Три отдельных",
        "шага limit=1 дают счётчики 1, 2 и 3: текущая база, старший архив и ближайший архив. До копии DB",
        "Store не может создать активную базу; после первого шага читает R4 в карантине. Полный inspect",
        "остаётся незавершённым до последнего файла и общего manifest.", "",
        "Это обычные успешно завершённые команды и паузы между ними. Падение процесса внутри копирования,",
        "частичный staging-файл, временная hardlink-пара и повреждение SQL в сценарии не создавались.", "",
        "## Восстановление ответа и самодостаточность", "",
        "Ответ последнего CLI-шагa проверен и отброшен как бизнес-результат. Новый status возвращает тот же",
        "полный публичный ответ; повтор completed step также сохраняет его. Внутренний адрес сравнения",
        "публичного ответа не публикуется. Это модель потери ответа после завершённого локального вызова.", "",
        "Затем source bundle и три прежних рабочих каталога временно переименованы. Completed status,",
        "повтор step, полный inspect, Store и оба архива читаются без этих путей. Имена восстановлены в",
        "finally; исходные наблюдения совпали. Вторая операция показывает два независимо запущенных",
        "CLI-процесса с последовательными файловыми checkpoints; порядок захвата блокировки не задавался.", "",
        "## Публичные основания", "",
        "- [Статусы](restore-job-statuses.json), [CLI](restore-job-cli-outcomes.json), [отказы](restore-job-rejections.json).",
        "- [Восстановление ответа](restore-job-reply-recovery.json), [полная сводка](restore-job-completed-summary.json).",
        "- [Источник снимка](restore-job-source-observations.json), [живая база](restore-job-live-observations.json), [исходная сводка](restore-job-source-summary.json).",
        "- [Первый checkpoint](restore-job-partial-observations.json), [полный target](restore-job-target-observations.json), [архивы](restore-job-archive-observations.json).",
        "- [Недоступность путей](restore-job-detached-path-observations.json), [два CLI caller](restore-job-concurrency.json), [машинный отчёт](restore-job-report.json).", "",
        "## Границы выполненного", "",
        f"Новых этапов: {len(stages)}; новых CLI-процессов: {len(cli_outcomes)}; ожидаемых отказов: {len(rejections)}.",
        f"Файлов данных: {completed['total_file_count']}; наблюдавшийся прогресс: 0 → 1 → 2 → 3.",
        f"Восстановленная ревизия: {final_observation['state']['revision']}; события: {final_observation['status']['events']}; архивов: {full_summary['archive_count']}.",
        f"Поздняя действующая база: ревизия {live_before['state']['revision']}, событий {live_before['status']['events']}.",
        "Прежние допуски и решения остаются историей. Новые записи и публикация из восстановленной базы",
        "блокируются. Внешние LLM, человеческая экспертиза, аппаратные отказы и предельная нагрузка не моделировались.", "",
        "Сверка публичных наблюдений исключает только вычисляемый верхний governance.observed_at. Полные",
        "адресованные доказательства, timestamps и expired-признаки сохраняются. Частные планы, staging,",
        "контрольные суммы, manifests, базы и credentials остаются вне публикации.", "",
    ]
    with (workspace / "restore-job-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
