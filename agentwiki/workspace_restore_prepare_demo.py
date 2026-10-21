"""Resume only proven preparation metadata after fixed worker interruptions.

Only public status/Store records and narrow lstat topology are published. The
driver never parses private plans, owner records, manifests, stage bytes,
checksums or origin rows, and never manually repairs a refused operation.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .store import Store, WorkbenchError, canonical
from .workspace_backup_cli import _public_summary
from .workspace_restore_interrupt_demo import run_workspace_restore_interrupt_demo
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_worker import BOUNDARIES, RULES_VERSION


_STAGE = re.compile(r"\.restore-wrj_[0-9a-f]{32}-(0[0-6])\.part\Z")
_BLOCKED = {"plan_partial": "workspace_restore_job_manifest_invalid",
            "intent_durable": "workspace_restore_job_conflict",
            "owner_partial": "workspace_restore_job_manifest_invalid"}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore preparation demonstration stopped: " + message)


def _kill_at_boundary(repository: Path, source: Path, job: Path, target: Path,
                      operation: str, boundary: str) -> dict[str, Any]:
    """Require a fixed ACK and SIGKILL reap; bound cleanup on failure."""
    _require(boundary in BOUNDARIES, "unsupported fixed preparation boundary")
    marker = {"rules_version": RULES_VERSION, "boundary": boundary, "checkpoint_reached": True}
    expected_line = (json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    process = subprocess.Popen(
        [sys.executable, "-m", "agentwiki.workspace_restore_prepare_worker", "--bundle", str(source),
         "--destination", str(target), "--job", str(job), "--operation-id", operation, "--boundary", boundary],
        cwd=repository, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _require(process.stdout is not None and process.stderr is not None, "worker pipes are unavailable")
        received = bytearray()
        deadline = time.monotonic() + 60
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while b"\n" not in received:
                remaining = deadline - time.monotonic()
                _require(remaining > 0, "worker acknowledgement timed out")
                if not selector.select(min(remaining, 1)):
                    continue
                block = os.read(process.stdout.fileno(), 4096)
                _require(bool(block), "worker exited before its acknowledgement")
                received.extend(block)
                _require(len(received) <= 4096, "worker acknowledgement exceeded its byte bound")
        _require(bytes(received) == expected_line, "worker acknowledgement has an unsupported body")
        _require(process.poll() is None, "acknowledged worker is no longer alive")
        process.kill()
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Restore preparation demonstration stopped: killed worker was not reaped in time") from error
        _require(process.returncode == -signal.SIGKILL and stdout == b"" and stderr == b"",
                 "worker did not terminate with only its safe acknowledgement")
        return {"boundary": boundary, "acknowledgement": marker, "child_process_id": process.pid,
                "child_alive_after_acknowledgement": True, "signal_name": "SIGKILL",
                "signal_number": int(signal.SIGKILL), "returncode": process.returncode,
                "completed_child_process": True, "business_response_received": False,
                "additional_stdout_empty": True, "stderr_empty": True, "shell": False,
                "worker_alarm_seconds": 90, "acknowledgement_deadline_seconds": 60}
    finally:
        if process.poll() is None:
            process.kill()
        try:
            process.communicate(timeout=15)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _metadata(job: Path, target: Path, source_size: int) -> tuple[dict[str, bool], dict[str, Any]]:
    """Observe fixed control paths and allowlisted staging metadata, not bytes."""
    names = {"plan": job / ".agentwiki-restore-job-plan.json",
             "owner_record": job / ".agentwiki-restore-job-owner.json",
             "lock": job / ".agentwiki-restore-job.lock", "target_directory": target,
             "snapshot_intent": target / ".agentwiki-snapshot-intent.json",
             "workspace_intent": target / ".agentwiki-workspace-intent.json",
             "snapshot_manifest": target / ".agentwiki-snapshot-manifest.json",
             "outer_manifest": target / ".agentwiki-workspace-manifest.json",
             "database": target / "state.sqlite3", "archives_directory": target / "archives"}
    stages: dict[str, Path] = {}
    for path in job.iterdir():
        matched = _STAGE.fullmatch(path.name)
        if matched:
            _require(matched.group(1) not in stages, "more than one fixed staging ordinal was found")
            stages[matched.group(1)] = path
    names.update({"staging_" + index: path for index, path in stages.items()})

    def details(path: Path) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None

    observed = {key: details(path) for key, path in names.items()}
    meta_stage, data_stage = observed.get("staging_01"), observed.get("staging_03")
    workspace_intent = observed["workspace_intent"]
    same_pair = (meta_stage is not None and workspace_intent is not None
                 and (meta_stage.st_dev, meta_stage.st_ino) == (workspace_intent.st_dev, workspace_intent.st_ino)
                 and meta_stage.st_nlink == workspace_intent.st_nlink == 2)
    archive_entries = tuple(sorted(path.name for path in names["archives_directory"].iterdir())) if observed["archives_directory"] else ()
    safe = {
        "plan_present": observed["plan"] is not None,
        "plan_nonempty": observed["plan"] is not None and observed["plan"].st_size > 0,
        "owner_record_present": observed["owner_record"] is not None,
        "owner_record_nonempty": observed["owner_record"] is not None and observed["owner_record"].st_size > 0,
        "target_directory_present": observed["target_directory"] is not None,
        "snapshot_intent_present": observed["snapshot_intent"] is not None,
        "workspace_intent_present": workspace_intent is not None,
        "snapshot_manifest_present": observed["snapshot_manifest"] is not None,
        "archives_directory_present": observed["archives_directory"] is not None,
        "archive_data_absent": not archive_entries,
        "database_present": observed["database"] is not None,
        "outer_manifest_present": observed["outer_manifest"] is not None,
        "metadata_stage_present": meta_stage is not None,
        "metadata_stage_nonempty": meta_stage is not None and meta_stage.st_size > 0,
        "metadata_stage_single_link": meta_stage is not None and meta_stage.st_nlink == 1,
        "metadata_own_hardlink_pair": same_pair,
        "data_stage_present": data_stage is not None,
        "data_stage_proper_prefix": data_stage is not None and 0 < data_stage.st_size < source_size,
        "data_stage_single_link": data_stage is not None and data_stage.st_nlink == 1,
        "all_observed_files_private": all(value.st_uid == os.geteuid() and (
            (stat.S_ISDIR(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o700)
            or (stat.S_ISREG(value.st_mode) and stat.S_IMODE(value.st_mode) == 0o600))
            for value in observed.values() if value is not None),
    }
    # Only equality booleans escape this function's caller. Private inode,
    # timestamps, byte counts and entry names remain ephemeral metadata.
    identity = {key: None if value is None else (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
        value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    ) for key, value in observed.items()}
    identity["job_entries"] = tuple(sorted(path.name for path in job.iterdir()))
    identity["target_entries"] = tuple(sorted(path.name for path in target.iterdir())) if observed["target_directory"] else ()
    identity["archive_entries"] = archive_entries
    return safe, identity


def run_workspace_restore_prepare_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Refuse three unproven states and resume six metadata-only operations."""
    from . import workspace_backup

    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_interrupt_demo(workspace, workers)
    private = workspace / "private"
    source_bundle = private / "full-backup"
    repository = Path(__file__).resolve().parents[1]
    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    previous = Store(private / "interrupt-restore-05" / "state.sqlite3")
    source_size = (source_bundle / "state.sqlite3").stat().st_size
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "source_bundle", "original_manifest", "restore_manifest",
        "plan", "owner", "staging_path", "sha256", "schema_fingerprint", "request_digest",
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

    stages: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    worker_outcomes: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    targets: dict[str, Any] = {}
    target_archives: dict[str, Any] = {}

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"restore-prepare-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def cli(label: str, action: str, job: Path, target: Path, operation: str,
            expected_error: str | None = None) -> dict[str, Any]:
        arguments = ["--job", str(job)]
        if action == "prepare":
            arguments += ["--bundle", str(source_bundle), "--destination", str(target), "--operation-id", operation]
        elif action == "step":
            arguments += ["--limit", "16"]
        else:
            _require(action in {"status", "resume-prepare"}, "unsupported ordinary CLI action")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", "workspace-restore", action, *arguments],
                              cwd=repository, shell=False, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore preparation demonstration stopped: ordinary CLI timed out") from error
            code_match = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
            code = code_match.group(1) if code_match else None
            cli_outcomes.append({"label": label, "command": "workspace-restore", "action": action,
                                 "child_process_id": process.pid, "returncode": process.returncode,
                                 "error_code": code, "stderr_empty": not stderr,
                                 "completed_child_process": True, "shell": False})
            if expected_error is not None:
                _require(process.returncode != 0 and code == expected_error and not stdout,
                         "an unsafe preparation state did not refuse with the expected safe code")
                rejection = {"label": label, "category": "unproven_preparation", "action": action,
                             "code": code, "rejected": True}
                rejections.append(rejection)
                return rejection
            _require(process.returncode == 0 and not stderr, "ordinary preparation CLI failed")
            try:
                return public(_public_status(json.loads(stdout)))
            except (TypeError, ValueError) as error:
                raise RuntimeError("Restore preparation demonstration stopped: invalid ordinary CLI response") from error

    captured, live_before, previous_before = observe(source), observe(live), observe(previous)
    source_summary = public(_public_summary(workspace_backup.inspect_bundle(source_bundle), [], "inspect"))
    archives = {"oldest": observe(Store(source_bundle / "archives" / "history-0001.sqlite3")),
                "nearest": observe(Store(source_bundle / "archives" / "history.sqlite3"))}
    _require(len(baseline_files) == 246 and source_summary["archive_count"] == 2
             and captured["state"]["revision"] == 4 and captured["status"]["events"] == 36
             and live_before["state"]["revision"] == 5 and live_before["status"]["events"] == 59,
             "baseline is not the expected historical R4 and current R5 workspace")
    stage("historical-source-before-preparation",
          "The preceding interruption scenario remains intact; nine fresh jobs use the same quarantined R4 bundle with two archives.",
          {"source_summary": source_summary, "captured_status": captured["status"], "live_status": live_before["status"]})

    for index, boundary in enumerate(BOUNDARIES, 1):
        job = private / f"prepare-job-{index:02d}"
        target = private / f"prepare-restore-{index:02d}"
        operation = "restore-prepare-" + boundary.replace("_", "-")
        worker = public(_kill_at_boundary(repository, source_bundle, job, target, operation, boundary))
        worker_outcomes.append(worker)
        before, identity_before = _metadata(job, target, source_size)
        _require(before["plan_present"] and before["plan_nonempty"] and before["all_observed_files_private"]
                 and not before["database_present"] and before["archive_data_absent"]
                 and not before["outer_manifest_present"], "interruption did not leave a private preparation-only state")
        if boundary in {"plan_partial", "plan_durable"}:
            _require(not before["target_directory_present"] and not before["owner_record_present"],
                     "the planned-only boundary unexpectedly created or adopted a destination")
        else:
            _require(before["target_directory_present"] and before["snapshot_intent_present"],
                     "the guarded target boundary has no standard quarantine intent")
            _require(before["owner_record_present"] is (boundary != "intent_durable"),
                     "the ownership record does not match the selected boundary")
            if boundary != "intent_durable":
                _require(before["owner_record_nonempty"], "the selected owner boundary left an empty control record")
        if boundary in {"intent_durable", "owner_partial", "owner_durable"}:
            _require(not before["workspace_intent_present"] and not before["snapshot_manifest_present"]
                     and not before["metadata_stage_present"], "an early control boundary advanced into later metadata")
        elif boundary == "metadata_partial":
            _require(before["metadata_stage_present"] and before["metadata_stage_nonempty"]
                     and before["metadata_stage_single_link"] and not before["workspace_intent_present"],
                     "metadata-prefix boundary did not retain its own unlinked staging file")
        elif boundary == "metadata_linked":
            _require(before["metadata_own_hardlink_pair"], "metadata-link boundary has no proven own hardlink pair")
        elif boundary in {"preparation_ready", "data_staging_preserved"}:
            _require(before["snapshot_intent_present"] and before["workspace_intent_present"]
                     and before["snapshot_manifest_present"] and before["archives_directory_present"]
                     and not before["metadata_stage_present"], "completed preparation lacks its metadata layout")
        if boundary == "data_staging_preserved":
            _require(before["data_stage_present"] and before["data_stage_proper_prefix"] and before["data_stage_single_link"],
                     "data-cache guard did not retain its proper-prefix staging file")
        else:
            _require(not before["data_stage_present"], "an early preparation boundary unexpectedly staged database data")
        if boundary in _BLOCKED:
            refusals = []
            for action in ("prepare", "status", "step", "resume-prepare"):
                refusal = cli(boundary + "-refuse-" + action, action, job, target, operation, _BLOCKED[boundary])
                _require(_metadata(job, target, source_size)[1] == identity_before,
                         "a refused command modified an immutable control record or target")
                refusal["private_metadata_unchanged"] = True
                refusals.append(refusal)
            after, identity_after = _metadata(job, target, source_size)
            _require(before == after and identity_before == identity_after,
                     "a refused operation modified an immutable control record or adopted the destination")
            if boundary in {"intent_durable", "owner_partial"}:
                _require(before["snapshot_intent_present"], "blocked Store probe has no quarantine intent")
                try:
                    Store(target / "state.sqlite3")
                except WorkbenchError as error:
                    _require(error.code == "store_snapshot_incomplete", "guarded partial target returned another Store refusal")
                    rejections.append({"label": boundary + "-store-incomplete", "category": "incomplete_snapshot",
                                       "action": "Store", "code": error.code, "rejected": True})
                else:
                    raise RuntimeError("Restore preparation demonstration stopped: partial target initialized an active Store")
                _require(_metadata(job, target, source_size)[1] == identity_before,
                         "opening a guarded incomplete target modified preparation metadata")
            case = {"boundary": boundary, "worker": worker, "outcome": "refused",
                    "expected_error_code": _BLOCKED[boundary], "refusals": refusals,
                    "topology_after_kill": before, "topology_after_refusals": after,
                    "private_metadata_unchanged": True, "destination_adopted": False,
                    "manual_repair_performed": False, "Store_probe_performed": boundary != "plan_partial"}
            cases.append(case)
            stage(boundary.replace("_", "-"),
                  "An incomplete immutable control record or missing ownership proof remains refused by all four ordinary commands; no manual repair is attempted.", case)
            continue

        detached = private / f"prepare-source-detached-{index:02d}"
        moved = False
        try:
            source_bundle.rename(detached)
            moved = True
            prepared = cli(boundary + "-readonly-prepare-offline", "prepare", job, target, operation)
            status = cli(boundary + "-readonly-status-offline", "status", job, target, operation)
            readonly_topology, readonly_identity = _metadata(job, target, source_size)
            _require(prepared == status and prepared["state"] == "prepared" and prepared["copied_file_count"] == 0
                     and prepared["total_file_count"] == 3 and before == readonly_topology and identity_before == readonly_identity,
                     "ordinary prepare/status changed partial preparation or required the source")
            resumed = cli(boundary + "-metadata-only-resume-offline", "resume-prepare", job, target, operation)
            resumed_topology, resumed_identity = _metadata(job, target, source_size)
            repeated = cli(boundary + "-repeat-metadata-resume-offline", "resume-prepare", job, target, operation)
            resumed_status = cli(boundary + "-status-after-metadata-resume", "status", job, target, operation)
            _require(prepared == resumed == repeated == resumed_status and resumed_topology["snapshot_intent_present"]
                     and resumed_topology["workspace_intent_present"] and resumed_topology["snapshot_manifest_present"]
                     and resumed_topology["archives_directory_present"] and resumed_topology["archive_data_absent"]
                     and not resumed_topology["database_present"] and not resumed_topology["outer_manifest_present"]
                     and not resumed_topology["metadata_stage_present"]
                     and _metadata(job, target, source_size)[1] == resumed_identity,
                     "metadata-only resume copied data, left metadata staging or changed its repeated result")
            data_cache_unchanged = None
            metadata_stage_proper_prefix = None
            if boundary == "metadata_partial":
                metadata_stage_proper_prefix = (identity_before.get("staging_01") is not None
                    and resumed_identity.get("workspace_intent") is not None
                    and 0 < identity_before["staging_01"][5] < resumed_identity["workspace_intent"][5])
                _require(metadata_stage_proper_prefix, "metadata staging was not a proper prefix of the installed intent size")
            if boundary == "data_staging_preserved":
                _require(before["data_stage_present"] and before["data_stage_proper_prefix"] and before["data_stage_single_link"],
                         "data-cache guard did not retain a proper-prefix staging file")
                data_cache_unchanged = identity_before.get("staging_03") == resumed_identity.get("staging_03")
                _require(data_cache_unchanged and resumed_topology["data_stage_present"],
                         "metadata-only resume changed the data staging inode, size or timestamps")
            installed_metadata_inodes_preserved = all(
                value is None or (resumed_identity.get(key) is not None and value[:2] == resumed_identity[key][:2])
                for key, value in identity_before.items()
                if key in {"plan", "owner_record", "snapshot_intent", "workspace_intent", "snapshot_manifest"}
            )
            _require(installed_metadata_inodes_preserved, "metadata resume replaced an existing immutable record")
        finally:
            if moved:
                detached.rename(source_bundle)

        completed = cli(boundary + "-ordinary-data-step", "step", job, target, operation)
        completed_status = cli(boundary + "-ordinary-completed-status", "status", job, target, operation)
        _, completed_identity_before = _metadata(job, target, source_size)
        completed_resume = cli(boundary + "-completed-metadata-resume-noop", "resume-prepare", job, target, operation)
        _require(_metadata(job, target, source_size)[1] == completed_identity_before,
                 "metadata-only resume changed an already completed bundle or job")
        _require(completed == completed_status == completed_resume and completed["completed"] is True
                 and completed["copied_file_count"] == 3
                 and all(prepared[key] == completed[key] for key in ("job_id", "operation_id", "created_at", "workspace_id", "total_file_count")),
                 "data continuation or completed metadata replay changed the fixed operation")
        full_summary = public(_public_summary(workspace_backup.inspect_bundle(target), [], "inspect"))
        restored = Store(target / "state.sqlite3")
        target_observation = observe(restored)
        observed_archives = {"oldest": observe(Store(target / "archives" / "history-0001.sqlite3")),
                             "nearest": observe(Store(target / "archives" / "history.sqlite3"))}
        _require(matches(captured, target_observation) and canonical(full_summary) == canonical(completed["result"])
                 and all(matches(archives[label], value) for label, value in observed_archives.items()),
                 "completed historical Store or full bundle differs from the captured source")
        final_topology, _ = _metadata(job, target, source_size)
        _require(final_topology["database_present"] and final_topology["outer_manifest_present"]
                 and not final_topology["metadata_stage_present"] and not final_topology["data_stage_present"],
                 "completed data step left an incomplete bundle or staging cache")
        for action in ("enqueue", "publish"):
            forbidden_release = workspace / f"restore-prepare-forbidden-release-{index:02d}"
            watched = observe(restored)
            try:
                if action == "enqueue":
                    restored.enqueue("restore-prepare-forbidden-write", "code_author", {})
                else:
                    restored.publish(forbidden_release)
            except WorkbenchError as error:
                _require(error.code == "store_snapshot_quarantined" and matches(watched, observe(restored))
                         and not forbidden_release.exists(), "rejected write or publish changed the quarantined target")
                rejections.append({"label": boundary + "-quarantine-" + action, "category": "quarantine",
                                   "action": action, "code": error.code, "rejected": True,
                                   "public_observations_unchanged": True})
            else:
                raise RuntimeError("Restore preparation demonstration stopped: target quarantine allowed a write")
        case = {"boundary": boundary, "worker": worker, "outcome": "completed",
                "readonly_prepare": prepared, "readonly_status": status, "metadata_resume": resumed,
                "metadata_resume_repeated": repeated, "metadata_resume_status": resumed_status,
                "completed_step": completed, "completed_status": completed_status, "completed_resume": completed_resume,
                "full_summary": full_summary, "topology_after_kill": before,
                "topology_after_readonly_calls": readonly_topology, "topology_after_metadata_resume": resumed_topology,
                "topology_after_data_completion": final_topology,
                "readonly_calls_changed_metadata": False, "metadata_resume_without_source": True,
                "metadata_resume_copied_data": False, "source_bundle_restored": True,
                "installed_metadata_inodes_preserved": installed_metadata_inodes_preserved,
                "data_staging_metadata_unchanged": data_cache_unchanged,
                "metadata_staging_proper_prefix_observed": metadata_stage_proper_prefix,
                "metadata_own_hardlink_pair_repaired": boundary == "metadata_linked",
                "fixed_operation_identity_preserved": True, "full_history_preserved": True,
                "completed_metadata_resume_preserved_metadata": True,
                "old_authority_activated": False}
        cases.append(case)
        targets[boundary] = target_observation
        target_archives[boundary] = observed_archives
        stage(boundary.replace("_", "-"),
              "Without the source bundle, ordinary readonly calls preserve metadata and resume-prepare finishes only metadata; the returned source then supports ordinary full data continuation.", case)

    captured_final, live_final, previous_final = observe(source), observe(live), observe(previous)
    _require(matches(captured, captured_final) and matches(live_before, live_final) and matches(previous_before, previous_final)
             and all(matches(archives[label], observe(Store(source_bundle / "archives" / name)))
                     for label, name in (("oldest", "history-0001.sqlite3"), ("nearest", "history.sqlite3"))),
             "preparation recovery changed an existing historical or live workspace")
    stage("historical-and-live-records-preserved",
          "Three unproven operations remain refused; six completed targets preserve quarantine, captured history, both archives and the live R5 workspace.",
          {"source_unchanged": True, "live_unchanged": True, "previous_restore_unchanged": True,
           "archives_unchanged": True, "rejections": rejections})
    artifacts = {
        "restore-prepare-cases.json": cases,
        "restore-prepare-worker-outcomes.json": worker_outcomes,
        "restore-prepare-cli-outcomes.json": cli_outcomes,
        "restore-prepare-rejections.json": rejections,
        "restore-prepare-source-summary.json": source_summary,
        "restore-prepare-source-observations.json": {"before": captured, "after": captured_final},
        "restore-prepare-live-observations.json": {"before": live_before, "after": live_final},
        "restore-prepare-previous-observations.json": {"before": previous_before, "after": previous_final},
        "restore-prepare-target-observations.json": targets,
        "restore-prepare-archive-observations.json": {"source": archives, "targets": target_archives},
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Source-independent metadata preparation with fail-closed immutable control records",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "restore_prepare_stage_count": len(stages),
            "restore_prepare_case_count": len(cases), "restore_prepare_blocked_case_count": len(_BLOCKED),
            "restore_prepare_completed_case_count": len(targets), "restore_prepare_worker_process_count": len(worker_outcomes),
            "restore_prepare_cli_child_process_count": len(cli_outcomes),
            "restore_prepare_child_process_count": len(worker_outcomes) + len(cli_outcomes),
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": (baseline["summary"]["total_cli_and_interrupt_worker_process_count"]
                                                            + len(cli_outcomes) + len(worker_outcomes)),
            "actual_sigkill_count": len(worker_outcomes), "metadata_resume_without_source_case_count": len(targets),
            "operator_rejection_count": len(rejections), "data_staging_metadata_preserved": True,
            "unproven_destination_adopted": False, "manual_control_record_repair_performed": False,
            "metadata_resume_copied_data": False, "all_original_paths_restored": True,
            "captured_revision": captured["state"]["revision"], "captured_event_count": captured["status"]["events"],
            "live_revision": live_final["state"]["revision"], "live_event_count": live_final["status"]["events"],
            "complete_archive_count": source_summary["archive_count"], "restored_workspace_quarantined": True,
            "full_history_preserved": True, "old_authority_activated": False,
            "private_plan_contents_read_by_demo": False, "private_owner_contents_read_by_demo": False,
            "private_manifest_contents_read_by_demo": False, "private_origin_contents_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "hardware_or_power_failure_simulated": False, "external_fencing_established": False,
            "external_llm_used": False, "human_review_recorded": False,
            "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "Nine fresh three-data-file jobs are interrupted only after a fixed trusted acknowledgement, then killed with SIGKILL and reaped. Local process interruption does not simulate power, kernel or storage failure.",
            "A partial immutable plan, an intent without its owner record and a partial immutable owner record remain refused by prepare, status, step and resume-prepare. The driver never manufactures or repairs those records.",
            "Six proven plans resume preparation with the source bundle temporarily unavailable. Resume-prepare installs only metadata ordinals zero through two and an empty archive directory, without copying database or archive data or issuing new authority.",
            "Status and exact prepare replay remain readonly; only resume-prepare completes metadata. An ordinary step runs after the source bundle is restored and then copies the three data files.",
            "The data-cache guard compares only ephemeral inode, size and timestamps. Unchanged lstat metadata is reported, without asserting independent byte-for-byte equality or reading private staging contents.",
            "Store probes are limited to guarded intent-bearing targets; no Store is constructed for a partial plan or an empty target before the first intent. The mkdir-before-intent gap is not exercised as a safe Store boundary.",
            "The driver reads no plan, owner, manifest, checksum, origin or staging contents. Narrow filesystem metadata identities remain ephemeral and only allowlisted equality/topology booleans are published.",
            "The worker is fixed to this fixture's current database and two archives; all possible crash points, schedules, hostile filesystem mutations and the maximum seventeen-file bundle are outside this demonstration.",
            "Public observation equality excludes only the top-level governance observed_at wrapper, preserving expired flags and addressed proof bodies. Historical credentials and leases remain inactive under quarantine.",
            "Programmed roles, local checksums and writer controls do not establish human independence, factual truth, external fencing or measured production capacity.",
        ],
    }
    write_json("restore-prepare-report.json", report)
    lines = [
        "# Подготовка восстановления без исходного комплекта", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [прежний сценарий прерываний](restore-interrupt-report.md); его файлы",
        "сохранены. Девять новых операций используют исторический R4 с 36 событиями и двумя архивами.",
        "Действующая база остаётся R5 с 59 событиями. Новые credentials и полномочия не выдаются.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Отказ и продолжение", "",
        "Частичный неизменяемый plan, intent без owner и частичный owner остаются блокирующими.",
        "Обычные prepare, status, step и resume-prepare отказывают, не заменяя записи и не усыновляя",
        "назначение. Эти три операции не исправляются вручную. Store вызывается только для уже",
        "защищённых intent целей и отказывает как incomplete; до первого intent такой probe не делается.", "",
        "Шесть остальных операций имеют проверяемый plan и допустимую принадлежность назначения.",
        "При временно недоступном source bundle readonly prepare/status сохраняют метаданные, а",
        "resume-prepare завершает только три sidecars и пустой каталог archives. Результат остаётся",
        "prepared с 0/3: ни DB, ни архивы, ни общий завершающий manifest ещё не установлены.", "",
        "Повтор resume-prepare стабилен. В отдельном случае собственный data staging-префикс сохраняет",
        "inode, размер и timestamps: metadata-only путь его не очищает. Содержимое staging не читается,",
        "поэтому независимая побайтовая идентичность на основании этих метаданных не заявляется.", "",
        "После возврата source bundle обычный step копирует три файла данных. Полный inspect, Store",
        "и оба архива совпадают с R4. Completed resume-prepare возвращает прежний факт без изменений.",
        "Новые записи и публикация блокируются карантином. Каталог источника возвращается в finally.", "",
        "## Публичные основания", "",
        "- [Девять случаев](restore-prepare-cases.json), [workers](restore-prepare-worker-outcomes.json), [CLI](restore-prepare-cli-outcomes.json), [отказы](restore-prepare-rejections.json).",
        "- [Исходная сводка](restore-prepare-source-summary.json), [источник](restore-prepare-source-observations.json), [живая база](restore-prepare-live-observations.json), [прежнее восстановление](restore-prepare-previous-observations.json).",
        "- [Новые назначения](restore-prepare-target-observations.json), [архивы](restore-prepare-archive-observations.json), [машинный отчёт](restore-prepare-report.json).", "",
        "## Границы результата", "",
        f"Этапов: {len(stages)}; случаев: {len(cases)}; SIGKILL: {len(worker_outcomes)}; обычных CLI: {len(cli_outcomes)}; отказов: {len(rejections)}.",
        "Родитель принимает только фиксированный ACK до 4096 байт за 60 секунд, проверяет живой",
        "процесс, посылает именно ему SIGKILL и дожидается returncode -9. Worker ограничен alarm 90 секунд.",
        "Это выбранные точки локального процесса; потеря питания, сбой ядра или хранилища, все",
        "расписания и предельная нагрузка не моделировались. Независимая экспертиза, внешние LLM,",
        "истинность содержания и внешний fencing не установлены.", "",
        "Частные plan, owner, manifests, staging, checksums и origin остаются вне публикации.",
        "Публикуются только Store/API-ответы и ограниченные булевы наблюдения топологии. Сравнение",
        "исключает лишь верхний governance.observed_at; timestamps, expired и адресованные доказательства сохраняются.", "",
    ]
    with (workspace / "restore-prepare-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
