"""Preserve a complete imported workspace in a private quarantined bundle.

Only public Store observations and fixed workspace-backup summaries are exported.
The demonstration never reads private origin rows, manifests, recovery plans or
checksums. Proposed Python remains data and all reviewers are programmed roles.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import sys
from threading import Barrier, current_thread
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .dossier_demo import _proposal
from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .releases import load_release
from .review_continuity_demo import CODE_A, DOC_A, _document, _file
from .source_transfer_chain_demo import (
    CODE_AUTHOR, COORDINATOR, DOCUMENT_AUTHOR, INTEGRATOR,
    run_source_transfer_chain_demo,
)
from .source_transfer_cli import _public_receipt
from .store import Store, WorkbenchError, canonical


SUMMARY_FIELDS = {
    "rules_version", "kind", "workspace_backup_id", "created_at", "workspace_id",
    "revision", "event_high_water", "event_count", "table_counts", "archive_count",
    "receipts", "integrity", "foreign_keys", "inspection_only", "domain_writes_allowed",
    "full_history_preserved", "old_authority_activated", "external_fencing_established",
}
PUBLIC_FLAGS = {
    "inspection_only": True, "domain_writes_allowed": False,
    "full_history_preserved": True, "old_authority_activated": False,
    "external_fencing_established": False,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Workspace backup demonstration stopped: " + message)


def run_workspace_backup_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Capture R4 with two archives, continue the source and restore history."""
    from . import source_transfer, store_backup, workspace_backup

    workspace = Path(workspace).expanduser().resolve()
    baseline_report = run_source_transfer_chain_demo(workspace, workers)
    private = workspace / "private"
    source_database = private / "next-workspace" / "state.sqlite3"
    backup_directory, restored_directory = private / "full-backup", private / "full-restore"
    administrator_file = private / "next-admin.credential"
    administrator = read_credential(administrator_file)
    old_administrator = read_credential(private / "admin.credential")
    previous_administrator = read_credential(private / "new-admin.credential")
    document_author = read_credential(private / "next-document-author.credential")
    code_author = read_credential(private / "next-code-author.credential")
    integrator = read_credential(private / "next-integrator.credential")
    reviewer_a = read_credential(private / "next-reviewer-a.credential")
    reviewer_b = read_credential(private / "next-reviewer-b.credential")
    coordinator = read_credential(private / "next-coordinator.credential")
    repository = Path(__file__).resolve().parents[1]
    source = Store(source_database)
    previous_source = Store(private / "new-workspace" / "state.sqlite3")
    old_snapshot = Store(private / "restore-01" / "state.sqlite3")
    baseline_public_files = sorted(path.name for path in workspace.iterdir()
                                   if path.is_file() and path.suffix in {".json", ".md"})
    private_markers: list[str] = []
    for path in private.glob("*.credential"):
        value = read_credential(path)
        private_markers.extend((value, hashlib.sha256(value.encode("ascii")).hexdigest()))
    forbidden_fields = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors",
        "destination_path", "history_path",
    }

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not forbidden_fields.intersection(value), "a private workspace field reached public output")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers),
                     "a bearer credential or its hash reached public output")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def integer(value: Any, *, positive: bool = False) -> bool:
        return type(value) is int and (1 if positive else 0) <= value < 2**63

    def safe_summary(value: Any) -> dict[str, Any]:
        _require(isinstance(value, dict) and set(value) == SUMMARY_FIELDS
                 and value.get("rules_version") == "workspace-backup-1"
                 and value.get("kind") in {"backup", "restore"}
                 and isinstance(value.get("workspace_backup_id"), str)
                 and re.fullmatch(r"wbk_[0-9a-f]{32}", value["workspace_backup_id"]) is not None
                 and isinstance(value.get("workspace_id"), str)
                 and re.fullmatch(r"wsp_[0-9a-f]{32}", value["workspace_id"]) is not None
                 and type(value.get("created_at")) in {int, float}
                 and math.isfinite(value["created_at"]) and value["created_at"] > 0
                 and integer(value.get("revision"), positive=True)
                 and integer(value.get("event_high_water")) and integer(value.get("event_count"))
                 and value["event_count"] <= value["event_high_water"]
                 and isinstance(value.get("table_counts"), dict)
                 and value["table_counts"].get("events") == value["event_count"]
                 and all(isinstance(key, str) and integer(count) for key, count in value["table_counts"].items())
                 and integer(value.get("archive_count"), positive=True)
                 and 1 <= value["archive_count"] <= 16
                 and isinstance(value.get("receipts"), list)
                 and len(value["receipts"]) == value["archive_count"]
                 and value.get("integrity") == value.get("foreign_keys") == "ok"
                 and all(value.get(key) is expected for key, expected in PUBLIC_FLAGS.items()),
                 "the full backup API returned unsupported safe summary fields")
        receipts = [_public_receipt(receipt) for receipt in value["receipts"]]
        _require(len({entry["transfer_id"] for entry in receipts}) == len(receipts)
                 and len({entry["workspace_id"] for entry in receipts}) == len(receipts)
                 and receipts[-1]["workspace_id"] == value["workspace_id"],
                 "the full backup summary lost its ordered workspace identity")
        return public({key: value[key] for key in sorted(SUMMARY_FIELDS)})

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(),
                "access": store.snapshot_access()}

    def governance_body(value: dict[str, Any]) -> dict[str, Any]:
        return {key: body for key, body in value.items() if key != "observed_at"}

    def observations_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
        return canonical({**before, "governance": governance_body(before["governance"])}) == canonical({
            **after, "governance": governance_body(after["governance"]),
        })

    def captured_records_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
        return (all(before[key] == after[key] for key in ("status", "state", "tasks", "events"))
                and governance_body(before["governance"]) == governance_body(after["governance"]))

    stages: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    summaries: dict[str, dict[str, Any]] = {}
    releases: dict[str, dict[str, Any]] = {}

    def stage(label: str, explanation: str, value: Any) -> None:
        name = f"workspace-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(name, {"label": label, "explanation": explanation, "observations": value})
        stages.append({"label": label, "explanation": explanation, "artifact": name})

    def rejected(label: str, action: Callable[[], Any], expected: str,
                 watched: Store, *, category: str) -> dict[str, Any]:
        before = observe(watched)
        try:
            action()
        except WorkbenchError as error:
            _require(error.code == expected, "unexpected rejection for " + label + ": " + error.code)
            _require(observations_match(before, observe(watched)),
                     "a rejected operation changed public Store observations")
            result = {"label": label, "category": category, "code": error.code,
                      "rejected": True, "public_observations_unchanged": True,
                      "comparison_excludes_only_governance_wrapper_observed_at": True}
            rejections.append(result)
            return result
        raise RuntimeError("Workspace backup demonstration stopped: expected rejection for " + label)

    def cli(label: str, action: str, *, destination: Path | None = None,
            bundle: Path | None = None) -> dict[str, Any]:
        if action == "create":
            arguments = ["--workspace", str(source_database.parent), "--destination", str(destination),
                         "--credential", str(administrator_file)]
        elif action == "inspect":
            arguments = ["--bundle", str(bundle)]
        else:
            _require(action == "restore", "unsupported demonstration CLI action")
            arguments = ["--bundle", str(bundle), "--destination", str(destination)]
        command = [sys.executable, "-m", "agentwiki", "workspace-backup", action, *arguments]
        with subprocess.Popen(command, cwd=repository, shell=False, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate()
                raise RuntimeError("Workspace backup demonstration stopped: CLI child timed out") from error
            matched = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
            code = matched.group(1) if matched else None
            cli_outcomes.append({"label": label, "command": "workspace-backup", "action": action,
                                 "child_process_id": process.pid, "returncode": process.returncode,
                                 "error_code": code, "stderr_empty": not stderr,
                                 "shell": False, "completed_child_process": True})
        _require(process.returncode == 0 and not stderr,
                 "a full backup CLI child failed with " + (code or "an unspecified error"))
        try:
            value = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise RuntimeError("Workspace backup demonstration stopped: invalid CLI JSON") from error
        return safe_summary(value)

    def publish(label: str) -> dict[str, Any]:
        output = workspace / ("workspace-" + label + "-release")
        value = source.publish(output)
        loaded = load_release(value["path"])
        expected = source.snapshot()
        _require(all(loaded["snapshot"][key] == expected[key]
                     for key in ("revision", "artifacts", "dependencies", "sources", "queries", "digests")),
                 "the live source release differs from the declared snapshot")
        result = {"label": label, "release_id": value["release_id"], "revision": value["revision"],
                  "manifest_schema_version": loaded["manifest"]["schema_version"],
                  "manifest_content_file_count": len(value["files"]),
                  "release_loader_snapshot_equal_live": True, "snapshot": loaded["snapshot"],
                  "release_files_exported_with_this_report": False,
                  "relative_directory": str(Path(value["path"]).relative_to(workspace))}
        releases[label] = result
        return public(result)

    captured = observe(source)
    _require(captured["state"]["revision"] == 4 and captured["access"]["domain_writes_allowed"] is True,
             "the continued source is not the active second workspace at R4")
    lineage_before = source_transfer.transfer_lineage_status(source_database.parent)
    _require(lineage_before["archive_count"] == 2,
             "the active workspace does not have both archived hops")
    original_stores = {
        "original": Store(private / "source" / "state.sqlite3"),
        "previous": previous_source, "current": source,
    }
    old_observations_before = {label: observe(store) for label, store in original_stores.items()}
    source_archive_stores = {
        "oldest": Store(source_database.parent / "history-0001.sqlite3"),
        "nearest": Store(source_database.parent / "history.sqlite3"),
    }
    source_archive_observations = {label: observe(store) for label, store in source_archive_stores.items()}
    old_snapshot_before = observe(old_snapshot)
    initial_release = publish("captured-r4")
    stage("active-workspace-with-two-archived-hops",
          "The active R4 workspace has two complete ancestral archives and a normally loadable release.",
          {"source_status": captured["status"], "source_access": captured["access"],
           "lineage": lineage_before, "release": initial_release})

    rejected("original-administrator-cannot-authorize-current-backup", lambda: workspace_backup.create_backup(
        source_database, private / "full-rejected-old-admin", credential=old_administrator),
        "governance_unauthorized", source, category="authorization")
    rejected("live-code-author-cannot-authorize-administrative-backup", lambda: workspace_backup.create_backup(
        source_database, private / "full-rejected-code-author", credential=code_author),
        "governance_unauthorized", source, category="authorization")
    rejected("fenced-workspace-cannot-authorize-full-backup", lambda: workspace_backup.create_backup(
        private / "new-workspace" / "state.sqlite3", private / "full-rejected-fenced",
        credential=previous_administrator), "store_source_fenced", previous_source, category="source_boundary")
    rejected("quarantined-copy-cannot-authorize-full-backup", lambda: workspace_backup.create_backup(
        private / "restore-01" / "state.sqlite3", private / "full-rejected-old-snapshot",
        credential=old_administrator), "workspace_backup_source_invalid", old_snapshot,
        category="source_boundary")
    stage("current-authorization-and-source-boundaries",
          "Only the live imported workspace and its current administrator may authorize a new full backup.",
          rejections)

    summaries["created"] = cli("create-full-workspace-backup", "create", destination=backup_directory)
    summaries["backup_inspected_initial"] = cli("inspect-completed-full-backup", "inspect", bundle=backup_directory)
    created = summaries["created"]
    _require(created == summaries["backup_inspected_initial"]
             and created["archive_count"] == 2 and created["receipts"] == lineage_before["receipts"]
             and created["revision"] == captured["state"]["revision"]
             and created["event_count"] == captured["status"]["events"]
             and observations_match(captured, observe(source)),
             "the full backup does not describe the captured live state and complete history")
    backup_store = Store(backup_directory / "state.sqlite3")
    backup_observation_initial = observe(backup_store)
    _require(captured_records_match(captured, backup_observation_initial)
             and backup_observation_initial["access"]["quarantined"] is True
             and backup_observation_initial["access"]["domain_writes_allowed"] is False,
             "the copied current DB did not retain captured records under quarantine")
    backup_archive_stores = {
        "oldest": Store(backup_directory / "archives" / "history-0001.sqlite3"),
        "nearest": Store(backup_directory / "archives" / "history.sqlite3"),
    }
    backup_archive_observations = {label: observe(store) for label, store in backup_archive_stores.items()}
    _require(all(observations_match(source_archive_observations[label], value)
                 for label, value in backup_archive_observations.items()),
             "a copied ancestor differs from its original historical records")
    stage("full-backup-captures-current-state-and-all-archives",
          "The completed full bundle contains quarantined R4 records and both unchanged fenced ancestral archives.",
          {"summary": created, "copied_status": backup_observation_initial["status"],
           "copied_access": backup_observation_initial["access"],
           "archive_statuses": {label: value["status"] for label, value in backup_archive_observations.items()}})
    rejected("existing-backup-destination-is-preserved", lambda: workspace_backup.create_backup(
        source_database, backup_directory, credential=administrator),
        "workspace_backup_destination_exists", source, category="destination")

    initial_snapshot = source.snapshot()
    bodies = {"document-a": _document(DOC_A, 9),
              "code-a": _file(CODE_A, "code", "SAMPLE_LIMIT = 9\n"),
              "alternative-document": _document(DOC_A, 10)}
    for label, body in bodies.items():
        source.enqueue("workspace-author-" + label, "doc_author" if body["kind"] == "document" else "code_author", {
            "proposal": _proposal(initial_snapshot, body,
                                  "Live source continuation after full capture; proposed Python remains data.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        scheduled = list(bodies)[index]
        role = "doc_author" if bodies[scheduled]["kind"] == "document" else "code_author"
        principal, secret = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author" else (CODE_AUTHOR, code_author))
        lease = source.claim_task(principal, [role], lease_seconds=300, credential=secret)
        _require(lease is not None, "a new live author lease was unavailable")
        result = source.submit(lease["task_id"], principal, lease["token"],
                               lease["payload"]["proposal"], credential=secret)
        return {"label": lease["task_id"].removeprefix("workspace-author-"), "principal_id": principal,
                "thread": current_thread().name, "task_id": lease["task_id"], "fencing_token": lease["token"], **result}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="workspace-author") as executor:
        futures = [executor.submit(author_job, index) for index in range(len(bodies))]
        authors = {value["label"]: value for value in (future.result() for future in futures)}
    source.enqueue("workspace-compose-a", "integrator", {
        "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                       for label in ("document-a", "code-a")],
        "rationale": "Continue the original live workspace with new proposals, retaining authenticated lineage.",
    })
    composition_lease = source.claim_task(INTEGRATOR, ["integrator"], lease_seconds=300, credential=integrator)
    _require(composition_lease is not None, "the live integrator lease was unavailable")
    composition = source.compose(composition_lease["task_id"], INTEGRATOR,
                                 composition_lease["token"], credential=integrator)
    main_id = composition["proposal_id"]
    formal_review = source.review(main_id, credential=reviewer_a)
    _require(formal_review["approved"] is True, "the live continuation failed formal review")
    stage("live-authors-continue-after-capture",
          "Three live author jobs and one new composition use current source grants after the full backup.",
          {"authors": authors, "composition": composition, "formal_review": formal_review})

    generation = source.governance_status()["policy_generation"]
    decision_history: list[dict[str, Any]] = []
    decision_requests: list[dict[str, Any]] = []
    packets: dict[str, dict[str, Any]] = {}
    candidate_packet = source.content_status(main_id)["packet"]
    packets[candidate_packet["packet_id"]] = candidate_packet
    for suffix, secret in (("a", reviewer_a), ("b", reviewer_b)):
        for index, identifier in enumerate((DOC_A, CODE_A), 1):
            label = suffix + "-" + str(index)
            request = {"proposal_id": main_id, "artifact_id": identifier, "verdict": "approve",
                       "rationale": "Programmed current-source decision; no independent human or model review is established.",
                       "expected_proposal_digest": composition["digest"], "expected_policy_generation": generation,
                       "expected_previous_decision": None,
                       "expected_content_packet_digest": candidate_packet["packet_digest"],
                       "operation_id": "workspace-decision-" + label}
            result = source.governance_decide(**request, credential=secret)
            decision_requests.append({"label": label, "request": request})
            decision_history.append({"label": label, "result": result})
    observation = source.governance_observe(main_id, credential=coordinator,
                                          expected_policy_generation=generation)
    packets[observation["content"]["packet_id"]] = observation["content"]
    _require(observation["eligible"] is True, "new current-source opinions did not satisfy the policy")
    acceptance = source.accept(main_id, COORDINATOR, credential=coordinator,
                               expected_policy_generation=generation)
    _require(acceptance["accepted"] is True and acceptance["revision"] == captured["state"]["revision"] + 1,
             "live source continuation did not advance to the next revision")
    source_after_acceptance = observe(source)
    final_release = publish("continued-r5")
    stage("fresh-source-decisions-and-acceptance",
          "Four fresh decisions authorize revision 5 only in the live source; the full backup still describes R4.",
          {"packet": candidate_packet, "decisions": decision_history, "observation": observation,
           "acceptance": acceptance, "release": final_release})

    current_grants = source.governance_status()["grants"]
    reviewer_b_grant = next(value for value in current_grants
                            if value["principal_id"] == "chain-reviewer-b" and value["role"] == "reviewer")
    revocation = source.governance_revoke(reviewer_b_grant["grant_id"], credential=administrator)
    source_after_revocation = observe(source)
    captured_b = next(value for value in captured["governance"]["grants"]
                      if value["grant_id"] == reviewer_b_grant["grant_id"])
    current_b = next(value for value in source_after_revocation["governance"]["grants"]
                     if value["grant_id"] == reviewer_b_grant["grant_id"])
    _require(captured_b["revoked"] is False and current_b["revoked"] is True,
             "the historical and current reviewer-grant states were not distinguished")
    rejected("revoked-reviewer-cannot-authorize-full-backup", lambda: workspace_backup.create_backup(
        source_database, private / "full-rejected-revoked-reviewer", credential=reviewer_b),
        "governance_unauthorized", source, category="authorization")
    _require(observations_match(backup_observation_initial, observe(backup_store)),
             "live acceptance or revocation changed the captured full backup")
    stage("later-revocation-does-not-rewrite-captured-history",
          "The live reviewer grant is now revoked; the quarantined R4 copy retains its earlier historical state.",
          {"revocation": revocation, "captured_reviewer_grant": captured_b,
           "current_reviewer_grant": current_b, "source_status": source_after_revocation["status"],
           "captured_status": backup_observation_initial["status"]})

    detachment_locations = (
        (private / "source", private / "workspace-detached-original"),
        (private / "new-workspace", private / "workspace-detached-previous"),
        (private / "next-workspace", private / "workspace-detached-current"),
    )
    _require(all(not detached.exists() for _, detached in detachment_locations),
             "temporary source-path detachment requires fresh private names")
    moved: list[tuple[Path, Path]] = []
    try:
        for original, detached in detachment_locations:
            original.rename(detached)
            moved.append((original, detached))
        summaries["backup_inspected_detached"] = cli("inspect-backup-without-source-paths", "inspect", bundle=backup_directory)
        summaries["restored"] = cli("restore-full-history-without-source-paths", "restore",
                                     bundle=backup_directory, destination=restored_directory)
        summaries["restore_inspected_detached"] = cli("inspect-restore-without-source-paths", "inspect", bundle=restored_directory)
        detached_restored = observe(Store(restored_directory / "state.sqlite3"))
        detached_archive_observations = {
            "oldest": observe(Store(restored_directory / "archives" / "history-0001.sqlite3")),
            "nearest": observe(Store(restored_directory / "archives" / "history.sqlite3")),
        }
        _require(captured_records_match(captured, detached_restored)
                 and detached_restored["access"]["quarantined"] is True
                 and all(observations_match(source_archive_observations[label], value)
                         for label, value in detached_archive_observations.items())
                 and summaries["backup_inspected_detached"] == created
                 and summaries["restored"] == summaries["restore_inspected_detached"],
                 "full inspection or restore depended on an unavailable source path")
    finally:
        for original, detached in reversed(moved):
            detached.rename(original)
    _require(observations_match(source_after_revocation, observe(source))
             and all(observations_match(old_observations_before[label], observe(store))
                     for label, store in original_stores.items() if label != "current"),
             "restoring original source paths changed saved public observations")
    detached_result = {
        "previous_workspace_directory_count": len(detachment_locations),
        "mechanism": "reversible directory rename with finally restoration",
        "source_paths_unavailable_during_inspect_and_restore": True,
        "restored_store_records_equal_capture": True,
        "restored_archive_records_equal_ancestry": True,
        "all_source_paths_restored": True, "process_crash_simulated": False,
        "private_origin_or_manifest_contents_read_by_demo": False,
    }
    stage("full-inspection-and-restore-with-all-source-paths-unavailable",
          "All three original workspace paths are temporarily renamed; the full bundle still inspects and restores complete quarantined history.",
          {"detachment": detached_result, "backup_summary": summaries["backup_inspected_detached"],
           "restore_summary": summaries["restored"], "restored_status": detached_restored["status"],
           "restored_archive_statuses": {label: value["status"] for label, value in detached_archive_observations.items()}})

    restored = Store(restored_directory / "state.sqlite3")
    restored_observation = observe(restored)
    _require(captured_records_match(captured, restored_observation)
             and restored_observation["state"]["revision"] == 4
             and source.snapshot()["revision"] == 5,
             "the restored point-in-time state differs from capture or later live state")
    summary_pins = {key for key in SUMMARY_FIELDS if key not in {"kind", "created_at"}}
    _require(all(created[key] == summaries["restored"][key] for key in summary_pins)
             and summaries["restored"]["kind"] == "restore",
             "restoration changed the full bundle's immutable captured identity")
    captured_proposal_id = next(value["proposal_id"] for value in captured["governance"]["receipts"])
    stage("restored-point-in-time-records-remain-quarantined",
          "The restored current DB retains R4 content, dependencies, tasks, events and receipts; live R5 and the later revocation remain separate.",
          {"restored_status": restored_observation["status"], "restored_access": restored_observation["access"],
           "live_status": source_after_revocation["status"], "full_restore": summaries["restored"]})

    rejected("existing-full-restore-destination-is-preserved", lambda: workspace_backup.restore_bundle(
        backup_directory, restored_directory), "workspace_backup_destination_exists", restored,
        category="destination")
    rejected("db-only-inspector-does-not-accept-full-workspace-layout", lambda: store_backup.inspect_bundle(
        backup_directory), "snapshot_manifest_invalid", restored, category="format_boundary")
    rejected("restored-enqueue-remains-blocked", lambda: restored.enqueue(
        "workspace-restored-blocked-job", "code_author", {}), "store_snapshot_quarantined", restored,
        category="quarantine")
    rejected("restored-acceptance-replay-remains-blocked", lambda: restored.accept(
        captured_proposal_id, COORDINATOR, credential=coordinator, expected_policy_generation=generation),
        "store_snapshot_quarantined", restored, category="quarantine")
    rejected("restored-submission-remains-blocked", lambda: restored.submit(
        "workspace-restored-blocked-job", CODE_AUTHOR, 1, {}, credential=code_author),
        "store_snapshot_quarantined", restored, category="quarantine")
    rejected("restored-publish-remains-blocked", lambda: restored.publish(
        workspace / "workspace-restored-forbidden-release"), "store_snapshot_quarantined", restored,
        category="quarantine")
    _require(not (workspace / "workspace-restored-forbidden-release").exists(),
             "a quarantined publication created a release directory")
    rejected("restored-db-only-backup-remains-blocked", lambda: restored.create_backup(
        private / "full-rejected-restored-db-backup", credential=administrator),
        "store_snapshot_quarantined", restored, category="quarantine")
    rejected("restored-full-backup-remains-blocked", lambda: workspace_backup.create_backup(
        restored_directory / "state.sqlite3", private / "full-rejected-restored-full-backup",
        credential=administrator), "workspace_backup_source_invalid", restored, category="quarantine")
    rejected("restored-copy-cannot-authorize-source-transfer", lambda: source_transfer.transfer_source(
        restored_directory / "state.sqlite3", private / "full-rejected-restored-transfer",
        credential=administrator, new_administrator_id="workspace-rejected-administrator",
        new_credential=old_administrator, operation_id="workspace-rejected-restored-transfer"),
        "source_transfer_source_invalid", restored, category="quarantine")
    stage("restored-authority-and-format-boundaries",
          "Current credentials cannot turn historical copied rows into writes, publication, backup authority or transfer authority.",
          {"rejections": [value for value in rejections if value["category"] in {
              "quarantine", "format_boundary", "destination"}],
           "forbidden_release_directory_created": False})

    summaries["backup_inspected_final"] = cli("inspect-full-backup-after-live-continuation", "inspect", bundle=backup_directory)
    summaries["restore_inspected_final"] = cli("inspect-full-restore-after-blocked-writes", "inspect", bundle=restored_directory)
    _require(summaries["backup_inspected_final"] == created
             and summaries["restore_inspected_final"] == summaries["restored"]
             and observations_match(backup_observation_initial, observe(backup_store))
             and observations_match(restored_observation, observe(restored)),
             "inspection or rejected operations changed a completed full bundle")
    source_final = observe(source)
    _require(observations_match(source_after_revocation, source_final)
             and observations_match(old_snapshot_before, observe(old_snapshot))
             and all(observations_match(source_archive_observations[label], observe(store))
                     for label, store in source_archive_stores.items()),
             "full backup operations changed live or older historical records")
    stage("complete-history-remains-stable-after-live-continuation",
          "Repeated full inspection preserves the captured identity, both archives and quarantined restore while the original source stays active at R5.",
          {"backup": summaries["backup_inspected_final"], "restore": summaries["restore_inspected_final"],
           "source_status": source_final["status"], "restored_status": restored_observation["status"]})

    data_artifacts = {
        "workspace-summaries.json": summaries,
        "workspace-cli-outcomes.json": cli_outcomes,
        "workspace-rejections.json": rejections,
        "workspace-source-observations.json": {"captured": captured, "after_acceptance": source_after_acceptance,
                                               "after_revocation": source_after_revocation, "final": source_final},
        "workspace-backup-observations.json": {"initial": backup_observation_initial, "final": observe(backup_store)},
        "workspace-restored-observations.json": {"detached": detached_restored, "final": observe(restored)},
        "workspace-archive-observations.json": {"original": source_archive_observations,
                                                "backup": backup_archive_observations,
                                                "restored_without_sources": detached_archive_observations},
        "workspace-old-snapshot-observations.json": {"before": old_snapshot_before, "after": observe(old_snapshot)},
        "workspace-dependencies.json": {"captured": captured["state"]["dependencies"],
                                         "restored": restored_observation["state"]["dependencies"],
                                         "continued": source_final["state"]["dependencies"]},
        "workspace-detached-path-observations.json": detached_result,
        "workspace-lineage.json": lineage_before,
        "workspace-authors.json": authors,
        "workspace-composition.json": {"composition": composition, "formal_review": formal_review},
        "workspace-decision-requests.json": decision_requests,
        "workspace-decisions.json": decision_history,
        "workspace-packets.json": packets,
        "workspace-observation.json": observation,
        "workspace-acceptance.json": acceptance,
        "workspace-revocation.json": {"revocation": revocation, "captured": captured_b, "current": current_b},
        "workspace-content-policy-history.json": [source.content_status()["current_policy"]],
        "workspace-governance-policy-history.json": source_final["governance"]["policy_history"],
        "workspace-releases.json": releases,
    }
    for name, value in data_artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": "workspace-backup-1",
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Full quarantined workspace backup with all ancestral archives and continuing live source",
        "summary": {
            "baseline_public_file_count": len(baseline_public_files), "workspace_stage_count": len(stages),
            "workspace_cli_child_process_count": len(cli_outcomes),
            "total_cli_child_process_count": baseline_report["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "captured_revision": captured["state"]["revision"], "restored_revision": restored_observation["state"]["revision"],
            "source_revision_after_continuation": source_final["state"]["revision"],
            "captured_event_count": captured["status"]["events"],
            "source_event_count_after_continuation": source_final["status"]["events"],
            "restored_event_count": restored_observation["status"]["events"],
            "complete_archive_count": created["archive_count"],
            "oldest_archive_event_count": source_archive_observations["oldest"]["status"]["events"],
            "nearest_archive_event_count": source_archive_observations["nearest"]["status"]["events"],
            "fresh_author_job_count": len(authors), "fresh_author_pool_workers": workers,
            "fresh_observed_author_thread_count": len({value["thread"] for value in authors.values()}),
            "fresh_decision_count": len(decision_history), "new_role_grants_issued": 0,
            "operator_rejection_count": len(rejections),
            "source_workspace_directories_temporarily_unavailable": len(detachment_locations),
            "all_source_paths_restored": True, "full_backup_restored_without_original_paths": True,
            "captured_dependencies_preserved": True, "captured_receipts_preserved": True,
            "historical_reviewer_unrevoked_in_copy": captured_b["revoked"] is False,
            "later_live_reviewer_revoked": current_b["revoked"] is True,
            "restored_workspace_quarantined": True, "old_authority_activated": False,
            "full_history_preserved": True, "db_only_inspector_accepts_full_bundle": False,
            "private_origin_contents_read_by_demo": False, "private_manifest_contents_read_by_demo": False,
            "private_plan_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "partial_bundle_or_crash_simulated": False, "external_fencing_established": False,
            "external_llm_used": False, "human_review_recorded": False,
            "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(data_artifacts),
        "limits": [
            "The complete backup remains a private inspection bundle; restoring it never activates old runtime authority or renews leases.",
            "Only the original live source performs fresh author work and a new acceptance. Its later revocation is separate from historical copied grant state.",
            "Source-path unavailability is a reversible rename of three private directories with finally restoration, not a process or hardware crash.",
            "The full bundle verifier checks all ancestral archives; the ordinary database-only inspector retains its separate exact layout contract.",
            "Public equality omits only the changing top-level governance observed_at wrapper. Expired flags and full addressed proof bodies remain intact.",
            "Declared dependency provenance is preserved. Query maps in the fictional source are empty; no separate stale read/query case is induced here.",
            "Incomplete bundle points, concurrent source policy changes, byte corruption and input-limit exhaustion are not simulated by this ordinary scenario.",
            "Programmed local roles do not establish independent human or model expertise, factual truth, external fencing or measured production capacity.",
        ],
    }
    write_json("workspace-report.json", report)
    lines = [
        "# Полный backup рабочей среды и цепочки истории", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий двух переносов](chain-report.md). Его публичные результаты",
        "сохранены без перезаписи. Новый полный комплект объединяет quarantined снимок текущей базы",
        "и оба предковых архива; частные origin, intent, manifest и планы демонстратором не читаются.", "",
        "## Последовательность", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Захват и последующая работа", "",
        "Полный backup сохраняет ревизию 4 с её зависимостями, задачами, событиями и квитанцией.",
        "Три новых авторских задания, композиция и четыре свежих решения затем принимаются в исходной",
        "активной базе как ревизия 5. Используются её существующие действующие роли; копия не участвует",
        "в новой записи. Последующий отзыв reviewer B также остаётся изменением живого источника.", "",
        "## Самодостаточная история и восстановление", "",
        "Три первоначальных частных каталога временно переименованы. Полный комплект успешно проверяется",
        "и восстанавливается без этих путей; Store читает восстановленную ревизию 4 и оба архивных Store.",
        "Имена прежних каталогов восстановлены в finally, публичные наблюдения совпали. Это обратимая",
        "недоступность путей, без моделирования аварии процесса или дискового повреждения.", "",
        "Восстановленная база остаётся в карантине. Прежние grants и решения читаются как история;",
        "enqueue, submit, acceptance replay, publish, backup и transfer не получают полномочий записи.",
        "Обычный DB-only inspector сохраняет свой отдельный формат и не принимает полный комплект.", "",
        "## Публичные основания", "",
        "- [Безопасные сводки](workspace-summaries.json), [CLI](workspace-cli-outcomes.json), [отказы](workspace-rejections.json).",
        "- [Живой источник](workspace-source-observations.json), [backup](workspace-backup-observations.json), [restore](workspace-restored-observations.json).",
        "- [Архивы](workspace-archive-observations.json), [цепочка квитанций](workspace-lineage.json), [прежний snapshot](workspace-old-snapshot-observations.json).",
        "- [Зависимости](workspace-dependencies.json), [выпуски](workspace-releases.json), [недоступность путей](workspace-detached-path-observations.json).",
        "- [Авторы](workspace-authors.json), [композиция](workspace-composition.json), [пакеты](workspace-packets.json).",
        "- [Решения](workspace-decisions.json), [рассмотрение](workspace-observation.json), [принятие](workspace-acceptance.json), [отзыв](workspace-revocation.json).",
        "- [Content-политика](workspace-content-policy-history.json), [governance](workspace-governance-policy-history.json), [машинный отчёт](workspace-report.json).", "",
        "## Границы выполненного", "",
        f"Новых этапов: {len(stages)}; новых CLI-процессов: {len(cli_outcomes)}; ожидаемых отказов: {len(rejections)}.",
        f"Захваченная и восстановленная ревизия: {captured['state']['revision']}; поздний источник: {source_final['state']['revision']}.",
        f"События захвата: {captured['status']['events']}; позднего источника: {source_final['status']['events']}; restore: {restored_observation['status']['events']}.",
        f"Архивов: {created['archive_count']}; событий: {source_archive_observations['oldest']['status']['events']} и {source_archive_observations['nearest']['status']['events']}.",
        f"Авторских заданий: {len(authors)}; наблюдавшихся новых потоков: {report['summary']['fresh_observed_author_thread_count']}; свежих решений: {len(decision_history)}.",
        "Программируемые роли не являются независимой человеческой экспертизой. Внешние LLM и",
        "предлагаемый Python не исполнялись; измерений предельной ёмкости нет. Отдельный stale query/read",
        "случай, частичный bundle, повреждение байтов и авария процесса не воспроизводились.", "",
        "Публичная сверка исключает только вычисляемый верхний governance.observed_at. Полные",
        "адресованные доказательства, raw timestamps и признаки expired сохранены. Частные базы,",
        "контрольные суммы, manifests, планы и credentials не публикуются. Выпуски остаются в каталоге запуска.", "",
    ]
    with (workspace / "workspace-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
