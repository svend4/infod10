"""Transfer a fenced source into a fresh, path-bound local workspace.

The historical source and its archive remain fenced. Only public Store records
and the transfer API's fixed safe responses are exported. Private origin rows,
manifests, request digests and credential denylists are never parsed here.
Proposed Python remains data; all participants are programmed local principals.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
from threading import Barrier, current_thread
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .dossier_demo import _proposal
from .governance_cli import prepare_credential, read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .releases import load_release
from .review_continuity_demo import CODE_A, CODE_D, DOC_A, DOC_D, _document, _file
from .source_fence_demo import run_source_fence_demo
from .store import Store, WorkbenchError, canonical
from .store_backup_demo import CODE_AUTHOR as OLD_CODE_AUTHOR, SUMMARY_FIELDS


NEW_ADMINISTRATOR = "transfer-administrator"
DOCUMENT_AUTHOR = "transfer-document-author"
CODE_AUTHOR = "transfer-code-author"
INTEGRATOR = "transfer-integrator"
REVIEWER_A = "transfer-reviewer-a"
REVIEWER_B = "transfer-reviewer-b"
COORDINATOR = "transfer-coordinator"
RECEIPT_FIELDS = {
    "schema_version", "rules_version", "transfer_id", "workspace_id", "operation_id", "created_at",
    "source_fence_id", "source_epoch", "source_revision", "source_event_high_water_before",
    "transfer_event_seq", "artifact_count", "source_count", "new_administrator_id",
    "dependencies_preserved", "old_authority_transferred", "external_fencing_established",
}
DESTINATION_FIELDS = {
    "workspace_id", "revision", "artifact_count", "source_count", "governance_enabled",
    "authorship_enabled", "content_enabled", "continuity_enabled",
}
REPORT_FIELDS = {
    "rules_version", "receipt", "destination", "replayed", "resumed",
    "old_authority_transferred", "dependencies_preserved", "external_fencing_established",
}
PUBLIC_FLAGS = {
    "old_authority_transferred": False, "dependencies_preserved": True,
    "external_fencing_established": False,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Source transfer demonstration stopped: " + message)


def run_source_transfer_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a fresh transfer, new authenticated work and a quarantined backup."""
    from . import source_transfer, store_backup

    workspace = Path(workspace).expanduser().resolve()
    baseline_report = run_source_fence_demo(workspace, workers)
    private = workspace / "private"
    source_database = private / "source" / "state.sqlite3"
    destination_directory = private / "new-workspace"
    destination_database = destination_directory / "state.sqlite3"
    history_database = destination_directory / "history.sqlite3"
    old_snapshot_database = private / "restore-01" / "state.sqlite3"
    administrator_file = private / "admin.credential"
    administrator = read_credential(administrator_file)
    old_code_author = read_credential(private / "code-author.credential")
    new_administrator_file = private / "new-admin.credential"
    repository = Path(__file__).resolve().parents[1]
    operation_id = "source-transfer-demo-create"
    source, old_snapshot = Store(source_database), Store(old_snapshot_database)
    baseline_public_files = sorted(path.name for path in workspace.iterdir()
                                   if path.is_file() and path.suffix in {".json", ".md"})
    private_markers: list[str] = []
    for path in private.glob("*.credential"):
        value = read_credential(path)
        private_markers.extend((value, hashlib.sha256(value.encode("ascii")).hexdigest()))

    def new_credential(label: str) -> str:
        value = prepare_credential(private / (label + ".credential"))
        private_markers.extend((value, hashlib.sha256(value.encode("ascii")).hexdigest()))
        return value

    new_administrator = new_credential("new-admin")
    forbidden_fields = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path", "history_path",
    }

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not forbidden_fields.intersection(value), "a private transfer field reached public output")
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

    def safe_report(value: Any) -> dict[str, Any]:
        _require(isinstance(value, dict) and set(value) == REPORT_FIELDS
                 and value.get("rules_version") == "source-transfer-1"
                 and all(value.get(key) is expected for key, expected in PUBLIC_FLAGS.items())
                 and type(value.get("replayed")) is bool and type(value.get("resumed")) is bool,
                 "the transfer API returned unsupported public report fields")
        receipt, destination = value["receipt"], value["destination"]
        _require(isinstance(receipt, dict) and set(receipt) == RECEIPT_FIELDS
                 and receipt.get("schema_version") == 1 and receipt.get("rules_version") == "source-transfer-1"
                 and all(receipt.get(key) is expected for key, expected in PUBLIC_FLAGS.items())
                 and bool(re.fullmatch(r"sft_[0-9a-f]{32}", receipt.get("transfer_id", "")))
                 and bool(re.fullmatch(r"wsp_[0-9a-f]{32}", receipt.get("workspace_id", ""))),
                 "the transfer receipt lost its identity or authority boundary")
        _require(isinstance(destination, dict) and set(destination) == DESTINATION_FIELDS
                 and destination["workspace_id"] == receipt["workspace_id"]
                 and destination["revision"] == receipt["source_revision"]
                 and all(type(destination[key]) is bool for key in (
                     "governance_enabled", "authorship_enabled", "content_enabled", "continuity_enabled")),
                 "the destination summary does not describe its imported baseline")
        return public({key: value[key] for key in sorted(REPORT_FIELDS)})

    def safe_backup_summary(value: Any) -> dict[str, Any]:
        _require(isinstance(value, dict) and set(value) == SUMMARY_FIELDS
                 and value.get("inspection_only") is True and value.get("domain_writes_allowed") is False,
                 "the new workspace backup lost its inspection boundary")
        return public({key: value[key] for key in sorted(SUMMARY_FIELDS)})

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(),
                "access": store.snapshot_access()}

    def observations_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {
                key: child for key, child in value["governance"].items() if key != "observed_at"
            }}
        return canonical(comparable(before)) == canonical(comparable(after))

    stages: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    transfer_results: dict[str, dict[str, Any]] = {}
    releases: dict[str, dict[str, Any]] = {}

    def stage(label: str, explanation: str, value: Any) -> None:
        name = f"transfer-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(name, {"label": label, "explanation": explanation,
                          "source_status": source.status(), "observations": value})
        stages.append({"label": label, "explanation": explanation, "artifact": name,
                       "source_event_count": source.status()["events"]})

    def rejected(label: str, action: Callable[[], Any], expected: str,
                 watched: Store, *, category: str) -> dict[str, Any]:
        before = observe(watched)
        try:
            action()
        except WorkbenchError as error:
            _require(error.code == expected, "unexpected rejection for " + label + ": " + error.code)
            _require(observations_match(before, observe(watched)),
                     "a rejected operation changed public observations")
            result = {"label": label, "category": category, "code": error.code,
                      "rejected": True, "public_observations_unchanged": True,
                      "comparison_excludes_only_governance_wrapper_observed_at": True}
            rejections.append(result)
            return result
        raise RuntimeError("Source transfer demonstration stopped: expected rejection for " + label)

    def cli(label: str, action: str) -> dict[str, Any]:
        arguments = ["--workspace", str(source_database.parent if action == "create" else destination_directory)]
        if action == "create":
            arguments += ["--destination", str(destination_directory), "--credential", str(administrator_file),
                          "--new-administrator", NEW_ADMINISTRATOR, "--new-credential", str(new_administrator_file),
                          "--operation-id", operation_id]
        with subprocess.Popen([sys.executable, "-m", "agentwiki", "store-transfer", action, *arguments],
                              cwd=repository, shell=False, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate()
                raise RuntimeError("Source transfer demonstration stopped: CLI child timed out") from error
            matched = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
            code = matched.group(1) if matched else None
            cli_outcomes.append({"label": label, "command": "store-transfer", "action": action,
                                 "child_process_id": process.pid, "returncode": process.returncode,
                                 "error_code": code, "stderr_empty": not stderr,
                                 "shell": False, "completed_child_process": True})
        _require(process.returncode == 0 and not stderr,
                 "a transfer CLI child failed with " + (code or "an unspecified error"))
        try:
            value = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Source transfer demonstration stopped: invalid CLI JSON") from error
        return safe_report(value)

    def publish(label: str, store: Store) -> dict[str, Any]:
        output = workspace / ("transfer-" + label + "-release")
        value = store.publish(output)
        loaded = load_release(value["path"])
        expected = store.snapshot()
        _require(loaded["snapshot"]["dependencies"] == expected["dependencies"]
                 and loaded["snapshot"]["artifacts"] == expected["artifacts"]
                 and loaded["snapshot"]["revision"] == expected["revision"],
                 "the published imported dependencies did not survive release loading")
        result = {"label": label, "release_id": value["release_id"], "revision": value["revision"],
                  "manifest_schema_version": loaded["manifest"]["schema_version"],
                  "manifest_content_file_count": len(value["files"]),
                  "release_loader_dependencies_equal_live": True,
                  "snapshot": loaded["snapshot"], "release_files_exported_with_this_report": False,
                  "relative_directory": str(Path(value["path"]).relative_to(workspace))}
        releases[label] = result
        return public(result)

    source_before, old_snapshot_before = observe(source), observe(old_snapshot)
    _require(source_before["access"]["source_fenced"] is True
             and old_snapshot_before["access"]["quarantined"] is True,
             "the source or old copy lost its previous boundary")
    stage("fenced-source-and-old-copy",
          "The current source is stopped; its earlier snapshot remains quarantined. A new destination has not been created.",
          {"source": source_before["status"], "source_access": source_before["access"],
           "old_snapshot_access": old_snapshot_before["access"]})

    rejected("old-source-secret-cannot-bootstrap-new-administrator", lambda: source_transfer.transfer_source(
        source_database, private / "rejected-old-administrator", credential=administrator,
        new_administrator_id=NEW_ADMINISTRATOR, new_credential=old_code_author,
        operation_id="transfer-rejected-old-secret"), "source_transfer_credential_reused", source,
        category="credential")
    stage("old-secret-rejected-before-transfer",
          "A credential already issued in the source cannot become the new administrator's credential.",
          rejections[-1])

    created = cli("create-fresh-destination", "create")
    transfer_results["created"] = created
    receipt = created["receipt"]
    _require(created["replayed"] is False and created["resumed"] is False,
             "the first transfer unexpectedly replayed or resumed")
    _require(all(created["destination"][key] is True for key in (
        "governance_enabled", "authorship_enabled", "content_enabled", "continuity_enabled")),
        "the destination did not retain every enabled source protection mode")
    destination, history = Store(destination_database), Store(history_database)
    imported, history_initial = observe(destination), observe(history)
    source_after_transfer = observe(source)
    _require(imported["state"]["revision"] == source_before["state"]["revision"]
             and imported["state"]["artifacts"] == source_before["state"]["artifacts"]
             and imported["state"]["dependencies"] == source_before["state"]["dependencies"],
             "the new baseline lost accepted content or its original dependencies")
    _require(imported["access"]["domain_writes_allowed"] is True
             and imported["access"]["imported_workspace"] is True
             and imported["tasks"] == []
             and imported["status"]["proposals"] == {}
             and len(imported["governance"]["grants"]) == 1
             and imported["governance"]["decisions"] == []
             and imported["governance"]["receipts"] == [],
             "old runtime authority or jobs were carried into the active destination")
    _require(source_after_transfer["events"][:-1] == source_before["events"]
             and source_after_transfer["events"][-1]["seq"] == receipt["transfer_event_seq"]
             and source_after_transfer["events"][-1]["data"] == receipt
             and history_initial["events"] == source_after_transfer["events"]
             and history_initial["access"]["source_fenced"] is True,
             "the source reservation or fenced history archive differs from its receipt")
    initial_release = publish("imported", destination)
    stage("fresh-active-baseline-and-fenced-history",
          "The new workspace imports accepted content and exact dependency records at revision R, starts with only a fresh administrator, and preserves old history separately.",
          {"transfer": created, "destination_status": imported["status"], "destination_access": imported["access"],
           "dependencies": imported["state"]["dependencies"], "history_status": history_initial["status"],
           "history_access": history_initial["access"], "initial_release": initial_release})

    status_initial = cli("completed-transfer-status", "status")
    transfer_results["status_initial"] = status_initial
    before_replay = observe(destination)
    replayed = cli("exact-transfer-replay", "create")
    transfer_results["replayed"] = replayed
    _require(replayed["replayed"] is True and replayed["receipt"] == receipt
             and observations_match(before_replay, observe(destination))
             and observations_match(source_after_transfer, observe(source)),
             "the completed replay changed the source or destination")
    rejected("another-destination-conflicts", lambda: source_transfer.transfer_source(
        source_database, private / "another-destination", credential=administrator,
        new_administrator_id=NEW_ADMINISTRATOR, new_credential=new_administrator,
        operation_id=operation_id), "source_transfer_conflict", source, category="reservation")
    rejected("another-operation-conflicts", lambda: source_transfer.transfer_source(
        source_database, destination_directory, credential=administrator,
        new_administrator_id=NEW_ADMINISTRATOR, new_credential=new_administrator,
        operation_id="another-source-transfer-operation"), "source_transfer_conflict", source,
        category="reservation")
    stage("completed-replay-and-single-target-binding",
          "The exact request returns its original fact without new events; another destination or operation conflicts.",
          {"status": status_initial, "replayed": replayed, "conflicts": rejections[-2:]})

    rejected("old-administrator-not-authorized-in-destination", lambda: destination.governance_grant(
        "transfer-rejected-role", "code_author", [CODE_A], time.time() + 3600,
        credential=administrator, new_credential=old_code_author,
        operation_id="transfer-rejected-old-admin"), "governance_unauthorized", destination,
        category="credential")
    rejected("old-author-not-authorized-in-destination", lambda: destination.claim_task(
        OLD_CODE_AUTHOR, ["code_author"], credential=old_code_author),
        "governance_unauthorized", destination, category="credential")
    rejected("fresh-grant-cannot-reuse-old-source-secret", lambda: destination.governance_grant(
        "transfer-rejected-secret-reuse", "code_author", [CODE_A], time.time() + 3600,
        credential=new_administrator, new_credential=old_code_author,
        operation_id="transfer-rejected-secret-reuse"), "governance_credential_conflict", destination,
        category="credential")
    old_acceptance = json.loads((workspace / "acceptance.json").read_text())
    rejected("old-source-proposal-is-not-a-local-destination-proposal", lambda: destination.proposal(
        old_acceptance["proposal_id"]), "unknown_proposal", destination, category="history")
    rejected("source-remains-fenced", lambda: source.enqueue(
        "transfer-source-blocked-job", "code_author", {}), "store_source_fenced", source,
        category="source_boundary")
    rejected("history-archive-remains-fenced", lambda: history.enqueue(
        "transfer-history-blocked-job", "code_author", {}), "store_source_fenced", history,
        category="source_boundary")

    copied_directory = private / "copied-destination"
    copied_directory.mkdir(mode=0o700)
    copied_database = copied_directory / "state.sqlite3"
    copied_database.touch(mode=0o600, exist_ok=False)
    with closing(sqlite3.connect(destination_database.as_uri() + "?mode=ro", uri=True)) as original_connection:
        with closing(sqlite3.connect(copied_database)) as copied_connection:
            original_connection.backup(copied_connection)
            copied_connection.commit()
            copied_connection.execute("PRAGMA journal_mode=DELETE")
    copied_database.chmod(0o600)
    rejected("bare-active-database-copy-cannot-activate-elsewhere", lambda: Store(copied_database),
             "store_transfer_invalid", destination, category="path_binding")
    stage("old-authority-and-active-copy-rejected",
          "Old credentials and proposals do not acquire new authority; source and archive stay fenced, and a bare active database copied to another path is rejected.",
          {"rejections": [value for value in rejections if value["category"] in {
              "credential", "history", "source_boundary", "path_binding"}],
           "copy_mechanism": "SQLite Connection.backup into a new private file",
           "copied_database_activated": False})

    grants: dict[str, dict[str, Any]] = {}
    fresh_credentials: dict[str, str] = {}
    expires_at = time.time() + 3600

    def grant(label: str, principal: str, role: str, artifact_ids: list[str]) -> str:
        secret = new_credential("new-" + label)
        fresh_credentials[label] = secret
        grants[label] = destination.governance_grant(
            principal, role, artifact_ids, expires_at, credential=new_administrator,
            new_credential=secret, operation_id="transfer-grant-" + label)
        return secret

    document_author = grant("document-author", DOCUMENT_AUTHOR, "doc_author", [DOC_A, DOC_D])
    code_author = grant("code-author", CODE_AUTHOR, "code_author", [CODE_A, CODE_D])
    integrator = grant("integrator", INTEGRATOR, "integrator", [DOC_A, CODE_A])
    reviewer_a = grant("reviewer-a", REVIEWER_A, "reviewer", [DOC_A, CODE_A])
    reviewer_b = grant("reviewer-b", REVIEWER_B, "reviewer", [DOC_A, CODE_A])
    coordinator = grant("coordinator", COORDINATOR, "coordinator", [DOC_A, CODE_A])
    initial_snapshot = destination.snapshot()
    bodies = {"document-a": _document(DOC_A, 5), "code-a": _file(CODE_A, "code", "SAMPLE_LIMIT = 5\n"),
              "alternative-document": _document(DOC_A, 6)}
    for label, body in bodies.items():
        destination.enqueue("transfer-author-" + label, "doc_author" if body["kind"] == "document" else "code_author", {
            "proposal": _proposal(initial_snapshot, body,
                                  "Fresh destination work; the fictional Python configuration is proposed data only.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        scheduled = list(bodies)[index]
        role = "doc_author" if bodies[scheduled]["kind"] == "document" else "code_author"
        principal, secret = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author" else (CODE_AUTHOR, code_author))
        lease = destination.claim_task(principal, [role], lease_seconds=300, credential=secret)
        _require(lease is not None, "a fresh authenticated author lease was unavailable")
        result = destination.submit(lease["task_id"], principal, lease["token"],
                                    lease["payload"]["proposal"], credential=secret)
        return {"label": lease["task_id"].removeprefix("transfer-author-"), "principal_id": principal,
                "thread": current_thread().name, "task_id": lease["task_id"], "fencing_token": lease["token"], **result}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="transfer-author") as executor:
        futures = [executor.submit(author_job, index) for index in range(len(bodies))]
        authors = {value["label"]: value for value in (future.result() for future in futures)}
    destination.enqueue("transfer-compose-a", "integrator", {
        "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                       for label in ("document-a", "code-a")],
        "rationale": "Compose only fresh destination authors and preserve their authenticated lineage.",
    })
    composition_lease = destination.claim_task(INTEGRATOR, ["integrator"], lease_seconds=300, credential=integrator)
    _require(composition_lease is not None, "the new integrator lease was unavailable")
    composition = destination.compose(composition_lease["task_id"], INTEGRATOR,
                                      composition_lease["token"], credential=integrator)
    main_id = composition["proposal_id"]
    formal_review = destination.review(main_id, credential=reviewer_a)
    _require(formal_review["approved"] is True, "the fresh fictional candidate failed formal review")
    stage("fresh-authenticated-authors-and-composition",
          "New role grants authorize three new author jobs and one composition. No old lease or opinion is reused.",
          {"grants": grants, "authors": authors, "composition": composition, "formal_review": formal_review})

    generation = destination.governance_status()["policy_generation"]
    decision_history: list[dict[str, Any]] = []
    decision_requests: list[dict[str, Any]] = []
    packets: dict[str, dict[str, Any]] = {}
    candidate_packet = destination.content_status(main_id)["packet"]
    packets[candidate_packet["packet_id"]] = candidate_packet
    for suffix, secret in (("a", reviewer_a), ("b", reviewer_b)):
        for index, identifier in enumerate((DOC_A, CODE_A), 1):
            label = suffix + "-" + str(index)
            request = {"proposal_id": main_id, "artifact_id": identifier, "verdict": "approve",
                       "rationale": "Programmed fresh destination decision; independent human or model review is not established.",
                       "expected_proposal_digest": composition["digest"], "expected_policy_generation": generation,
                       "expected_previous_decision": None,
                       "expected_content_packet_digest": candidate_packet["packet_digest"],
                       "operation_id": "transfer-decision-" + label}
            result = destination.governance_decide(**request, credential=secret)
            decision_requests.append({"label": label, "request": request})
            decision_history.append({"label": label, "result": result})
    observation = destination.governance_observe(main_id, credential=coordinator,
                                                  expected_policy_generation=generation)
    packets[observation["content"]["packet_id"]] = observation["content"]
    _require(observation["eligible"] is True, "fresh current opinions did not satisfy the destination's policy")
    acceptance = destination.accept(main_id, COORDINATOR, credential=coordinator,
                                    expected_policy_generation=generation)
    _require(acceptance["accepted"] is True
             and acceptance["revision"] == receipt["source_revision"] + 1,
             "the fresh destination work was not accepted at the next revision")
    stage("fresh-opinions-and-new-acceptance",
          "Four new scoped opinions and a new coordinator authorize a new acceptance in the destination, advancing R to R+1.",
          {"packet": candidate_packet, "decisions": decision_history, "observation": observation,
           "acceptance": acceptance})
    final_release = publish("updated", destination)
    stage("updated-release-retains-provenance",
          "The updated destination release loads with complete dependency records; unchanged imported artifacts still use their original baseline.",
          final_release)

    backup_directory, restore_directory = private / "newdest-backup", private / "newdest-restore"
    backed_up = safe_backup_summary(destination.create_backup(backup_directory, credential=new_administrator))
    restored_summary = safe_backup_summary(store_backup.restore_bundle(backup_directory, restore_directory))
    restored = Store(restore_directory / "state.sqlite3")
    restored_observation = observe(restored)
    destination_final = observe(destination)
    _require(restored_observation["state"] == destination_final["state"]
             and restored_observation["governance"]["receipts"] == destination_final["governance"]["receipts"]
             and restored_observation["access"]["quarantined"] is True
             and restored_observation["access"]["domain_writes_allowed"] is False,
             "the destination backup did not preserve its imported baseline and new accepted receipt")
    rejected("restored-new-workspace-stays-quarantined", lambda: restored.enqueue(
        "transfer-restored-blocked-job", "code_author", {}), "store_snapshot_quarantined", restored,
        category="snapshot_boundary")
    stage("new-workspace-backup-and-historical-restore",
          "An ordinary backup and restore preserve imported provenance and the new receipt; quarantine takes priority over the live destination's path binding.",
          {"backup": backed_up, "restored": restored_summary, "restored_status": restored_observation["status"],
           "restored_access": restored_observation["access"], "restored_state": restored_observation["state"],
           "restored_receipts": restored_observation["governance"]["receipts"]})

    status_final = cli("transfer-status-after-new-work", "status")
    transfer_results["status_final"] = status_final
    _require(status_final["receipt"] == receipt
             and status_final["destination"]["revision"] == receipt["source_revision"],
             "the historical transfer fact changed after new destination work")
    source_final, history_final, old_snapshot_final = observe(source), observe(history), observe(old_snapshot)
    _require(observations_match(source_after_transfer, source_final)
             and observations_match(history_initial, history_final)
             and observations_match(old_snapshot_before, old_snapshot_final),
             "new destination work changed the source, archive or old snapshot")
    stage("old-source-remains-stopped-and-history-preserved",
          "New work and its backup leave the old source, full history archive and older snapshot unchanged; the completed transfer remains one historical fact.",
          {"source_status": source_final["status"], "history_status": history_final["status"],
           "old_snapshot_status": old_snapshot_final["status"], "completed_transfer_status": status_final})

    data_artifacts = {
        "transfer-results.json": transfer_results,
        "transfer-cli-outcomes.json": cli_outcomes,
        "transfer-rejections.json": rejections,
        "transfer-source-observations.json": {"before": source_before, "after_transfer": source_after_transfer, "final": source_final},
        "transfer-destination-observations.json": {"imported": imported, "final": destination_final},
        "transfer-history-observations.json": {"initial": history_initial, "final": history_final},
        "transfer-old-snapshot-observations.json": {"before": old_snapshot_before, "after": old_snapshot_final},
        "transfer-dependencies.json": {"source": source_before["state"]["dependencies"],
                                       "imported": imported["state"]["dependencies"],
                                       "updated": destination_final["state"]["dependencies"]},
        "transfer-releases.json": releases,
        "transfer-grants.json": grants,
        "transfer-authors.json": authors,
        "transfer-composition.json": {"composition": composition, "formal_review": formal_review},
        "transfer-decision-requests.json": decision_requests,
        "transfer-decisions.json": decision_history,
        "transfer-packets.json": packets,
        "transfer-observation.json": observation,
        "transfer-acceptance.json": acceptance,
        "transfer-content-policy-history.json": [destination.content_status()["current_policy"]],
        "transfer-governance-policy-history.json": destination_final["governance"]["policy_history"],
        "transfer-new-backup.json": {"backup": backed_up, "restore_summary": restored_summary,
                                     "restored": restored_observation},
    }
    for name, value in data_artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": "source-transfer-1",
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Fresh active workspace from one fenced source, preserved provenance and new authority",
        "summary": {
            "baseline_public_file_count": len(baseline_public_files), "transfer_stage_count": len(stages),
            "transfer_cli_child_process_count": len(cli_outcomes),
            "total_cli_child_process_count": baseline_report["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "source_events_before_transfer": source_before["status"]["events"],
            "source_events_after_transfer": source_final["status"]["events"],
            "transfer_reservation_event_count": len(source_final["events"]) - len(source_before["events"]),
            "imported_revision": imported["state"]["revision"],
            "destination_revision_after_new_acceptance": destination_final["state"]["revision"],
            "destination_events_after_new_acceptance": destination_final["status"]["events"],
            "historical_archive_event_count": history_final["status"]["events"],
            "old_snapshot_event_count": old_snapshot_final["status"]["events"],
            "fresh_author_job_count": len(authors),
            "fresh_author_pool_workers": workers,
            "fresh_observed_author_thread_count": len({value["thread"] for value in authors.values()}),
            "fresh_role_grant_count": len(grants), "fresh_decision_count": len(decision_history),
            "operator_rejection_count": len(rejections), "exact_transfer_replay_count": 1,
            "baseline_dependencies_preserved": True, "imported_release_loaded": True,
            "updated_release_loaded": True, "fresh_acceptance_completed": True,
            "new_workspace_backup_restored": True, "restored_new_workspace_quarantined": True,
            "copied_active_database_rejected": True, "old_source_remains_fenced": True,
            "old_authority_transferred": False, "old_secrets_reissued": False,
            "private_origin_contents_read_by_demo": False, "private_manifest_contents_read_by_demo": False,
            "private_fingerprints_exported": False, "older_snapshot_activated": False,
            "incomplete_import_or_crash_simulated": False, "external_fencing_established": False,
            "external_llm_used": False, "human_review_recorded": False,
            "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(data_artifacts),
        "limits": [
            "The source-side single-target reservation and destination path binding are cooperative local controls, not an external fencing service.",
            "The active destination contains accepted content and preserved dependency descriptors; old runtime grants, leases, tasks, opinions and receipts remain in the separate fenced archive.",
            "All new decisions are programmed local opinions under fresh grants; they do not establish independent people, factual truth or semantic equivalence.",
            "Release loader compatibility and exact baseline dependencies preserve the existing declared freshness checks; a separately induced stale imported read/query case is not performed here.",
            "Bare destination copying is demonstrated through SQLite backup into a new private file; it is not a corrupted SQL fixture or simulated process crash.",
            "Public observations omit only governance_status's changing top-level observed_at for equality; raw timestamps and addressed proof bodies remain intact.",
            "The new destination's ordinary backup stays a quarantined historical snapshot. It does not reactivate a live copied database or any old source authority.",
            "The destination's ordinary database backup does not embed its separate full ancestral history.sqlite3 archive; that archive remains a separate private transfer artifact.",
            "Old software and a trusted database owner using raw SQL can bypass the cooperative path. Existing external publication effects are not revoked or replayed by the transfer.",
        ],
    }
    write_json("transfer-report.json", report)
    lines = [
        "# Выполненный перенос в новую рабочую среду", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала выполнен [сценарий остановки источника](fence-report.md). Его публичные основания сохранены",
        "без перезаписи. Остановленный источник получает одноразовую привязку к новому назначению; старый",
        "snapshot сохраняет карантин. Содержимое частных origin, manifest и credential denylist не читается демонстратором.", "",
        "## Последовательность", "", "| Этап | Наблюдение | Событий источника |", "|---|---|---:|",
    ]
    lines += [f"| [{value['label']}]({value['artifact']}) | {value['explanation']} | {value['source_event_count']} |" for value in stages]
    lines += [
        "", "## Новая база и сохранённая история", "",
        "Назначение начинает работу с принятым содержанием и исходными пятью полями dependency records.",
        "Импортная ревизия R равна текущей ревизии источника; она не сбрасывается в 1. Обычный publish и",
        "load_release подтверждают совместимость этих оснований с существующим форматом выпуска.", "",
        "Старые grants, tasks, proposals, decisions и receipts отсутствуют в активных таблицах назначения.",
        "Текущие защитные режимы и политики включены заново с новым администратором. История полностью",
        "сохранена в отдельной fenced SQLite-копии и остаётся доступной для чтения.", "",
        "## Новые полномочия и новая работа", "",
        "Старые credentials не получают полномочия в назначении; повторная выдача старого секрета также",
        "отклоняется. Новые author/integrator/reviewer/coordinator grants разрешают три авторских задания,",
        "композицию документа и Python-конфигурации, четыре свежих решения и новое принятие R → R+1.",
        "Предлагаемый Python не исполняется; программируемые роли не являются независимой человеческой экспертизой.", "",
        "## Повторы, копии и последующее восстановление", "",
        "Точный повтор transfer возвращает первоначальную квитанцию без нового события. Другая операция",
        "или назначение конфликтуют. Копирование активной DB в другой частный путь через SQLite Backup API",
        "не активирует вторую среду: Store отклоняет её из-за привязки origin к первоначальному пути.", "",
        "После нового принятия выполняются обычный backup назначения и restore. Историческая копия",
        "сохраняет полный snapshot, зависимость импортированных неизменённых файлов и новую квитанцию,",
        "но остаётся в карантине. Quarantine имеет приоритет над привязкой активного пути.", "",
        "Этот обычный backup базы назначения не включает отдельный history.sqlite3 со всей предшествующей",
        "историей. Полный архив переноса сохраняется отдельно и должен храниться как отдельный частный артефакт.", "",
        "## Публичные основания", "",
        "- [Transfer и status](transfer-results.json), [CLI](transfer-cli-outcomes.json), [отказы](transfer-rejections.json).",
        "- [Источник](transfer-source-observations.json), [назначение](transfer-destination-observations.json), [история](transfer-history-observations.json).",
        "- [Зависимости](transfer-dependencies.json), [проверенные выпуски](transfer-releases.json), [прежняя копия](transfer-old-snapshot-observations.json).",
        "- [Новые grants](transfer-grants.json), [авторы](transfer-authors.json), [композиция](transfer-composition.json).",
        "- [Пакеты](transfer-packets.json), [решения](transfer-decisions.json), [наблюдение](transfer-observation.json), [принятие](transfer-acceptance.json).",
        "- [История content-политики](transfer-content-policy-history.json), [governance](transfer-governance-policy-history.json), [новый backup/restore](transfer-new-backup.json).",
        "- [Машинный отчёт](transfer-report.json).", "",
        "## Границы выполненного", "",
        f"Новых этапов: {len(stages)}; новых CLI-процессов: {len(cli_outcomes)}; ожидаемых отказов: {len(rejections)}.",
        f"События источника: {source_before['status']['events']} → {source_final['status']['events']}; новая ревизия назначения: {destination_final['state']['revision']}.",
        f"Авторских заданий: {len(authors)}; наблюдавшихся новых потоков: {report['summary']['fresh_observed_author_thread_count']}; свежих решений: {len(decision_history)}.",
        "Source reservation и path binding относятся к обновлённому локальному Store. Старый код и прямой",
        "SQL владельца остаются вне этой границы. Внешний распределённый fence не установлен; существующие",
        "эффекты публикации не отзываются и не выполняются повторно автоматически.", "",
        "Сверка публичных наблюдений исключает только верхний governance.observed_at, вычисляемый при",
        "каждом чтении. Raw timestamps, признаки expired и полные адресованные доказательства сохраняются.",
        "Отдельный stale imported read/query случай, авария процесса и SQL-повреждение не воспроизводились.", "",
        "Выпуски находятся в каталоге запуска и не включены в плоский набор этого отчёта. Частные базы,",
        "планы, manifest, fingerprints и credentials не публикуются. Измерений предельной нагрузки нет.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "transfer-report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
