"""Demonstrate recovery of lost CLI responses and a bounded audit catalogue.

The example discards stdout from completed local subprocesses. It does not
simulate a network failure, kill an in-flight writer or give audits authority
over acceptance. Proposed Python remains fictional stored data.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
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


DOCUMENT_AUTHOR = "recovery-document-author"
CODE_AUTHOR = "recovery-code-author"
INTEGRATOR = "recovery-integrator"
REVIEWER_A = "recovery-reviewer-a"
REVIEWER_B = "recovery-reviewer-b"
REVIEWER_C = "recovery-reviewer-c"
ADMINISTRATOR = "recovery-administrator"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("History recovery demonstration stopped: " + message)


def _advisory(result: dict[str, Any]) -> None:
    _require(result.get("advisory_only") is True
             and result.get("acceptance_authority") is False
             and result.get("authority_evaluated") is False,
             "a public audit response lost its explicit advisory boundary")


def run_history_recovery_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save one ordinary local workflow in a strictly new empty directory."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("History recovery demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    private = workspace / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    database = private / "state.sqlite3"
    database.touch(mode=0o600, exist_ok=False)
    database.chmod(0o600)
    repository = Path(__file__).resolve().parents[1]
    private_markers: list[str] = []

    def new_credential(label: str) -> str:
        secret = prepare_credential(private / (label + ".credential"))
        private_markers.extend((secret, hashlib.sha256(secret.encode("ascii")).hexdigest()))
        return secret

    def public(value: Any) -> Any:
        # Keep public proof bodies byte-for-byte equivalent after canonical
        # encoding. A secret is an error, rather than a reason to drop keys.
        if isinstance(value, dict):
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers),
                     "public data contains a credential or its private hash")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    store = Store(database)
    store.bootstrap([_document(DOC_A, 3), _document(DOC_D, 3),
                     _file(CODE_A, "code", "SAMPLE_LIMIT = 3\n"),
                     _file(CODE_D, "code", "SAMPLE_LIMIT = 3\n")])
    initial_snapshot = store.snapshot()
    editorial_policy = {"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True}
    active_catalog = _catalog()
    admin = new_credential("admin")
    enabled_governance = store.governance_enable(editorial_policy, ADMINISTRATOR, admin)
    generation = 1
    expires_at = time.time() + 3600
    all_artifacts = [DOC_A, CODE_A, DOC_D, CODE_D]
    grants: dict[str, dict[str, Any]] = {}

    def grant(label: str, principal: str, role: str, artifact_ids: list[str]) -> str:
        secret = new_credential(label)
        grants[label] = store.governance_grant(
            principal, role, artifact_ids, expires_at, credential=admin,
            new_credential=secret, operation_id="recovery-grant-" + label)
        return secret

    document_author = grant("document-author", DOCUMENT_AUTHOR, "doc_author", [DOC_A, DOC_D])
    code_author = grant("code-author", CODE_AUTHOR, "code_author", [CODE_A, CODE_D])
    integrator = grant("integrator", INTEGRATOR, "integrator", all_artifacts)
    reviewer_a = grant("reviewer-a", REVIEWER_A, "reviewer", all_artifacts)
    reviewer_b = grant("reviewer-b", REVIEWER_B, "reviewer", all_artifacts)
    reviewer_c = grant("reviewer-c", REVIEWER_C, "reviewer", all_artifacts)
    coordinator = grant("coordinator", "recovery-coordinator", "coordinator", all_artifacts)
    enabled_authorship = store.authorship_enable(credential=admin, expected_policy_generation=generation)
    generation = enabled_authorship["policy_generation"]
    enabled_content = store.content_enable(active_catalog, credential=admin,
                                           expected_policy_generation=generation)
    generation = enabled_content["policy_generation"]
    active_catalog = deepcopy(enabled_content["current_policy"]["policy"])
    content_policy_history = [deepcopy(enabled_content["current_policy"])]
    enabled_continuity = store.continuity_enable(credential=admin, expected_policy_generation=generation)
    generation = enabled_continuity["policy_generation"]
    operations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    read_only_observations: list[dict[str, Any]] = []
    catalogue_pages: dict[str, dict[str, Any]] = {}
    audit_results: dict[str, dict[str, Any]] = {}
    audit_requests: dict[str, dict[str, Any]] = {}
    recovery_results: dict[str, dict[str, Any]] = {}
    audit_headers: dict[str, dict[str, Any]] = {}
    checkpoints: dict[str, dict[str, Any]] = {}
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    decision_history: list[dict[str, Any]] = []
    decision_requests: dict[str, dict[str, Any]] = {}
    packets: dict[str, dict[str, Any]] = {}
    packet_labels: dict[str, str] = {}
    observations: dict[str, dict[str, Any]] = {}
    catalog_updates: list[dict[str, Any]] = []

    def cli(label: str, action: str, arguments: list[str], *, authenticated: bool = False,
            discard_stdout: bool = False) -> dict[str, Any] | None:
        command = [sys.executable, "-m", "agentwiki", "history-audit", action,
                   "--workspace", str(private)]
        if authenticated:
            command += ["--credential", str(private / "admin.credential")]
        command += arguments
        # This launches a separate process using the repository's ordinary CLI.
        # DEVNULL deliberately prevents this client from obtaining its result.
        with subprocess.Popen(command, cwd=repository, shell=False,
                              stdout=subprocess.DEVNULL if discard_stdout else subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=60)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate()
                raise RuntimeError("History recovery demonstration stopped: CLI child timed out") from error
            safe_stderr = stderr
            for marker in private_markers:
                safe_stderr = safe_stderr.replace(marker, "[private]")
            cli_outcomes.append({
                "label": label, "module": "agentwiki", "command": "history-audit", "action": action,
                "arguments": arguments, "workspace": "private", "credential_file": "private/admin.credential" if authenticated else None,
                "shell": False, "working_directory": "repository checkout", "child_process_id": process.pid,
                "returncode": process.returncode, "stdout_discarded": discard_stdout,
                "stdout_captured": not discard_stdout, "stderr": safe_stderr,
                "completed_child_process": True, "network_failure_simulated": False, "process_crash_simulated": False,
            })
            _require(process.returncode == 0, "CLI " + action + " exited with " + str(process.returncode))
        if discard_stdout:
            _require(stdout is None, "the intentionally discarded response was unexpectedly captured")
            return None
        try:
            result = json.loads(stdout)
        except (json.JSONDecodeError, TypeError) as error:
            raise RuntimeError("History recovery demonstration stopped: CLI did not return one JSON object") from error
        _require(isinstance(result, dict), "CLI returned a non-object result")
        return public(result)

    def read_only(label: str, operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        before = len(store.events())
        result = operation()
        after = len(store.events())
        _require(after == before, "a recovery or catalogue read created an event: " + label)
        read_only_observations.append({"label": label, "events_before": before, "events_after": after,
                                       "event_count_unchanged": True})
        return result

    def remember_audit(label: str, result: dict[str, Any]) -> dict[str, Any]:
        _advisory(result)
        root, checkpoint = result["audit"], result["checkpoint"]
        audit_id, checkpoint_id = root["audit_id"], checkpoint["checkpoint_id"]
        if audit_id in audit_headers:
            _require(canonical(audit_headers[audit_id]) == canonical(root), "an immutable audit header changed")
        if checkpoint_id in checkpoints:
            _require(canonical(checkpoints[checkpoint_id]) == canonical(checkpoint), "an immutable checkpoint changed")
        audit_headers[audit_id], checkpoints[checkpoint_id] = root, checkpoint
        audit_results[label] = result
        return result

    def recover(label: str, operation_id: str) -> dict[str, Any]:
        result = read_only(label, lambda: cli(label, "recover", ["--operation", operation_id], authenticated=True))
        remember_audit(label, result)
        _require(result["replayed"] is True, "recover did not return an existing operation replay")
        recovery_results[label] = result
        return result

    def catalogue(label: str, *, after_seq: int = 0, snapshot_max_seq: int | None = None,
                  limit: int = 10) -> dict[str, Any]:
        arguments = ["--after-seq", str(after_seq), "--limit", str(limit)]
        if snapshot_max_seq is not None:
            arguments += ["--snapshot-max-seq", str(snapshot_max_seq)]
        result = read_only(label, lambda: cli(label, "list", arguments))
        _advisory(result)
        _require(result["errors"] == [] and result["scanned_count"] == result["returned_count"] == len(result["entries"]),
                 "an ordinary valid catalogue unexpectedly contained invalid records")
        catalogue_pages[label] = result
        return result

    def expected_exception(label: str, operation: Callable[[], Any], code: str) -> dict[str, Any]:
        before = len(store.events())
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == code, "unexpected rejection for " + label + ": " + error.code)
            _require(len(store.events()) == before, "a rejected operation created an event")
            result = {"label": label, "rejected": True, "code": error.code, "event_count_unchanged": True}
            operations.append(result)
            return result
        raise RuntimeError("History recovery demonstration stopped: expected rejection for " + label)

    initial_empty = catalogue("initial-empty-catalogue", limit=1)
    _require(initial_empty["snapshot_max_seq"] == 0 and not initial_empty["entries"]
             and initial_empty["next_cursor"] is None, "the initial catalogue did not retain an empty ceiling of zero")
    bodies = {"document-a": _document(DOC_A, 4),
              "code-a": _file(CODE_A, "code", "SAMPLE_LIMIT = 4\n"),
              "alternative-document": _document(DOC_A, 5)}
    for label, body in bodies.items():
        store.enqueue("recovery-author-" + label, "doc_author" if body["kind"] == "document" else "code_author", {
            "proposal": _proposal(initial_snapshot, body,
                                  "Fictional response-recovery example; proposed Python is data only.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        scheduled = list(bodies)[index]
        role = "doc_author" if bodies[scheduled]["kind"] == "document" else "code_author"
        principal, secret = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author"
                             else (CODE_AUTHOR, code_author))
        task = store.claim_task(principal, roles=[role], credential=secret, lease_seconds=300)
        _require(task is not None, "an authenticated author lease was unavailable")
        submitted = store.submit(task["task_id"], principal, task["token"],
                                 task["payload"]["proposal"], credential=secret)
        return {"label": task["task_id"].removeprefix("recovery-author-"), "principal_id": principal,
                "thread": current_thread().name, "task_id": task["task_id"],
                "fencing_token": task["token"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="recovery-author") as executor:
        futures = [executor.submit(author_job, index) for index in range(len(bodies))]
        authors = {result["label"]: result for result in (future.result() for future in futures)}
    store.enqueue("recovery-compose-a", "integrator", {
        "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                       for label in ("document-a", "code-a")],
        "rationale": "Compose the exact fictional document and code candidate for operation recovery.",
    })
    lease = store.claim_task(INTEGRATOR, roles=["integrator"], credential=integrator, lease_seconds=300)
    _require(lease is not None, "the integrator lease was unavailable")
    main = store.compose(lease["task_id"], INTEGRATOR, lease["token"], credential=integrator)
    main_id = main["proposal_id"]
    formal_review = store.review(main_id, credential=reviewer_a)
    _require(formal_review["approved"], "formal review did not approve the fictional candidate")

    def remember_packet(label: str, result: dict[str, Any]) -> dict[str, Any]:
        identifier = result["packet_id"]
        if identifier in packets:
            _require(canonical(packets[identifier]) == canonical(result), "one packet ID names different bodies")
        packets[identifier], packet_labels[label] = result, identifier
        return result

    def packet(label: str) -> dict[str, Any]:
        return remember_packet(label, store.content_status(main_id)["packet"])

    def decide(label: str, secret: str, principal: str, identifier: str,
               verdict: str = "approve") -> dict[str, Any]:
        current_packet = packet("decision-" + label)
        previous = decisions.get((principal, identifier))
        request = {
            "proposal_id": main_id, "artifact_id": identifier, "verdict": verdict,
            "rationale": "Programmed local decision for " + label + "; no independent human or model review is claimed.",
            "expected_proposal_digest": main["digest"], "expected_policy_generation": generation,
            "expected_previous_decision": previous["decision_id"] if previous else None,
            "expected_content_packet_digest": current_packet["packet_digest"],
            "operation_id": "recovery-decision-" + label,
        }
        result = store.governance_decide(**request, credential=secret)
        decisions[(principal, identifier)] = result
        decision_requests[label] = request
        decision_history.append({"label": label, "result": result})
        return result

    def observe(label: str) -> dict[str, Any]:
        result = store.governance_observe(main_id, credential=coordinator,
                                          expected_policy_generation=generation)
        remember_packet("observation-" + label, result["content"])
        observations[label] = result
        return result

    def stage(label: str, explanation: str, result: Any = None) -> None:
        filename = f"stage-{len(stages) + 1:02d}-{label}.json"
        status = store.status()
        write_json(filename, {"label": label, "explanation": explanation, "runtime_status": status,
                              "governance_policy_generation": generation,
                              "content_policy_generation": content_policy_history[-1]["generation"],
                              "operation_result": result})
        stages.append({"label": label, "explanation": explanation, "artifact": filename,
                       "runtime_revision": status["revision"]})

    def update_catalog(label: str) -> dict[str, Any]:
        nonlocal generation, active_catalog
        changed = store.content_set_policy(_scope_edit(active_catalog, "section-d", label),
                                           credential=admin, expected_policy_generation=generation)
        generation = changed["policy_generation"]
        current = deepcopy(changed["current_policy"])
        active_catalog = current["policy"]
        content_policy_history.append(current)
        result = {"label": label, "governance_policy_generation": generation,
                  "content_policy_generation": current["generation"], "content_policy_digest": current["policy_digest"]}
        catalog_updates.append(result)
        return result

    def start_audit(label: str) -> dict[str, Any]:
        current_packet = packet("audit-target-" + label)
        request = {"decision_id": original_objection["decision_id"], "expected_policy_generation": generation,
                   "expected_target_packet_digest": current_packet["packet_digest"],
                   "operation_id": "recovery-audit-start-" + label}
        audit_requests[label + "-start"] = request
        result = remember_audit(label + "-start", store.continuity_audit_start(**request, credential=admin))
        _require(result["checkpoint"]["state"] == "pending", "a three-edge audit was not pending at start")
        return result

    stage("authenticated-candidate", "Three local author jobs and one composition prepare the candidate; the initial catalogue pins an empty ceiling of zero.",
          {"authors": authors, "composition": main, "formal_review": formal_review, "empty_catalogue": initial_empty})
    original_approvals = [decide("initial-" + suffix + "-" + str(index), secret, principal, identifier)["decision_id"]
                          for suffix, secret, principal in (("a", reviewer_a, REVIEWER_A), ("b", reviewer_b, REVIEWER_B))
                          for index, identifier in enumerate((DOC_A, CODE_A), 1)]
    original_objection = decide("original-c-objection", reviewer_c, REVIEWER_C, DOC_A, "request_changes")
    initial_observation = observe("initial-veto")
    _require(not initial_observation["eligible"]
             and any(reason["code"] == "governance_changes_requested" for reason in initial_observation["reasons"]),
             "the initial explicit objection did not block acceptance")
    stage("initial-quorum-and-veto", "A/B approve both writes, while C requests changes; the catalogue and recovery do not replace review authority.",
          initial_observation)

    target_updates = [update_catalog("before-target-d-" + str(index + 1)) for index in range(3)]
    target_packet = packet("frozen-three-edge-target")
    target_generation = generation
    preserved_veto = observe("three-edges-preserve-veto")
    current_original = [result for result in preserved_veto["decisions"]
                        if result["decision_id"] in set(original_approvals + [original_objection["decision_id"]])]
    _require(len(current_original) == 5
             and all(result["valid"] and result["continuity_match"]["proof"]["mode"] == "preserved_catalog"
                     and len(result["continuity_match"]["proof"]["transitions"]) == len(target_updates)
                     for result in current_original),
             "the three unrelated catalog changes did not preserve the original votes and veto")
    stage("three-unrelated-catalog-transitions", "Three different D metadata updates preserve the full selected A basis, including C's active veto.",
          {"updates": target_updates, "observation": preserved_veto})

    main_start_operation = "recovery-main-start"
    main_start_request = {"decision_id": original_objection["decision_id"], "expected_policy_generation": generation,
                          "expected_target_packet_digest": target_packet["packet_digest"],
                          "operation_id": main_start_operation}
    audit_requests["main-start-with-discarded-response"] = main_start_request
    cli("discarded-start-response", "start", [
        "--decision", original_objection["decision_id"], "--expected-policy-generation", str(generation),
        "--expected-target-packet-digest", target_packet["packet_digest"], "--operation", main_start_operation,
    ], authenticated=True, discard_stdout=True)
    stage("completed-start-response-discarded", "An independent CLI process commits start and exits zero; this client deliberately discards stdout and has no audit ID from that response.",
          cli_outcomes[-1])
    recovered_start = recover("recovered-start-response", main_start_operation)
    main_audit_id = recovered_start["audit"]["audit_id"]
    initial_checkpoint = recovered_start["checkpoint"]["checkpoint_id"]
    _require(recovered_start["checkpoint"]["index"] == 0
             and recovered_start["checkpoint"]["state"] == "pending"
             and recovered_start["current_checkpoint_id"] == initial_checkpoint,
             "recovery did not locate the exact initial checkpoint by operation ID")
    stage("start-recovered-by-operation", "A new CLI process uses only the known start operation ID to recover the immutable initial checkpoint without new events.",
          recovered_start)

    main_step_operation = "recovery-main-first-advance"
    main_step_request = {"audit_id": main_audit_id, "expected_checkpoint_id": initial_checkpoint,
                         "limit": 1, "operation_id": main_step_operation}
    audit_requests["main-advance-with-discarded-response"] = main_step_request
    cli("discarded-advance-response", "advance", [
        "--audit", main_audit_id, "--expected-checkpoint", initial_checkpoint,
        "--limit", "1", "--operation", main_step_operation,
    ], authenticated=True, discard_stdout=True)
    stage("completed-advance-response-discarded", "A separate CLI process commits one transition and exits zero; its checkpoint response is also discarded.", cli_outcomes[-1])
    recovered_step = recover("recovered-first-advance-response", main_step_operation)
    first_checkpoint = recovered_step["checkpoint"]["checkpoint_id"]
    _require(recovered_step["checkpoint"]["index"] == 1
             and recovered_step["checkpoint"]["state"] == "pending"
             and recovered_step["checkpoint"]["checked_transition_count"] == 1
             and recovered_step["checkpoint"]["total_checked_transition_count"] == 1
             and recovered_step["current_checkpoint_id"] == first_checkpoint,
             "advance recovery did not return the stored first one-edge chunk")
    stage("advance-recovered-by-operation", "Recovery retrieves the exact stored first chunk, rather than repeating its write or requiring the lost checkpoint ID.", recovered_step)

    completed_start = start_audit("completed-secondary")
    completed_request = {"audit_id": completed_start["audit"]["audit_id"],
                         "expected_checkpoint_id": completed_start["checkpoint"]["checkpoint_id"],
                         "limit": len(target_updates), "operation_id": "recovery-secondary-complete"}
    audit_requests["completed-secondary-advance"] = completed_request
    secondary_complete = remember_audit("completed-secondary-advance", store.continuity_audit_advance(
        **completed_request, credential=admin))
    pending_start = start_audit("pending-secondary")
    _require(secondary_complete["checkpoint"]["state"] == "history_complete"
             and secondary_complete["checkpoint"]["total_checked_transition_count"] == len(target_updates),
             "the second audit did not complete its three-edge target")
    original_root_ids = [main_audit_id, completed_start["audit"]["audit_id"], pending_start["audit"]["audit_id"]]
    stage("three-original-audits", "There are three start events: main is pending after one edge, another audit is complete, and the third remains pending.",
          {"original_audit_ids": original_root_ids, "secondary_complete": secondary_complete, "secondary_pending": pending_start})

    first_page = catalogue("pinned-first-page-before-head-change", limit=1)
    ceiling = first_page["snapshot_max_seq"]
    _require(len(first_page["entries"]) == 1 and first_page["entries"][0]["audit_id"] == main_audit_id
             and first_page["entries"][0]["observed_head"]["checkpoint_id"] == first_checkpoint
             and first_page["entries"][0]["observed_head"]["state"] == "pending"
             and first_page["next_cursor"] is not None,
             "the first catalogue page did not show the pending main head")
    stage("catalogue-membership-pinned", "The first public CLI list page fixes snapshot_max_seq for the three existing roots and observes main's pending head.", first_page)

    late_start = start_audit("late-outside-ceiling")
    late_audit_id = late_start["audit"]["audit_id"]
    main_complete_request = {"audit_id": main_audit_id, "expected_checkpoint_id": first_checkpoint,
                             "limit": 2, "operation_id": "recovery-main-complete"}
    audit_requests["main-complete"] = main_complete_request
    main_complete = remember_audit("main-complete", cli("main-complete", "advance", [
        "--audit", main_audit_id, "--expected-checkpoint", first_checkpoint,
        "--limit", "2", "--operation", main_complete_request["operation_id"],
    ], authenticated=True))
    main_completed_checkpoint = main_complete["checkpoint"]["checkpoint_id"]
    _require(main_complete["checkpoint"]["state"] == "history_complete"
             and main_complete["checkpoint"]["total_checked_transition_count"] == len(target_updates),
             "the main audit did not complete its frozen three-edge target")
    stage("later-root-and-current-head-change", "A fourth root starts outside the saved ceiling. Main advances the remaining two edges and reaches history_complete.",
          {"late_audit": late_start, "main_complete": main_complete})

    repeated_page = catalogue("pinned-first-page-after-head-change", snapshot_max_seq=ceiling, limit=1)
    _require([entry["audit_id"] for entry in repeated_page["entries"]]
             == [entry["audit_id"] for entry in first_page["entries"]]
             and repeated_page["entries"][0]["observed_head"]["checkpoint_id"] == main_completed_checkpoint
             and repeated_page["entries"][0]["observed_head"]["state"] == "history_complete"
             and repeated_page["next_cursor"] == first_page["next_cursor"],
             "a pinned membership page failed to observe the later current head")
    stage("same-membership-new-head-observation", "Repeating the same bounded page returns the same root ID and a newer head; snapshot_max_seq fixes membership rather than all heads together.",
          {"before": first_page, "after": repeated_page})

    pinned_pages = [repeated_page]
    cursor = repeated_page["next_cursor"]
    while cursor is not None:
        next_page = catalogue("pinned-page-" + str(len(pinned_pages) + 1),
                              after_seq=cursor["after_seq"], snapshot_max_seq=cursor["snapshot_max_seq"], limit=1)
        pinned_pages.append(next_page)
        _require(len(pinned_pages) <= len(original_root_ids), "pinned catalogue pagination did not terminate")
        cursor = next_page["next_cursor"]
    pinned_entries = [entry for page in pinned_pages for entry in page["entries"]]
    pinned_ids = [entry["audit_id"] for entry in pinned_entries]
    _require(pinned_ids == original_root_ids and len(set(pinned_ids)) == len(original_root_ids)
             and late_audit_id not in pinned_ids
             and all(page["snapshot_max_seq"] == ceiling for page in pinned_pages),
             "pinned one-entry pages lost, repeated or admitted a later root")
    stage("pinned-pagination-exhausted", "Three one-entry pages enumerate each original root exactly once, exclude the late root and terminate without a cursor.",
          {"pinned_ids": pinned_ids, "page_count": len(pinned_pages), "snapshot_max_seq": ceiling})

    new_scan = catalogue("new-unpinned-scan")
    fresh_ids = [entry["audit_id"] for entry in new_scan["entries"]]
    repeated_empty = catalogue("empty-ceiling-zero-after-starts", snapshot_max_seq=initial_empty["snapshot_max_seq"], limit=1)
    _require(fresh_ids == original_root_ids + [late_audit_id] and new_scan["next_cursor"] is None
             and new_scan["snapshot_max_seq"] > ceiling
             and repeated_empty["entries"] == [] and repeated_empty["scanned_count"] == 0
             and repeated_empty["snapshot_max_seq"] == 0 and repeated_empty["next_cursor"] is None,
             "a new scan or the previously empty ceiling changed its membership contract")
    stage("new-scan-and-empty-snapshot", "A new catalogue scan sees all four roots. The earlier ceiling zero still describes an empty set after new starts.",
          {"new_scan": new_scan, "repeated_empty": repeated_empty})

    expected_exception("recover-unknown-operation", lambda: store.continuity_audit_recover(
        "recovery-operation-never-executed", credential=admin), "audit_operation_not_found")
    expected_exception("recover-without-administrator", lambda: store.continuity_audit_recover(
        main_start_operation, credential=None), "governance_unauthorized")
    expected_exception("catalogue-future-ceiling", lambda: store.continuity_audit_catalogue(
        snapshot_max_seq=new_scan["snapshot_max_seq"] + 1), "audit_invalid_input")
    expected_exception("catalogue-after-without-ceiling", lambda: store.continuity_audit_catalogue(
        after_seq=1), "audit_invalid_input")
    expected_exception("catalogue-limit-over-20", lambda: store.continuity_audit_catalogue(
        limit=21), "audit_invalid_input")
    stage("rejected-recovery-and-cursor-inputs", "Unknown operations, missing administrative authority and three invalid catalogue arguments are rejected without events.", operations)

    before_abstain = observe("audits-do-not-clear-veto")
    _require(not before_abstain["eligible"]
             and any(reason["code"] == "governance_changes_requested" for reason in before_abstain["reasons"]),
             "recovery or catalogue reads cleared C's active objection")
    blocked = store.accept(main_id, credential=coordinator, expected_policy_generation=generation)
    _require(blocked["accepted"] is False
             and any(reason["code"] == "governance_changes_requested" for reason in blocked["reasons"]),
             "completed advisory history authorized acceptance despite the active objection")
    operations.append({"label": "veto-blocks-after-recovery-and-catalogue", "result": blocked})
    stage("advisory-results-preserve-veto", "Completed audits and recovered responses leave C's request_changes active; ordinary Store.accept is blocked.",
          {"observation": before_abstain, "acceptance": blocked})

    final_abstain = decide("explicit-current-c-abstain", reviewer_c, REVIEWER_C, DOC_A, "abstain")
    final_observation = observe("original-approvals-after-explicit-abstain")
    accepted_packet = packet("accepted-current")
    _require(final_observation["eligible"], "an explicit current abstain did not clear the real veto")
    accepted = store.accept(main_id, credential=coordinator, expected_policy_generation=generation)
    operations.append({"label": "accepted-after-explicit-abstain", "result": accepted})
    receipt = accepted["governance_receipt"]
    proofs = receipt["review_continuity"]["used_decisions"]
    _require(accepted["accepted"] is True and accepted["revision"] == 2 and receipt["schema_version"] == 4
             and set(proofs) == set(original_approvals)
             and all(proof["mode"] == "preserved_catalog" and len(proof["transitions"]) == len(target_updates)
                     for proof in proofs.values()),
             "acceptance did not retain the four original approvals through exactly the three catalog edges")
    stage("acceptance-retains-original-approvals", "Only C's explicit current abstain removes the veto. One receipt schema 4 retains the four original approval IDs through three verified catalog transitions.",
          {"abstain": final_abstain, "observation": final_observation, "acceptance": accepted})

    post_acceptance_update = update_catalog("after-acceptance-live-d-change")
    live_after_acceptance = packet("live-after-acceptance-policy-change")
    _require(live_after_acceptance["packet_digest"] != target_packet["packet_digest"]
             and generation > target_generation, "the separate post-acceptance live change did not move the live packet")
    stage("later-live-policy-stays-outside-history", "After acceptance a fourth D edit changes live policy; it does not alter the earlier frozen audit target or accepted receipt.",
          {"post_acceptance_update": post_acceptance_update,
           "frozen_target_generation": target_generation, "live_governance_generation": generation})

    historical_start = recover("start-recovered-after-acceptance-and-live-change", main_start_operation)
    historical_step = recover("step-recovered-after-acceptance-and-live-change", main_step_operation)
    final_audits = {label: remember_audit("final-show-" + label, store.continuity_audit_show(identifier))
                    for label, identifier in (("main", main_audit_id), ("completed-secondary", original_root_ids[1]),
                                              ("pending-secondary", original_root_ids[2]), ("late", late_audit_id))}
    replay_events = len(store.events())
    accepted_replay = store.accept(main_id, credential=coordinator, expected_policy_generation=generation)
    decision_replay = store.governance_decide(**decision_requests["initial-a-1"], credential=reviewer_a)
    _require(canonical(historical_start["checkpoint"]) == canonical(recovered_start["checkpoint"])
             and canonical(historical_step["checkpoint"]) == canonical(recovered_step["checkpoint"])
             and historical_start["current_checkpoint_id"] == historical_step["current_checkpoint_id"] == main_completed_checkpoint
             and historical_start["audit"]["target_governance_generation"] == target_generation
             and accepted_replay["replayed"] and accepted_replay["governance_receipt"] == receipt
             and decision_replay["replayed"] and decision_replay["decision_id"] == original_approvals[0]
             and len(store.events()) == replay_events
             and final_audits["pending-secondary"]["checkpoint"]["state"] == "pending"
             and final_audits["late"]["checkpoint"]["state"] == "pending",
             "historical recovery/replay moved a checkpoint, changed the receipt or forced a pending audit to complete")
    final_catalogue = catalogue("final-catalogue-after-acceptance")
    final_by_id = {entry["audit_id"]: entry for entry in final_catalogue["entries"]}
    _require(final_by_id[original_root_ids[2]]["observed_head"]["state"] == "pending"
             and final_by_id[late_audit_id]["observed_head"]["state"] == "pending",
             "the catalogue confused accepted proposal status with pending audit state")
    replays = {"accepted": accepted_replay, "original_approval": decision_replay}
    stage("immutable-recovery-after-live-changes", "Recovery returns the initial and first historical checkpoints with main's current completed head, while accepted receipt replay and pending audit states stay historical.",
          {"recovered_start": historical_start, "recovered_advance": historical_step,
           "replays": replays, "final_catalogue": final_catalogue})

    final_status = store.status()
    final_governance = store.governance_status(main_id)
    events = store.events()
    transitions = [event["data"] for event in events if event["type"] == "review_continuity_transition"]
    stored_packet_ids = {result["result"]["content_packet"]["packet_id"] for result in decision_history}
    stored_packet_ids.update(root[side]["packet_id"] for root in audit_headers.values()
                             for side in ("source_packet", "target_packet"))
    stored_packet_ids.add(accepted_packet["packet_id"])
    for identifier in sorted(stored_packet_ids):
        remember_packet("stored-" + identifier, store.content_packet(identifier))
    artifacts = {
        "initial-state.json": initial_snapshot, "final-state.json": store.snapshot(),
        "authors.json": authors, "composition.json": main, "formal-review.json": formal_review,
        "proposals.json": {"composite": store.proposal(main_id),
                           "authors": {label: store.proposal(result["proposal_id"]) for label, result in authors.items()}},
        "activation.json": {"governance": enabled_governance, "authorship": enabled_authorship,
                            "content": enabled_content, "continuity": enabled_continuity},
        "policy.json": active_catalog, "editorial-policy.json": editorial_policy,
        "content-policy-history.json": content_policy_history,
        "governance-policy-history.json": final_governance["policy_history"],
        "catalog-updates.json": catalog_updates, "transitions.json": transitions,
        "packets.json": {"labels": packet_labels, "packets": packets, "stored_packet_ids": sorted(stored_packet_ids)},
        "decisions.json": decision_history, "decision-requests.json": decision_requests,
        "observations.json": observations, "audit-requests.json": audit_requests,
        "audits.json": audit_headers, "checkpoints.json": checkpoints, "audit-results.json": audit_results,
        "recoveries.json": recovery_results, "catalogue-pages.json": catalogue_pages,
        "catalogue-membership.json": {"snapshot_max_seq": ceiling, "original_ids": original_root_ids,
                                       "pinned_ids": pinned_ids, "late_audit_id": late_audit_id, "new_scan_ids": fresh_ids},
        "cli-outcomes.json": cli_outcomes, "read-only-observations.json": read_only_observations,
        "audit-final.json": final_audits, "operations.json": operations, "grants.json": grants,
        "events.json": events, "acceptance.json": accepted, "replays.json": replays,
        "governance-final.json": final_governance, "continuity-final.json": store.continuity_status(main_id),
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "kind": "advisory_review_history_recovery_and_catalogue_product_example",
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(), "workers": workers,
        "actors": {"authenticated_local_capability_principals": True, "persons_authenticated": False,
                   "human_review_recorded": False, "independent_people_or_models_established": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "main_proposal_id": main_id, "main_audit_id": main_audit_id,
        "main_source_decision_id": original_objection["decision_id"],
        "main_start_operation_id": main_start_operation, "main_first_advance_operation_id": main_step_operation,
        "main_initial_checkpoint_id": initial_checkpoint, "main_first_checkpoint_id": first_checkpoint,
        "main_completed_checkpoint_id": main_completed_checkpoint,
        "main_target_packet_id": target_packet["packet_id"], "main_target_governance_generation": target_generation,
        "accepted_content_packet_id": accepted_packet["packet_id"],
        "original_approval_ids": original_approvals,
        "catalogue_snapshot_max_seq": ceiling, "catalogue_original_ids": original_root_ids,
        "catalogue_late_audit_id": late_audit_id, "initial_empty_snapshot_max_seq": initial_empty["snapshot_max_seq"],
        "final_governance_policy_generation": generation,
        "final_content_policy_generation": content_policy_history[-1]["generation"],
        "stages": stages, "initial_runtime_revision": initial_snapshot["revision"], "final_runtime_status": final_status,
        "summary": {
            "author_job_count": len(authors), "author_pool_workers": workers,
            "observed_author_thread_count": len({result["thread"] for result in authors.values()}),
            "authenticated_local_principal_count": len({result["principal_id"] for result in grants.values()} | {ADMINISTRATOR}),
            "composite_proposal_count": 1, "decision_count": len(decision_history), "stage_count": len(stages),
            "catalog_update_count": len(catalog_updates), "target_history_update_count": len(target_updates),
            "post_acceptance_live_update_count": len(catalog_updates) - len(target_updates),
            "audit_count": len(audit_headers), "checkpoint_count": len(checkpoints),
            "main_checked_transition_count": main_complete["checkpoint"]["total_checked_transition_count"],
            "main_chunk_sizes": [recovered_step["checkpoint"]["checked_transition_count"], main_complete["checkpoint"]["checked_transition_count"]],
            "lost_response_count": sum(result["stdout_discarded"] for result in cli_outcomes),
            "cli_child_process_count": len(cli_outcomes), "recover_result_count": len(recovery_results),
            "catalogue_page_count": len(catalogue_pages), "pinned_catalogue_page_count": len(pinned_pages),
            "pinned_catalogue_root_count": len(pinned_ids), "new_catalogue_root_count": len(fresh_ids),
            "late_root_excluded_from_pinned_scan": late_audit_id not in pinned_ids,
            "empty_snapshot_remained_empty": repeated_empty["snapshot_max_seq"] == 0 and not repeated_empty["entries"],
            "pinned_membership_observed_new_head": repeated_page["entries"][0]["observed_head"]["checkpoint_id"] != first_page["entries"][0]["observed_head"]["checkpoint_id"],
            "read_only_observation_count": len(read_only_observations),
            "expected_api_rejection_count": sum(result.get("rejected", False) for result in operations),
            "blocked_acceptance_count": sum(result.get("result", {}).get("accepted") is False for result in operations),
            "runtime_event_count": len(events), "accepted_proposal_count": final_status["proposals"].get("accepted", 0),
            "pending_audit_count_after_acceptance": sum(result["checkpoint"]["state"] == "pending" for result in final_audits.values()),
            "acceptance_receipt_schema": receipt["schema_version"], "original_approval_ids_retained": set(proofs) == set(original_approvals),
            "audit_acceptance_authority": False, "recovery_created_new_event": False,
            "lost_response_model": "stdout_discarded_after_completed_local_CLI_process",
            "network_failure_simulated": False, "process_crash_simulated": False,
            "measured_capacity": False, "semantic_equivalence_proved": False,
            "release_created": False, "credential_material_exported": False,
        },
        "credential_file_layout": {"administrator": "private/admin.credential", "document_author": "private/document-author.credential",
                                   "code_author": "private/code-author.credential", "integrator": "private/integrator.credential",
                                   "reviewer_a": "private/reviewer-a.credential", "reviewer_b": "private/reviewer-b.credential",
                                   "reviewer_c": "private/reviewer-c.credential", "coordinator": "private/coordinator.credential",
                                   "private_database": "private/state.sqlite3"},
        "limitations": [
            "Lost responses are modeled by discarding stdout of two successfully completed local CLI children; no network outage or in-flight crash is reproduced.",
            "Recover requires a current original administrator grant and returns immutable existing checkpoint data with a separately observed current head; it does not retry a write.",
            "The catalogue freezes membership by start-event sequence ceiling. Each page observes heads in its own transaction, without one atomic snapshot of all heads across pages.",
            "Pending audit state is independent of accepted proposal state; catalogue presence, recovery and completed history grant no acceptance authority.",
            "Three catalog transitions precede acceptance. A separate fourth D update occurs afterward and cannot rewrite the accepted receipt or earlier frozen packets.",
            "All review roles are programmed local capability holders. They establish neither independent people/models nor human understanding, factual truth or code behavior.",
            "Proposed code remains data; this example uses no external LLM, executes no proposed Python and makes no raw SQL corruption mutations.",
            "Hashes provide internal consistency under trust in SQLite, the application, administrators and the local clock; they are not external signatures.",
            "Configured workers, child processes and three author jobs are functional workflow evidence, without throughput, load or service-capacity measurements.",
            "Credentials and their hashes remain private. This command saves local artifacts and does not publish Git or create a release.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример восстановления ответов и каталога аудитов", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки — UTC/Unix epoch.", "",
        "Три локальных авторских задания и одна композиция подготовили вымышленный документ и литеральную",
        "Python-конфигурацию. A/B одобрили обе записи, C записал request_changes. Предлагаемый код не исполнялся.", "",
        "## Последовательность", "", "| Этап | Наблюдение | Ревизия |", "|---|---|---:|",
    ]
    lines += [f"| [{result['label']}]({result['artifact']}) | {result['explanation']} | {result['runtime_revision']} |"
              for result in stages]
    lines += [
        "", "## Восстановление известной операции", "",
        "После трёх различных несвязанных обновлений D отдельный процесс обычной CLI выполнил start и завершился",
        "с кодом 0. Его stdout был направлен в DEVNULL. Клиент не получил audit ID из отброшенного ответа.",
        "Следующий отдельный процесс recover по известному operation ID вернул исходный checkpoint и audit ID.",
        "Затем таким же способом был отброшен ответ advance на один переход; recover вернул уже сохранённый шаг.", "",
        "Это модель потерянного ответа через намеренное отбрасывание stdout завершившейся локальной команды.",
        "Сетевой сбой и авария процесса во время записи не воспроизводились. Восстановление не добавило событий.", "",
        "## Каталог с фиксированным составом", "",
        "Три исходных аудита имели разные головы: основной pending после одного перехода, второй history_complete,",
        "третий pending с начальным checkpoint. Первая страница limit=1 зафиксировала snapshot_max_seq.",
        "Затем появился четвёртый аудит вне границы, а основной завершил оставшиеся два перехода.", "",
        "Повтор той же страницы сохранил audit ID и показал новую текущую голову. Три страницы по одной записи",
        "перечислили каждый исходный аудит ровно один раз и исключили поздний. Новый scan увидел все четыре.",
        "Исходный пустой snapshot с границей 0 остался пустым после всех новых start.", "",
        "Граница фиксирует состав по start events. Головы наблюдаются заново на каждой странице; каталог не",
        "предоставляет один общий атомарный снимок голов для всей последовательности страниц.", "",
        "## Принятие и дальнейшая история", "",
        "Ни завершённый аудит, ни recover, ни каталог не сняли действующее возражение C: Store.accept остался",
        "заблокирован. Только явный новый abstain C разрешил одно принятие: ревизия 1 → 2, receipt schema 4.",
        "Четыре исходных одобрения A/B сохранили ID и доказательства preserved_catalog через три перехода.", "",
        "Отдельная четвёртая правка D произошла уже после принятия. Восстановление старых start и advance после",
        "этой правки вернуло неизменяемые прежние checkpoints и более новую completed голову. Историческая",
        "квитанция принятия осталась прежней. Два других аудита сохранили pending даже после accepted proposal.", "",
        "## Сохранённые основания", "",
        "- [Машинный отчёт](report.json), [авторы](authors.json), [предложения](proposals.json), [композиция](composition.json).",
        "- [Исходы CLI процессов](cli-outcomes.json), [восстановления](recoveries.json), [наблюдения без записи](read-only-observations.json).",
        "- [Страницы каталога](catalogue-pages.json), [состав сканов](catalogue-membership.json), [финальные аудиты](audit-final.json).",
        "- [Заголовки](audits.json), [checkpoints](checkpoints.json), [ответы аудитов](audit-results.json), [запросы](audit-requests.json).",
        "- [Полные пакеты](packets.json), [решения](decisions.json), [наблюдения governance](observations.json).",
        "- [История content policy](content-policy-history.json), [история governance](governance-policy-history.json), [переходы](transitions.json).",
        "- [Краткие правки D](catalog-updates.json), [операции и отказы](operations.json), [события](events.json).",
        "- [Принятие](acceptance.json), [исторические повторы](replays.json), [финальное состояние](final-state.json).", "",
        "Полные тела политик и переходов сохранены в общих файлах; этапы не повторяют всю историю каталога.",
        "Source/target packets перечитаны по известным ID через публичный Store.content_packet.", "",
        "## Границы выполненного примера", "",
        f"Пул авторских заданий: {workers} Python workers; наблюдавшихся потоков: {report['summary']['observed_author_thread_count']}; авторских заданий: {len(authors)}.",
        f"Отдельных CLI процессов: {len(cli_outcomes)}; отброшенных ответов: {report['summary']['lost_response_count']}; аудитов: {len(audit_headers)}; checkpoints: {len(checkpoints)}.",
        f"Правок до целевого пакета: {len(target_updates)}; отдельных правок после принятия: {len(catalog_updates) - len(target_updates)}; решений: {len(decision_history)}.",
        "Размер пула и число локальных ролей не задают число независимых моделей или измеренную ёмкость сервиса.",
        "Внешние LLM и человеческая экспертиза отсутствуют. Смысловая истинность прозы, полнота описи и поведение",
        "кода не установлены. Хеши обеспечивают внутренние привязки при доверии к SQLite, приложению и часам.", "",
        "private/ имеет права 0700, credentials и state.sqlite3 — 0600. Секреты, их хеши и SQLite не входят в",
        "публичные JSON/MD. Создание выпуска и публикация в Git являются отдельными действиями.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
