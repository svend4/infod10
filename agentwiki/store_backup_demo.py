"""Create a private WAL-aware Store snapshot and inspect quarantined copies.

Only public Store records and fixed safe snapshot summaries leave private/.
The private database, intent, manifest, checksums and bearer files stay there.
This fictional workflow executes no proposed Python and activates no copy.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
from threading import Barrier, current_thread
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .dossier_demo import _proposal
from .governance_cli import prepare_credential
from .review_continuity_demo import (
    CODE_A, CODE_D, DOC_A, DOC_D, _catalog, _document, _file, _scope_edit,
)
from .store import Store, WorkbenchError, canonical


DOCUMENT_AUTHOR = "snapshot-document-author"
CODE_AUTHOR = "snapshot-code-author"
INTEGRATOR = "snapshot-integrator"
REVIEWER_A = "snapshot-reviewer-a"
REVIEWER_B = "snapshot-reviewer-b"
REVIEWER_C = "snapshot-reviewer-c"
ADMINISTRATOR = "snapshot-administrator"
SUMMARY_FIELDS = {
    "rules_version", "kind", "backup_id", "created_at", "inspection_only", "domain_writes_allowed",
    "revision", "event_high_water", "event_count", "table_counts", "integrity", "foreign_keys",
}
PRIVATE_PROOF_FIELDS = {
    "credential", "credential_hash", "credential_digest", "token_hash", "token_digest",
    "database_digest", "database_sha256", "db_digest", "db_sha256", "database_checksum",
    "manifest", "manifest_digest", "manifest_sha256", "schema_fingerprint", "schema_digest",
    "checksum", "checksums",
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Store snapshot demonstration stopped: " + message)


def _inspection(value: dict[str, Any]) -> None:
    _require(value.get("inspection_only") is True and value.get("domain_writes_allowed") is False,
             "a snapshot copy does not expose the required inspection boundary")


def run_store_backup_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a new fictional workflow; its private copies remain quarantined."""
    from . import store_backup

    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Store backup demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    private = workspace / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    source_directory = private / "source"
    source_directory.mkdir(mode=0o700)
    source_directory.chmod(0o700)
    database = source_directory / "state.sqlite3"
    database.touch(mode=0o600, exist_ok=False)
    database.chmod(0o600)
    bundle = private / "backup-01"
    restored_directory = private / "restore-01"
    repository = Path(__file__).resolve().parents[1]
    private_markers: list[str] = []

    def new_credential(label: str) -> str:
        value = prepare_credential(private / (label + ".credential"))
        private_markers.extend((value, hashlib.sha256(value.encode("ascii")).hexdigest()))
        return value

    def public(value: Any) -> Any:
        # Never remove fields from addressed public Store proof bodies. A
        # forbidden private field stops export, rather than changing its hash.
        if isinstance(value, dict):
            _require(not PRIVATE_PROOF_FIELDS.intersection(value), "a private snapshot or credential field reached public output")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers), "a private bearer value or its hash reached public output")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def safe_summary(value: dict[str, Any]) -> dict[str, Any]:
        _require(SUMMARY_FIELDS.issubset(value), "the backup API did not return its fixed safe summary")
        result = {key: value[key] for key in sorted(SUMMARY_FIELDS)}
        _inspection(result)
        _require(result["rules_version"] == "store-snapshot-1"
                 and result["integrity"] == result["foreign_keys"] == "ok", "the snapshot summary did not pass validation")
        return public(result)

    source = Store(database)
    source.bootstrap([_document(DOC_A, 3), _document(DOC_D, 3),
                      _file(CODE_A, "code", "SAMPLE_LIMIT = 3\n"),
                      _file(CODE_D, "code", "SAMPLE_LIMIT = 3\n")])
    initial_snapshot = source.snapshot()
    editorial_policy = {"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True}
    active_catalog = _catalog()
    admin = new_credential("admin")
    enabled_governance = source.governance_enable(editorial_policy, ADMINISTRATOR, admin)
    generation = 1
    expires_at = time.time() + 3600
    all_artifacts = [DOC_A, CODE_A, DOC_D, CODE_D]
    grants: dict[str, dict[str, Any]] = {}

    def grant(label: str, principal: str, role: str, artifact_ids: list[str]) -> str:
        value = new_credential(label)
        grants[label] = source.governance_grant(
            principal, role, artifact_ids, expires_at, credential=admin,
            new_credential=value, operation_id="snapshot-grant-" + label)
        return value

    document_author = grant("document-author", DOCUMENT_AUTHOR, "doc_author", [DOC_A, DOC_D])
    code_author = grant("code-author", CODE_AUTHOR, "code_author", [CODE_A, CODE_D])
    integrator = grant("integrator", INTEGRATOR, "integrator", all_artifacts)
    reviewer_a = grant("reviewer-a", REVIEWER_A, "reviewer", all_artifacts)
    reviewer_b = grant("reviewer-b", REVIEWER_B, "reviewer", all_artifacts)
    reviewer_c = grant("reviewer-c", REVIEWER_C, "reviewer", all_artifacts)
    coordinator = grant("coordinator", "snapshot-coordinator", "coordinator", all_artifacts)
    enabled_authorship = source.authorship_enable(credential=admin, expected_policy_generation=generation)
    generation = enabled_authorship["policy_generation"]
    enabled_content = source.content_enable(active_catalog, credential=admin, expected_policy_generation=generation)
    generation = enabled_content["policy_generation"]
    active_catalog = deepcopy(enabled_content["current_policy"]["policy"])
    content_policy_history = [deepcopy(enabled_content["current_policy"])]
    enabled_continuity = source.continuity_enable(credential=admin, expected_policy_generation=generation)
    generation = enabled_continuity["policy_generation"]
    operations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    decision_history: list[dict[str, Any]] = []
    decision_requests: dict[str, dict[str, Any]] = {}
    packets: dict[str, dict[str, Any]] = {}
    packet_labels: dict[str, str] = {}
    observations: dict[str, dict[str, Any]] = {}
    audit_results: dict[str, dict[str, Any]] = {}
    audit_requests: dict[str, dict[str, Any]] = {}
    audit_headers: dict[str, dict[str, Any]] = {}
    checkpoints: dict[str, dict[str, Any]] = {}
    catalog_updates: list[dict[str, Any]] = []

    def cli(label: str, action: str, arguments: list[str]) -> dict[str, Any]:
        command = [sys.executable, "-m", "agentwiki", "store-backup", action, *arguments]
        with subprocess.Popen(command, cwd=repository, shell=False, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=60)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate()
                raise RuntimeError("Store snapshot demonstration stopped: CLI child timed out") from error
            # Private error text is deliberately not exported. Its error code
            # and exit status are sufficient for this normal successful CLI flow.
            cli_outcomes.append({"label": label, "command": "store-backup", "action": action,
                                 "child_process_id": process.pid, "returncode": process.returncode,
                                 "stderr_empty": not stderr, "shell": False,
                                 "working_directory": "repository checkout", "completed_child_process": True})
            _require(process.returncode == 0, "CLI " + action + " exited with " + str(process.returncode))
        try:
            value = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise RuntimeError("Store snapshot demonstration stopped: backup CLI returned invalid JSON") from error
        return safe_summary(value)

    def remember_packet(label: str, value: dict[str, Any]) -> dict[str, Any]:
        identifier = value["packet_id"]
        if identifier in packets:
            _require(canonical(packets[identifier]) == canonical(value), "one public packet ID names different bodies")
        packets[identifier], packet_labels[label] = value, identifier
        return value

    def packet(label: str) -> dict[str, Any]:
        return remember_packet(label, source.content_status(main_id)["packet"])

    def remember_audit(label: str, value: dict[str, Any]) -> dict[str, Any]:
        _require(value.get("advisory_only") is True and value.get("acceptance_authority") is False
                 and value.get("authority_evaluated") is False, "an audit result lost its advisory flags")
        root, checkpoint = value["audit"], value["checkpoint"]
        audit_id, checkpoint_id = root["audit_id"], checkpoint["checkpoint_id"]
        if audit_id in audit_headers:
            _require(canonical(audit_headers[audit_id]) == canonical(root), "a frozen audit header changed")
        if checkpoint_id in checkpoints:
            _require(canonical(checkpoints[checkpoint_id]) == canonical(checkpoint), "an immutable checkpoint changed")
        audit_headers[audit_id], checkpoints[checkpoint_id] = root, checkpoint
        audit_results[label] = value
        return value

    def expected_exception(label: str, operation: Callable[[], Any], code: str,
                           observed_store: Store | None = None) -> dict[str, Any]:
        before = observed_store.status() if observed_store else None
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == code, "unexpected rejection for " + label + ": " + error.code)
            after = observed_store.status() if observed_store else None
            _require(before == after, "a rejected operation changed Store status")
            result = {"label": label, "rejected": True, "code": error.code,
                      "status_unchanged": True if observed_store else None}
            operations.append(result)
            return result
        raise RuntimeError("Store snapshot demonstration stopped: expected rejection for " + label)

    bodies = {"document-a": _document(DOC_A, 4), "code-a": _file(CODE_A, "code", "SAMPLE_LIMIT = 4\n"),
              "alternative-document": _document(DOC_A, 5)}
    for label, body in bodies.items():
        source.enqueue("snapshot-author-" + label, "doc_author" if body["kind"] == "document" else "code_author", {
            "proposal": _proposal(initial_snapshot, body, "Fictional private snapshot workflow; proposed Python is data only.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        scheduled = list(bodies)[index]
        role = "doc_author" if bodies[scheduled]["kind"] == "document" else "code_author"
        principal, value = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author" else (CODE_AUTHOR, code_author))
        lease = source.claim_task(principal, roles=[role], credential=value, lease_seconds=300)
        _require(lease is not None, "an authenticated author lease was unavailable")
        submitted = source.submit(lease["task_id"], principal, lease["token"], lease["payload"]["proposal"], credential=value)
        return {"label": lease["task_id"].removeprefix("snapshot-author-"), "principal_id": principal,
                "thread": current_thread().name, "task_id": lease["task_id"], "fencing_token": lease["token"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="snapshot-author") as executor:
        futures = [executor.submit(author_job, index) for index in range(len(bodies))]
        authors = {value["label"]: value for value in (future.result() for future in futures)}
    source.enqueue("snapshot-compose-a", "integrator", {
        "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                       for label in ("document-a", "code-a")],
        "rationale": "Compose the exact fictional candidate for WAL-aware private snapshot inspection.",
    })
    composition_lease = source.claim_task(INTEGRATOR, roles=["integrator"], credential=integrator, lease_seconds=300)
    _require(composition_lease is not None, "the integrator lease was unavailable")
    main = source.compose(composition_lease["task_id"], INTEGRATOR, composition_lease["token"], credential=integrator)
    main_id = main["proposal_id"]
    formal_review = source.review(main_id, credential=reviewer_a)
    _require(formal_review["approved"], "formal review did not approve the fictional candidate")

    def stage(label: str, explanation: str, value: Any = None) -> None:
        name = f"stage-{len(stages) + 1:02d}-{label}.json"
        status = source.status()
        write_json(name, {"label": label, "explanation": explanation, "source_status": status,
                          "governance_policy_generation": generation, "operation_result": value})
        stages.append({"label": label, "explanation": explanation, "artifact": name, "source_revision": status["revision"]})

    def decide(label: str, value: str, principal: str, identifier: str, verdict: str = "approve") -> dict[str, Any]:
        current_packet = packet("decision-" + label)
        previous = decisions.get((principal, identifier))
        request = {"proposal_id": main_id, "artifact_id": identifier, "verdict": verdict,
                   "rationale": "Programmed local decision for " + label + "; independent human or model review is not established.",
                   "expected_proposal_digest": main["digest"], "expected_policy_generation": generation,
                   "expected_previous_decision": previous["decision_id"] if previous else None,
                   "expected_content_packet_digest": current_packet["packet_digest"], "operation_id": "snapshot-decision-" + label}
        result = source.governance_decide(**request, credential=value)
        decisions[(principal, identifier)] = result
        decision_requests[label] = request
        decision_history.append({"label": label, "result": result})
        return result

    def observe(label: str) -> dict[str, Any]:
        result = source.governance_observe(main_id, credential=coordinator, expected_policy_generation=generation)
        observations[label] = result
        remember_packet("observation-" + label, result["content"])
        return result

    def update_catalog(label: str) -> dict[str, Any]:
        nonlocal generation, active_catalog
        result = source.content_set_policy(_scope_edit(active_catalog, "section-d", label),
                                            credential=admin, expected_policy_generation=generation)
        generation = result["policy_generation"]
        current = deepcopy(result["current_policy"])
        active_catalog = current["policy"]
        content_policy_history.append(current)
        summary = {"label": label, "governance_policy_generation": generation,
                   "content_policy_generation": current["generation"], "content_policy_digest": current["policy_digest"]}
        catalog_updates.append(summary)
        return summary

    stage("authenticated-candidate", "Three author jobs and one composition prepare the fictional A document/code candidate; no proposed code is executed.",
          {"authors": authors, "composition": main, "formal_review": formal_review})
    original_approvals = [decide("initial-" + suffix + "-" + str(index), value, principal, identifier)["decision_id"]
                          for suffix, value, principal in (("a", reviewer_a, REVIEWER_A), ("b", reviewer_b, REVIEWER_B))
                          for index, identifier in enumerate((DOC_A, CODE_A), 1)]
    original_objection = decide("original-c-objection", reviewer_c, REVIEWER_C, DOC_A, "request_changes")
    target_updates = [update_catalog("before-backup-d-" + str(index + 1)) for index in range(3)]
    target_packet = packet("audit-frozen-target")
    target_generation = generation
    before_abstain = observe("veto-before-snapshot")
    _require(not before_abstain["eligible"]
             and any(item["code"] == "governance_changes_requested" for item in before_abstain["reasons"]),
             "the unrelated catalog history removed C's active objection")
    stage("quorum-veto-and-three-catalog-edges", "Original A/B approvals and C's objection survive three unrelated D updates; the active objection still blocks acceptance.",
          {"updates": target_updates, "observation": before_abstain})

    start_request = {"decision_id": original_objection["decision_id"], "expected_policy_generation": generation,
                     "expected_target_packet_digest": target_packet["packet_digest"], "operation_id": "snapshot-audit-start"}
    audit_requests["start"] = start_request
    started = remember_audit("source-start", source.continuity_audit_start(**start_request, credential=admin))
    audit_id = started["audit"]["audit_id"]
    step_request = {"audit_id": audit_id, "expected_checkpoint_id": started["checkpoint"]["checkpoint_id"],
                    "limit": 1, "operation_id": "snapshot-audit-first-step"}
    audit_requests["first-step"] = step_request
    partial = remember_audit("source-partial-before-backup", source.continuity_audit_advance(**step_request, credential=admin))
    partial_checkpoint_id = partial["checkpoint"]["checkpoint_id"]
    _require(partial["checkpoint"]["state"] == "pending" and partial["checkpoint"]["total_checked_transition_count"] == 1,
             "the audit did not retain its first one-of-three checkpoint")
    pending_payload = _proposal(source.snapshot(), _document(DOC_D, 4),
                                "A separate D author candidate remains leased while the snapshot is captured.")
    source.enqueue("snapshot-inflight-doc-d", "doc_author", {"proposal": pending_payload})
    pending_lease = source.claim_task(DOCUMENT_AUTHOR, roles=["doc_author"], credential=document_author, lease_seconds=300)
    _require(pending_lease is not None and pending_lease["task_id"] == "snapshot-inflight-doc-d" and pending_lease["token"] == 1,
             "the source did not retain the expected first D author lease")
    stage("partial-audit-and-active-author-lease", "The audit is pending after one of three edges. A separate D author lease has token 1 and remains unsubmitted at capture.",
          {"audit": partial, "lease": pending_lease})

    # A real read-only SQLite transaction pins revision 1 while ordinary Store
    # APIs commit a decision and acceptance. The keeper performs SELECTs only;
    # BEGIN/ROLLBACK delimit its read transaction, without database mutations.
    keeper = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True, timeout=30, isolation_level=None)
    try:
        keeper.execute("BEGIN")
        keeper_revision_before = int(keeper.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        _require(keeper_revision_before == 1, "the old SQLite reader was not pinned before acceptance")
        final_abstain = decide("explicit-current-c-abstain", reviewer_c, REVIEWER_C, DOC_A, "abstain")
        final_observation = observe("acceptance-after-explicit-abstain")
        _require(final_observation["eligible"], "C's explicit abstain did not satisfy ordinary acceptance rules")
        accepted_packet = packet("accepted-current")
        accepted = source.accept(main_id, credential=coordinator, expected_policy_generation=generation)
        receipt = accepted["governance_receipt"]
        _require(accepted["accepted"] and accepted["revision"] == 2 and receipt["schema_version"] == 4
                 and set(receipt["review_continuity"]["used_decisions"]) == set(original_approvals)
                 and all(proof["mode"] == "preserved_catalog" and len(proof["transitions"]) == len(target_updates)
                         for proof in receipt["review_continuity"]["used_decisions"].values()),
                 "the ordinary acceptance did not retain the four original approvals through three edges")
        keeper_revision_after_accept = int(keeper.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        wal_path = database.with_name(database.name + "-wal")
        wal_before_backup = {"exists": wal_path.is_file(), "bytes": wal_path.stat().st_size if wal_path.is_file() else 0}
        _require(keeper_revision_after_accept == 1 and source.status()["revision"] == 2
                 and wal_before_backup["exists"] and wal_before_backup["bytes"] > 0,
                 "the keeper did not preserve an older read snapshot alongside the committed live WAL")
        source_before_backup = source.status()
        source_events_before_backup = source.events()
        source_state_at_capture = source.snapshot()
        source_governance_at_capture = source.governance_status(main_id)
        backup_summary = cli("create-private-backup-with-old-reader", "create", [
            "--workspace", str(source_directory), "--credential", str(private / "admin.credential"),
            "--destination", str(bundle),
        ])
        source_after_backup = source.status()
        _require(source_before_backup == source_after_backup and source.events() == source_events_before_backup
                 and backup_summary["revision"] == 2
                 and backup_summary["event_count"] == len(source_events_before_backup),
                 "backup changed source domain state or captured the old reader instead of committed revision 2")
        inspected_backup = cli("inspect-complete-private-backup", "inspect", ["--bundle", str(bundle)])
        captured = Store(bundle / "state.sqlite3")
        captured_access = captured.snapshot_access()
        _inspection(captured_access)
        captured_state = captured.snapshot()
        captured_status = captured.status()
        captured_governance = captured.governance_status(main_id)
        captured_events = captured.events()
        captured_tasks = captured.tasks()
        captured_audit = remember_audit("captured-partial-audit", captured.continuity_audit_show(audit_id))
        _require(canonical(captured_state) == canonical(source_state_at_capture)
                 and captured_events == source_events_before_backup
                 and captured_governance["receipts"] == [receipt]
                 and captured_audit["current_checkpoint_id"] == partial_checkpoint_id,
                 "the private backup did not preserve the committed receipt, event history and partial audit")
        keeper_revision_after_backup = int(keeper.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])
        _require(keeper_revision_after_backup == keeper_revision_before,
                 "backup moved the pre-existing reader's transaction")
    finally:
        keeper.rollback()
        keeper.close()
    wal_evidence = {"read_only_keeper": True, "sql_data_mutations_by_keeper": False,
                    "keeper_revision_before_acceptance": keeper_revision_before,
                    "keeper_revision_after_acceptance": keeper_revision_after_accept,
                    "keeper_revision_after_backup": keeper_revision_after_backup,
                    "committed_source_revision": source_after_backup["revision"],
                    "backup_revision": backup_summary["revision"], "wal_before_backup": wal_before_backup,
                    "source_status_before": source_before_backup, "source_status_after": source_after_backup,
                    "source_events_unchanged": source_events_before_backup == source.events(),
                    "keeper_released_after_backup": True, "raw_source_database_copied": False}
    stage("committed-WAL-state-captured", "A SELECT-only reader remains at revision 1 while ordinary APIs accept revision 2 into WAL; the backup captures revision 2 without changing source events.",
          {"wal_evidence": wal_evidence, "acceptance": accepted, "abstain": final_abstain,
           "backup_summary": backup_summary, "inspection": inspected_backup, "captured_access": captured_access})

    revoked_b = source.governance_revoke(grants["reviewer-b"]["grant_id"], credential=admin)
    finish_request = {"audit_id": audit_id, "expected_checkpoint_id": partial_checkpoint_id,
                      "limit": 2, "operation_id": "snapshot-live-audit-complete"}
    audit_requests["live-complete"] = finish_request
    live_complete = remember_audit("source-complete-after-backup", source.continuity_audit_advance(**finish_request, credential=admin))
    submitted_d = source.submit(pending_lease["task_id"], DOCUMENT_AUTHOR, pending_lease["token"],
                                pending_lease["payload"]["proposal"], credential=document_author)
    later_update = update_catalog("source-only-after-backup-d-update")
    _require(live_complete["checkpoint"]["state"] == "history_complete"
             and live_complete["checkpoint"]["total_checked_transition_count"] == len(target_updates)
             and not submitted_d["replayed"], "the ordinary source did not continue after backup")
    source_after_continuation = source.status()
    source_governance_after = source.governance_status(main_id)
    source_task_after = next(task for task in source.tasks() if task["task_id"] == pending_lease["task_id"])
    source_receipt_replay = source.accept(main_id, credential=coordinator, expected_policy_generation=generation)
    _require(source_receipt_replay["replayed"] and source_receipt_replay["governance_receipt"] == receipt
             and source_task_after["state"] == "done", "live historical replay or the original author lease stopped working")
    stage("source-continues-after-capture", "The live source revokes B, completes the audit, submits the original D lease and changes D policy. Historical acceptance replay still returns the same receipt.",
          {"revoked_b": revoked_b, "completed_audit": live_complete, "d_submission": submitted_d,
           "source_task": source_task_after, "later_policy_update": later_update, "accepted_replay": source_receipt_replay})

    restored_summary = cli("restore-new-private-inspection-copy", "restore", [
        "--bundle", str(bundle), "--destination", str(restored_directory),
    ])
    restored_inspection = cli("inspect-restored-private-copy", "inspect", ["--bundle", str(restored_directory)])
    restored = Store(restored_directory / "state.sqlite3")
    restored_access = restored.snapshot_access()
    _inspection(restored_access)
    restored_state = restored.snapshot()
    restored_status = restored.status()
    restored_governance = restored.governance_status(main_id)
    restored_events = restored.events()
    restored_tasks = restored.tasks()
    restored_task = next(task for task in restored_tasks if task["task_id"] == pending_lease["task_id"])
    restored_audit = remember_audit("restored-partial-audit", restored.continuity_audit_show(audit_id))
    restored_start_recovery = remember_audit("restored-start-recovery", restored.continuity_audit_recover(
        start_request["operation_id"], credential=admin))
    restored_step_recovery = remember_audit("restored-step-recovery", restored.continuity_audit_recover(
        step_request["operation_id"], credential=admin))
    restored_catalogue = restored.continuity_audit_catalogue(limit=10)
    source_catalogue = source.continuity_audit_catalogue(limit=10)
    _require(canonical(restored_state) == canonical(captured_state) and restored_events == captured_events
             and restored_governance["receipts"] == [receipt]
             and restored_audit["current_checkpoint_id"] == restored_start_recovery["current_checkpoint_id"]
             == restored_step_recovery["current_checkpoint_id"] == partial_checkpoint_id
             and canonical(restored_step_recovery["checkpoint"]) == canonical(partial["checkpoint"])
             and restored_task["state"] == "leased" and restored_task["token"] == pending_lease["token"]
             and restored_catalogue["entries"][0]["observed_head"]["state"] == "pending"
             and source_catalogue["entries"][0]["observed_head"]["state"] == "history_complete",
             "the restored copy followed later source changes instead of retaining captured historical records")
    snapshot_b = next(grant for grant in restored_governance["grants"] if grant["grant_id"] == grants["reviewer-b"]["grant_id"])
    source_b = next(grant for grant in source_governance_after["grants"] if grant["grant_id"] == grants["reviewer-b"]["grant_id"])
    _require(snapshot_b["revoked"] is False and source_b["revoked"] is True,
             "the snapshot did not retain B's historical pre-revocation grant state")
    grant_comparison = {"captured_b": snapshot_b, "later_source_b": source_b,
                        "historical_metadata_only": True, "snapshot_grant_activates_writes": False}
    stage("restored-historical-state-is-readable", "The restored copy preserves receipt, packets, partial checkpoint and the old leased task; B remains historically unrevoked there while the live source has revoked B.",
          {"restore_summary": restored_summary, "restore_inspection": restored_inspection,
           "access": restored_access, "grant_comparison": grant_comparison,
           "snapshot_audit": restored_audit, "source_catalogue": source_catalogue, "snapshot_catalogue": restored_catalogue})

    forbidden_publish = private / "forbidden-publish"
    expected_exception("restored-accept-historical-replay", lambda: restored.accept(
        main_id, credential=coordinator, expected_policy_generation=target_generation), "store_snapshot_quarantined", restored)
    expected_exception("restored-reviewer-b-decision-replay", lambda: restored.governance_decide(
        **decision_requests["initial-b-1"], credential=reviewer_b), "store_snapshot_quarantined", restored)
    expected_exception("restored-old-author-lease-submit", lambda: restored.submit(
        pending_lease["task_id"], DOCUMENT_AUTHOR, pending_lease["token"], pending_lease["payload"]["proposal"],
        credential=document_author), "store_snapshot_quarantined", restored)
    expected_exception("restored-enqueue", lambda: restored.enqueue(
        "must-not-enter-restored-queue", "doc_author", {"proposal": pending_payload}), "store_snapshot_quarantined", restored)
    expected_exception("restored-publish", lambda: restored.publish(forbidden_publish), "store_snapshot_quarantined", restored)
    _require(not forbidden_publish.exists() and restored.status() == restored_status and restored.events() == restored_events,
             "a quarantined write changed the restored Store or created a publication directory")
    stage("restored-writes-and-publish-rejected", "The restored copy rejects acceptance replay, B's old decision, the old lease submit, enqueue and publish before domain changes; publish creates no output directory.",
          {"rejections": operations[-5:], "publication_directory_created": forbidden_publish.exists()})

    direct_bundle = Store(bundle / "state.sqlite3")
    direct_access = direct_bundle.snapshot_access()
    _inspection(direct_access)
    expected_exception("direct-backup-database-enqueue", lambda: direct_bundle.enqueue(
        "must-not-enter-backup-queue", "doc_author", {"proposal": pending_payload}), "store_snapshot_quarantined", direct_bundle)
    database_only_directory = private / "database-only"
    database_only_directory.mkdir(mode=0o700)
    database_only_directory.chmod(0o700)
    database_only_path = database_only_directory / "state.sqlite3"
    shutil.copyfile(bundle / "state.sqlite3", database_only_path)
    database_only_path.chmod(0o600)
    # Only the already-quarantined snapshot DB is copied here. The live source
    # is never copied as a raw file and neither intent nor manifest is copied.
    database_only = Store(database_only_path)
    database_only_access = database_only.snapshot_access()
    _inspection(database_only_access)
    expected_exception("database-only-copy-enqueue", lambda: database_only.enqueue(
        "must-not-enter-database-only-queue", "doc_author", {"proposal": pending_payload}), "store_snapshot_quarantined", database_only)
    _require(database_only.snapshot() == captured_state, "the database-only quarantined copy lost its captured state")
    stage("quarantine-persists-in-the-database", "Opening the backup DB directly and copying only that marked DB without sidecars both retain the inspection boundary; enqueue is rejected in each copy.",
          {"direct_bundle_access": direct_access, "database_only_access": database_only_access,
           "database_only_copy_source": "private/backup-01/state.sqlite3", "source_database_raw_copy": False,
           "rejections": operations[-2:]})

    incomplete = private / "incomplete-operator-directory"
    incomplete.mkdir(mode=0o700)
    incomplete.chmod(0o700)
    occupied_restore = private / "existing-operator-destination"
    occupied_restore.mkdir(mode=0o700)
    occupied_restore.chmod(0o700)
    unauthorized_destination = private / "unauthorized-backup"
    expected_exception("backup-without-administrator", lambda: store_backup.create_backup(
        database, unauthorized_destination, credential=None), "governance_unauthorized", source)
    expected_exception("backup-existing-destination", lambda: store_backup.create_backup(
        database, bundle, credential=admin), "snapshot_destination_exists", source)
    expected_exception("inspect-incomplete-empty-directory", lambda: store_backup.inspect_bundle(
        incomplete), "snapshot_incomplete")
    expected_exception("restore-existing-empty-destination", lambda: store_backup.restore_bundle(
        bundle, occupied_restore), "snapshot_destination_exists")
    _require(not any(incomplete.iterdir()) and not any(occupied_restore.iterdir()),
             "an operator rejection overwrote an existing empty directory")
    stage("safe-operator-rejections", "Missing authority, an existing backup destination, an incomplete empty directory and an existing restore destination are rejected without SQL corruption or overwrites.", operations[-4:])

    final_source_status = source.status()
    final_source_governance = source.governance_status(main_id)
    final_source_events = source.events()
    final_restored_status = restored.status()
    _require(source.snapshot_access()["inspection_only"] is False and final_restored_status == restored_status
             and restored.events() == restored_events and direct_bundle.events() == captured_events
             and final_source_status == source_after_continuation,
             "source or snapshot domain state changed during read-only inspection and rejected operations")
    credentials_in_bundle = any(path.suffix == ".credential" for path in bundle.iterdir())
    _require(not credentials_in_bundle, "bearer credential files were included in the private snapshot bundle")
    stored_packet_ids = {entry["result"]["content_packet"]["packet_id"] for entry in decision_history}
    stored_packet_ids.update(root[side]["packet_id"] for root in audit_headers.values() for side in ("source_packet", "target_packet"))
    stored_packet_ids.add(accepted_packet["packet_id"])
    restored_packet_ids: list[str] = []
    for identifier in sorted(stored_packet_ids):
        live_body = source.content_packet(identifier)
        restored_body = restored.content_packet(identifier)
        _require(canonical(live_body) == canonical(restored_body), "an original packet body changed across backup and restore")
        remember_packet("stored-source-" + identifier, live_body)
        remember_packet("stored-restored-" + identifier, restored_body)
        restored_packet_ids.append(identifier)
    safe_summaries = {"created_backup": backup_summary, "inspected_backup": inspected_backup,
                      "restored": restored_summary, "inspected_restore": restored_inspection}
    access_results = {"source": source.snapshot_access(), "captured_bundle": captured_access,
                      "restored": restored_access, "direct_bundle": direct_access, "database_only": database_only_access}
    artifacts = {
        "initial-state.json": initial_snapshot, "source-state-at-capture.json": source_state_at_capture,
        "captured-state.json": captured_state, "restored-state.json": restored_state, "final-state.json": source.snapshot(),
        "authors.json": authors, "composition.json": main, "formal-review.json": formal_review,
        "proposals.json": {"composite": source.proposal(main_id),
                           "authors": {label: source.proposal(value["proposal_id"]) for label, value in authors.items()},
                           "source-d-after-backup": source.proposal(submitted_d["proposal_id"])},
        "activation.json": {"governance": enabled_governance, "authorship": enabled_authorship,
                            "content": enabled_content, "continuity": enabled_continuity},
        "policy.json": active_catalog, "editorial-policy.json": editorial_policy,
        "content-policy-history.json": content_policy_history,
        "governance-policy-history.json": final_source_governance["policy_history"],
        "captured-governance-policy-history.json": captured_governance["policy_history"],
        "catalog-updates.json": catalog_updates,
        "transitions.json": [event["data"] for event in final_source_events if event["type"] == "review_continuity_transition"],
        "captured-transitions.json": [event["data"] for event in captured_events if event["type"] == "review_continuity_transition"],
        "packets.json": {"labels": packet_labels, "packets": packets, "stored_packet_ids": sorted(stored_packet_ids),
                         "restored_packet_ids": restored_packet_ids},
        "decisions.json": decision_history, "decision-requests.json": decision_requests,
        "observations.json": observations, "audits.json": audit_headers, "checkpoints.json": checkpoints,
        "audit-requests.json": audit_requests, "audit-results.json": audit_results,
        "catalogue-pages.json": {"source_after_continuation": source_catalogue, "restored_at_capture": restored_catalogue},
        "backup-summaries.json": safe_summaries, "snapshot-access.json": access_results, "wal-evidence.json": wal_evidence,
        "statuses.json": {"source_before_backup": source_before_backup, "source_after_backup": source_after_backup,
                          "source_after_continuation": final_source_status, "captured": captured_status, "restored": final_restored_status},
        "grant-comparison.json": grant_comparison, "grants.json": grants,
        "tasks.json": {"lease_at_capture": pending_lease, "captured": captured_tasks, "restored": restored_tasks,
                       "source_after_continuation": source.tasks()},
        "source-governance-at-capture.json": source_governance_at_capture,
        "captured-governance.json": captured_governance, "restored-governance.json": restored_governance,
        "governance-final.json": final_source_governance,
        "events.json": final_source_events, "captured-events.json": captured_events, "restored-events.json": restored_events,
        "acceptance.json": accepted, "receipts.json": {"source_at_capture": receipt,
                                                       "captured": captured_governance["receipts"],
                                                       "restored": restored_governance["receipts"]},
        "replays.json": {"live_source_acceptance": source_receipt_replay},
        "operations.json": operations, "cli-outcomes.json": cli_outcomes,
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "kind": "private_WAL_snapshot_and_quarantined_restore_product_example",
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(), "workers": workers,
        "actors": {"authenticated_local_capability_principals": True, "persons_authenticated": False,
                   "human_review_recorded": False, "independent_people_or_models_established": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "main_proposal_id": main_id, "main_audit_id": audit_id,
        "main_source_decision_id": original_objection["decision_id"],
        "main_audit_start_operation_id": start_request["operation_id"], "main_audit_step_operation_id": step_request["operation_id"],
        "main_partial_checkpoint_id": partial_checkpoint_id,
        "main_live_completed_checkpoint_id": live_complete["checkpoint"]["checkpoint_id"],
        "main_target_packet_id": target_packet["packet_id"], "main_target_governance_generation": target_generation,
        "accepted_content_packet_id": accepted_packet["packet_id"], "original_approval_ids": original_approvals,
        "backup_id": backup_summary["backup_id"], "restore_id": restored_summary["backup_id"],
        "captured_governance_policy_generation": captured_governance["policy_generation"],
        "final_source_governance_policy_generation": generation,
        "stages": stages, "initial_runtime_revision": initial_snapshot["revision"], "final_runtime_status": final_source_status,
        "summary": {
            "author_job_count_before_capture": len(authors), "author_pool_workers": workers,
            "observed_author_thread_count": len({value["thread"] for value in authors.values()}),
            "authenticated_local_principal_count": len({value["principal_id"] for value in grants.values()} | {ADMINISTRATOR}),
            "composite_proposal_count": 1, "decision_count": len(decision_history), "stage_count": len(stages),
            "catalog_update_count": len(catalog_updates), "catalog_updates_before_capture": len(target_updates),
            "catalog_updates_after_capture": len(catalog_updates) - len(target_updates),
            "audit_count": len(audit_headers), "checkpoint_count": len(checkpoints),
            "captured_audit_checked_transitions": partial["checkpoint"]["total_checked_transition_count"],
            "later_source_audit_checked_transitions": live_complete["checkpoint"]["total_checked_transition_count"],
            "captured_runtime_revision": captured_status["revision"], "source_runtime_revision": final_source_status["revision"],
            "keeper_runtime_revision": keeper_revision_before, "committed_WAL_revision_captured": backup_summary["revision"] == 2,
            "backup_did_not_change_source_events": wal_evidence["source_events_unchanged"],
            "captured_event_count": len(captured_events), "source_event_count": len(final_source_events),
            "restored_event_count": len(restored_events), "cli_child_process_count": len(cli_outcomes),
            "quarantined_write_rejection_count": sum(value.get("code") == "store_snapshot_quarantined" for value in operations),
            "operator_api_rejection_count": sum(value.get("rejected", False) and value.get("code") != "store_snapshot_quarantined" for value in operations),
            "source_accepted_proposal_count": final_source_status["proposals"].get("accepted", 0),
            "snapshot_accepted_proposal_count": restored_status["proposals"].get("accepted", 0),
            "acceptance_receipt_schema": receipt["schema_version"], "original_approval_ids_retained": set(receipt["review_continuity"]["used_decisions"]) == set(original_approvals),
            "historical_B_grant_unrevoked_in_snapshot": snapshot_b["revoked"] is False,
            "later_source_B_grant_revoked": source_b["revoked"] is True,
            "old_source_lease_completed_after_capture": source_task_after["state"] == "done",
            "captured_task_state": restored_task["state"], "snapshot_activated": False,
            "snapshot_inspection_only": restored_access["inspection_only"], "snapshot_domain_writes_allowed": restored_access["domain_writes_allowed"],
            "credential_files_in_bundle": credentials_in_bundle, "private_database_or_manifest_exported": False,
            "private_database_digest_exported": False, "raw_source_database_copied": False,
            "network_failure_simulated": False, "process_crash_simulated": False,
            "measured_capacity": False, "semantic_equivalence_proved": False, "release_created": False,
        },
        "private_file_layout": {"source_workspace": "private/source", "backup_bundle": "private/backup-01",
                                "restored_workspace": "private/restore-01", "database_only_copy": "private/database-only",
                                "administrator": "private/admin.credential", "document_author": "private/document-author.credential",
                                "code_author": "private/code-author.credential", "integrator": "private/integrator.credential",
                                "reviewer_a": "private/reviewer-a.credential", "reviewer_b": "private/reviewer-b.credential",
                                "reviewer_c": "private/reviewer-c.credential", "coordinator": "private/coordinator.credential"},
        "limitations": [
            "The backup uses SQLite online backup of committed source state while a separate read-only transaction remains at an older revision. No raw live database file is copied.",
            "Private checksums, manifest, schema fingerprints, intent and database files remain outside all public artifacts. Safe inspection summaries report validation outcomes and counts only.",
            "The backup and restored DB are marked inspection-only. Historical grants, leases, approvals and accepted receipts are readable records, not permission to resume writes in a copy.",
            "The marker also survives a copy of only the already-quarantined snapshot DB without filesystem sidecars; no activation operation is provided.",
            "The original source continues independently after capture: revocation, audit advance, D task submission and a later content policy are absent from the captured copy.",
            "Checksums and SQLite integrity/foreign-key checks establish internal file consistency under trust in the implementation and local owner, not external authenticity or semantic truth.",
            "Public snapshot records may disclose the example's public artifact history; bearer files are separate and the private DB still contains credential hashes, so the bundle must remain private.",
            "Configured worker counts and child processes are bounded functional workflow evidence; no throughput, load or service-capacity measurement is made.",
            "The workflow executes no proposed Python, uses no external LLM, performs no SQL corruption and reproduces no process crash or network failure.",
            "Local capability principals establish neither independent people/models nor human understanding, factual truth, inventory completeness or program behavior.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример частного снимка Store и восстановления для осмотра", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Три локальных авторских задания и одна композиция подготовили вымышленный документ и литеральную",
        "Python-конфигурацию. A/B одобрили обе записи, C запросил изменения. Предлагаемый код не исполнялся.", "",
        "## Последовательность", "", "| Этап | Наблюдение | Ревизия источника |", "|---|---|---:|",
    ]
    lines += [f"| [{value['label']}]({value['artifact']}) | {value['explanation']} | {value['source_revision']} |" for value in stages]
    lines += [
        "", "## Что попало в снимок", "",
        "После трёх несвязанных правок D аудит C прошёл один из трёх переходов. Отдельное авторское задание D",
        "оставалось с живой арендой token 1. Затем SQLite читатель mode=ro открыл транзакцию и прочёл ревизию 1.",
        "Обычные API записали явный abstain C и приняли составное предложение: ревизия 2, receipt schema 4.", "",
        "Старый читатель продолжал видеть ревизию 1; файл WAL существовал и был непустым. Отдельный CLI процесс",
        "создал согласованный снимок уже принятой ревизии 2. Статус и события источника при этом не изменились.",
        "Inspect проверил частные контрольные суммы, схему, SQLite integrity и foreign keys. В отчёт вошли только",
        "безопасные результаты проверки, случайный backup ID и счётчики. Частные manifest и хеш базы не раскрываются.", "",
        "## Источник продолжил работу", "",
        "После снимка источник отозвал B, завершил аудит, отправил предложение по прежней живой аренде D и",
        "выполнил отдельную четвёртую правку D. Прежняя квитанция принятия продолжила возвращаться историческим replay.",
        "Эти более поздние изменения отсутствуют в восстановленной копии: там B исторически не отозван, аудит",
        "остаётся pending после одного перехода, задание D остаётся leased, а квитанция принятия совпадает точно.", "",
        "## Восстановленная копия доступна для чтения", "",
        "Restore создал новый частный каталог с inspection-only базой. Через обычные API прочитаны исходные IDs,",
        "полные source/target packets, квитанция, каталог аудита и точные результаты recover по operation ID.",
        "Попытки accept replay, прежнего решения B, submit старой аренды, enqueue и publish были отклонены",
        "с store_snapshot_quarantined. Каталог публикации не появился, статус и события копии не изменились.", "",
        "Прямое открытие DB из backup и копирование только уже карантинной DB без intent/manifest sidecars также",
        "сохранили запрет записи. Исторические допуски и аренды в снимке не активируются. Действия активации нет.", "",
        "## Публичные основания", "",
        "- [Машинный отчёт](report.json), [безопасные сводки backup/inspect/restore](backup-summaries.json), [исходы CLI](cli-outcomes.json).",
        "- [WAL и старый читатель](wal-evidence.json), [статусы](statuses.json), [границы доступа](snapshot-access.json).",
        "- [Состояние при захвате](captured-state.json), [восстановленное](restored-state.json), [поздний источник](final-state.json).",
        "- [Сравнение допуска B](grant-comparison.json), [задачи и аренда](tasks.json), [операции и отказы](operations.json).",
        "- [Квитанции](receipts.json), [принятие](acceptance.json), [исторический replay](replays.json).",
        "- [Пакеты](packets.json), [решения](decisions.json), [наблюдения](observations.json), [предложения](proposals.json).",
        "- [Аудит](audits.json), [checkpoints](checkpoints.json), [результаты и recover](audit-results.json), [каталоги](catalogue-pages.json).",
        "- [Content history](content-policy-history.json), [governance history](governance-policy-history.json), [переходы](transitions.json).",
        "- [События снимка](captured-events.json), [события восстановления](restored-events.json), [поздние события источника](events.json).", "",
        "Полные public Store bodies сохранены без вырезания полей, изменяющего их адреса. Файлы DB, intent,",
        "manifest и credentials находятся только в private/ и не копируются в публичные JSON/MD или Git.", "",
        "## Границы выполненного", "",
        f"Авторский пул: {workers} Python workers; наблюдавшихся потоков: {report['summary']['observed_author_thread_count']}; заданий до снимка: {len(authors)}.",
        f"Отдельных CLI процессов: {len(cli_outcomes)}; решений: {len(decision_history)}; checkpoints: {len(checkpoints)}.",
        f"Правок D до снимка: {len(target_updates)}; после снимка: {len(catalog_updates) - len(target_updates)}; отказы записи в копии: {report['summary']['quarantined_write_rejection_count']}.",
        "Размер пула и число ролей не являются числом независимых моделей или измеренной ёмкостью сервиса.",
        "Контрольные суммы и integrity/fk устанавливают внутреннюю согласованность при доверии к реализации,",
        "SQLite и владельцу среды. Они не доказывают внешнюю подлинность, истинность прозы или поведение кода.",
        "Сбой сети, авария процесса и повреждение SQL не воспроизводились. Внешние LLM и человеческая экспертиза отсутствуют.", "",
        "Частные каталоги имеют права 0700, DB/manifest/intent/credentials — 0600. Bundle содержит частные хеши",
        "допусков внутри DB и должен оставаться частным. Файлы bearer credentials хранятся отдельно от bundle.",
        "Создание снимка и локальных отчётов не является публикацией Git, выпуском или запуском восстановленной копии.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
