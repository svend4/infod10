"""Run a bounded advisory audit of fictional local review history.

Checkpoints explain a frozen historical target. They do not supply acceptance
authority, replace current votes or execute proposed Python configurations.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import hashlib
import json
from pathlib import Path
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


DOCUMENT_AUTHOR = "history-document-author"
CODE_AUTHOR = "history-code-author"
INTEGRATOR = "history-integrator"
REVIEWER_A = "history-reviewer-a"
REVIEWER_B = "history-reviewer-b"
REVIEWER_C = "history-reviewer-c"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("History audit demonstration stopped: " + message)


def _advisory(result: dict[str, Any]) -> None:
    _require(result.get("advisory_only") is True
             and result.get("acceptance_authority") is False
             and result.get("authority_evaluated") is False,
             "an audit result is missing its explicit advisory boundary")


def run_history_audit_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save an ordinary local workflow in a new directory without private export."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("History audit demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    private = workspace / "private"
    private.mkdir(mode=0o700)
    private_markers: list[str] = []

    def new_credential(label: str) -> str:
        secret = prepare_credential(private / (label + ".credential"))
        private_markers.extend((secret, hashlib.sha256(secret.encode("ascii")).hexdigest()))
        return secret

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers),
                     "public data contains a bearer credential or its private hash")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    store = Store(private / "state.sqlite3")
    store.bootstrap([_document(DOC_A, 3), _document(DOC_D, 3),
                     _file(CODE_A, "code", "SAMPLE_LIMIT = 3\n"),
                     _file(CODE_D, "code", "SAMPLE_LIMIT = 3\n")])
    initial_snapshot = store.snapshot()
    editorial_policy = {"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True}
    active_catalog = _catalog()
    admin = new_credential("admin")
    enabled_governance = store.governance_enable(editorial_policy, "history-administrator", admin)
    generation = 1
    expires_at = time.time() + 3600
    all_artifacts = [DOC_A, CODE_A, DOC_D, CODE_D]
    grants: dict[str, dict[str, Any]] = {}

    def grant(label: str, principal: str, role: str, artifact_ids: list[str]) -> str:
        secret = new_credential(label)
        grants[label] = store.governance_grant(
            principal, role, artifact_ids, expires_at, credential=admin,
            new_credential=secret, operation_id="history-grant-" + label)
        return secret

    document_author = grant("document-author", DOCUMENT_AUTHOR, "doc_author", [DOC_A, DOC_D])
    code_author = grant("code-author", CODE_AUTHOR, "code_author", [CODE_A, CODE_D])
    integrator = grant("integrator", INTEGRATOR, "integrator", all_artifacts)
    reviewer_a = grant("reviewer-a", REVIEWER_A, "reviewer", all_artifacts)
    reviewer_b = grant("reviewer-b", REVIEWER_B, "reviewer", all_artifacts)
    reviewer_c = grant("reviewer-c", REVIEWER_C, "reviewer", all_artifacts)
    coordinator = grant("coordinator", "history-coordinator", "coordinator", all_artifacts)
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
    catalog_update_summaries: list[dict[str, Any]] = []

    def remember_packet(label: str, value: dict[str, Any]) -> dict[str, Any]:
        address = value["packet_id"]
        if address in packets:
            _require(canonical(packets[address]) == canonical(value), "one packet address names different bodies")
        packets[address] = value
        packet_labels[label] = address
        return value

    def expected_exception(label: str, operation: Callable[[], Any], code: str) -> dict[str, Any]:
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == code, "unexpected API rejection for " + label + ": " + error.code)
            result = {"label": label, "rejected": True, "code": error.code}
            operations.append(result)
            return result
        raise RuntimeError("History audit demonstration stopped: expected rejection for " + label)

    bodies = {"document-a": _document(DOC_A, 4),
              "code-a": _file(CODE_A, "code", "SAMPLE_LIMIT = 4\n"),
              "alternative-document": _document(DOC_A, 5)}
    for label, body in bodies.items():
        store.enqueue("history-author-" + label, "doc_author" if body["kind"] == "document" else "code_author", {
            "proposal": _proposal(initial_snapshot, body,
                                  "Fictional history-audit input; proposed Python is stored data only.")})
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
        return {"label": task["task_id"].removeprefix("history-author-"),
                "principal_id": principal, "thread": current_thread().name,
                "task_id": task["task_id"], "fencing_token": task["token"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="history-author") as executor:
        futures = [executor.submit(author_job, index) for index in range(len(bodies))]
        authors = {value["label"]: value for value in (future.result() for future in futures)}
    store.enqueue("history-compose-a", "integrator", {
        "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                       for label in ("document-a", "code-a")],
        "rationale": "Compose an exact fictional document and code candidate for the advisory history example.",
    })
    lease = store.claim_task(INTEGRATOR, roles=["integrator"], credential=integrator, lease_seconds=300)
    _require(lease is not None, "the integrator lease was unavailable")
    main = store.compose(lease["task_id"], INTEGRATOR, lease["token"], credential=integrator)
    main_id = main["proposal_id"]
    formal_review = store.review(main_id, credential=reviewer_a)
    _require(formal_review["approved"], "the formal review did not approve the fictional candidate")

    def packet(label: str) -> dict[str, Any]:
        return remember_packet(label, store.content_status(main_id)["packet"])

    def observe(label: str) -> dict[str, Any]:
        result = store.governance_observe(main_id, credential=coordinator,
                                          expected_policy_generation=generation)
        remember_packet("observation-" + label, result["content"])
        observations[label] = result
        return result

    def stage(label: str, explanation: str, result: Any = None) -> None:
        filename = f"stage-{len(stages) + 1:02d}-{label}.json"
        # Full histories and packets are exported once below. Individual stage
        # files keep their exact outcomes without repeating the whole catalog.
        body = {"label": label, "explanation": explanation, "runtime_status": store.status(),
                "governance_policy_generation": generation,
                "content_policy_generation": content_policy_history[-1]["generation"],
                "operation_result": result}
        write_json(filename, body)
        stages.append({"label": label, "explanation": explanation, "artifact": filename,
                       "runtime_revision": body["runtime_status"]["revision"]})

    def decide(label: str, secret: str, principal: str, identifier: str,
               verdict: str = "approve") -> dict[str, Any]:
        current_packet = packet("decision-" + label)
        previous = decisions.get((principal, identifier))
        request = {
            "proposal_id": main_id, "artifact_id": identifier, "verdict": verdict,
            "rationale": "Programmed local review for " + label
                         + "; no human understanding, factual truth or code behavior is asserted.",
            "expected_proposal_digest": main["digest"], "expected_policy_generation": generation,
            "expected_previous_decision": previous["decision_id"] if previous else None,
            "expected_content_packet_digest": current_packet["packet_digest"],
            "operation_id": "history-decision-" + label,
        }
        result = store.governance_decide(**request, credential=secret)
        decisions[(principal, identifier)] = result
        decision_requests[label] = request
        decision_history.append({"label": label, "result": result})
        return result

    def approve(label: str) -> list[str]:
        return [decide(label + "-" + suffix + "-" + str(index), secret, principal, identifier)["decision_id"]
                for suffix, secret, principal in (("a", reviewer_a, REVIEWER_A), ("b", reviewer_b, REVIEWER_B))
                for index, identifier in enumerate((DOC_A, CODE_A), 1)]

    def accept(label: str, expected: bool, code: str | None = None) -> dict[str, Any]:
        result = store.accept(main_id, credential=coordinator, expected_policy_generation=generation)
        _require(result["accepted"] is expected, "unexpected acceptance outcome for " + label)
        if code:
            _require(any(item["code"] == code for item in result["reasons"]),
                     "the expected acceptance blocker is absent for " + label)
        operations.append({"label": label, "result": result})
        return result

    def update_catalog(label: str, descriptor: dict[str, Any]) -> dict[str, Any]:
        nonlocal generation, active_catalog
        changed = store.content_set_policy(descriptor, credential=admin,
                                           expected_policy_generation=generation)
        generation = changed["policy_generation"]
        current = deepcopy(changed["current_policy"])
        active_catalog = current["policy"]
        content_policy_history.append(current)
        summary = {"label": label, "content_policy_generation": current["generation"],
                   "content_policy_digest": current["policy_digest"],
                   "governance_policy_generation": generation}
        catalog_update_summaries.append(summary)
        return summary

    def remember_audit(label: str, response: dict[str, Any]) -> dict[str, Any]:
        _advisory(response)
        header, checkpoint = response["audit"], response["checkpoint"]
        audit_id, checkpoint_id = header["audit_id"], checkpoint["checkpoint_id"]
        if audit_id in audit_headers:
            _require(canonical(audit_headers[audit_id]) == canonical(header), "a frozen audit header changed")
        if checkpoint_id in checkpoints:
            _require(canonical(checkpoints[checkpoint_id]) == canonical(checkpoint), "an immutable checkpoint changed")
        audit_headers[audit_id] = header
        checkpoints[checkpoint_id] = checkpoint
        audit_results[label] = response
        return response

    def start_audit(label: str, source: dict[str, Any]) -> dict[str, Any]:
        target = packet("audit-target-" + label)
        request = {"decision_id": source["decision_id"], "expected_policy_generation": generation,
                   "expected_target_packet_digest": target["packet_digest"],
                   "operation_id": "history-audit-start-" + label}
        response = store.continuity_audit_start(**request, credential=admin)
        audit_requests[label + "-start"] = request
        remember_audit(label + "-start", response)
        _require(response["checkpoint"]["state"] == "pending", "a nonempty audit was not pending at start")
        return response

    def advance(label: str, current: dict[str, Any], limit: int) -> dict[str, Any]:
        request = {"audit_id": current["audit"]["audit_id"],
                   "expected_checkpoint_id": current["checkpoint"]["checkpoint_id"],
                   "limit": limit, "operation_id": "history-audit-advance-" + label}
        response = store.continuity_audit_advance(**request, credential=admin)
        audit_requests[label] = request
        return remember_audit(label, response)

    stage("authenticated-candidate", "Three local author jobs and one composition prepare a fictional candidate; formal review is recorded.",
          {"authors": authors, "composition": main, "formal_review": formal_review,
           "activation_generation": enabled_continuity["activation_generation"]})
    original_approvals = approve("initial")
    original_objection = decide("original-c-objection", reviewer_c, REVIEWER_C, DOC_A, "request_changes")
    initial_observation = observe("initial-veto")
    _require(not initial_observation["eligible"]
             and any(item["code"] == "governance_changes_requested" for item in initial_observation["reasons"]),
             "the ordinary current objection did not block the initial quorum")
    stage("initial-quorum-and-objection", "Two reviewers approve both writes; C explicitly requests changes. The audit does not grant review authority.",
          initial_observation)

    sweep_start_generation = generation
    sweep = [update_catalog("unrelated-sweep-" + str(index + 1),
                            _scope_edit(active_catalog, "section-d", "audit-long-history-" + str(index + 1)))
             for index in range(130)]
    target_packet = packet("main-frozen-target")
    target_generation = generation
    limited = observe("history-beyond-64")
    _require(any(item["code"] == "continuity_reconsideration_required" for item in limited["reasons"]),
             "the long current history did not retain a proposal-level reconsideration blocker")
    limited_acceptance = accept("bounded-acceptance-still-blocked", False, "continuity_reconsideration_required")
    stage("long-history-keeps-acceptance-bound", "130 unrelated catalog updates exceed the unchanged 64-edge acceptance reader; current heads require reconsideration.",
          {"catalog_update_count": len(sweep), "from_governance_generation": sweep_start_generation,
           "to_governance_generation": target_generation, "observation": limited,
           "acceptance": limited_acceptance})

    invalid_start = {"decision_id": original_objection["decision_id"],
                     "expected_policy_generation": generation,
                     "expected_target_packet_digest": target_packet["packet_digest"]}
    expected_exception("start-without-administrator", lambda: store.continuity_audit_start(
        **invalid_start, credential=None, operation_id="history-audit-start-no-authority"), "governance_unauthorized")
    expected_exception("start-with-stale-generation", lambda: store.continuity_audit_start(
        **{**invalid_start, "expected_policy_generation": generation - 1}, credential=admin,
        operation_id="history-audit-start-wrong-generation"), "audit_policy_changed")
    expected_exception("start-with-wrong-target", lambda: store.continuity_audit_start(
        **{**invalid_start, "expected_target_packet_digest": "0" * 64}, credential=admin,
        operation_id="history-audit-start-wrong-target"), "audit_target_changed")
    started = start_audit("main", original_objection)
    main_audit_id = started["audit"]["audit_id"]
    checkpoint_zero = started["checkpoint"]["checkpoint_id"]
    stage("audit-freezes-an-exact-target", "The administrator starts an advisory audit of C's exact source decision and the current target packet.", started)

    later_edit = update_catalog("live-catalog-after-audit-start", _scope_edit(active_catalog, "section-d", "after-frozen-target"))
    later_packet = packet("live-after-main-start")
    _require(later_packet["packet_digest"] != target_packet["packet_digest"], "the live catalog change did not change the live packet")
    frozen_show = remember_audit("main-frozen-after-live-change", store.continuity_audit_show(main_audit_id))
    _require(frozen_show["audit"]["audit_digest"] == started["audit"]["audit_digest"]
             and frozen_show["checkpoint"]["checkpoint_id"] == checkpoint_zero,
             "the audit followed the live packet instead of retaining its frozen target")
    stage("live-catalog-does-not-move-target", "A later D edit changes live state, while the audit header and its initial checkpoint remain unchanged.",
          {"live_change": later_edit, "frozen_audit": frozen_show,
           "frozen_target_governance_generation": target_generation, "live_governance_generation": generation})

    race_workers = min(workers, 2)
    race_barrier = Barrier(race_workers)
    race_requests = [{"audit_id": main_audit_id, "expected_checkpoint_id": checkpoint_zero,
                      "limit": 64, "operation_id": "history-audit-race-" + str(index + 1)} for index in range(2)]

    def race_advance(index: int) -> dict[str, Any]:
        if index < race_workers:
            race_barrier.wait(timeout=30)
        try:
            response = store.continuity_audit_advance(**race_requests[index], credential=admin)
            return {"index": index, "succeeded": True, "response": response}
        except WorkbenchError as error:
            _require(error.code == "audit_checkpoint_conflict", "an audit writer failed with an unexpected error")
            return {"index": index, "rejected": True, "code": error.code}

    with ThreadPoolExecutor(max_workers=race_workers, thread_name_prefix="history-audit-writer") as executor:
        race = [future.result() for future in [executor.submit(race_advance, index) for index in range(2)]]
    winners = [item for item in race if item.get("succeeded")]
    losers = [item for item in race if item.get("rejected")]
    _require(len(winners) == len(losers) == 1, "CAS did not select exactly one audit checkpoint writer")
    first = remember_audit("main-first-chunk", winners[0]["response"])
    first_request = race_requests[winners[0]["index"]]
    audit_requests["main-first-chunk"] = first_request
    operations.extend({"label": "audit-race-" + str(item["index"] + 1), **item} for item in race)
    _require(first["checkpoint"]["state"] == "pending"
             and first["checkpoint"]["checked_transition_count"] == 64
             and first["checkpoint"]["total_checked_transition_count"] == 64,
             "the first audit chunk did not check exactly 64 transitions")
    expected_exception("stale-checkpoint-different-operation", lambda: store.continuity_audit_advance(
        **{**first_request, "operation_id": "history-audit-stale-new-operation"}, credential=admin),
        "audit_checkpoint_conflict")
    stage("checkpoint-CAS-selects-one-writer", "Two operations propose the same checkpoint; one advances 64 edges and the other receives a CAS conflict.",
          {"writer_count": race_workers, "concurrent_execution": race_workers == 2, "outcomes": race})

    first_historical = remember_audit("main-first-historical-show", store.continuity_audit_show(
        main_audit_id, checkpoint_id=first["checkpoint"]["checkpoint_id"]))
    second = advance("main-second-chunk", first, 64)
    _require(second["checkpoint"]["state"] == "pending"
             and second["checkpoint"]["checked_transition_count"] == 64
             and second["checkpoint"]["total_checked_transition_count"] == 128,
             "the second audit chunk did not continue to 128 checked transitions")
    stage("second-bounded-chunk", "A second transaction checks the next 64 transitions and links its immutable checkpoint to the previous segment.",
          {"historical_first_checkpoint": first_historical, "advanced": second})

    completed = advance("main-final-chunk", second, 2)
    _require(completed["checkpoint"]["state"] == "history_complete"
             and completed["checkpoint"]["checked_transition_count"] == 2
             and completed["checkpoint"]["total_checked_transition_count"] == 130
             and completed["checkpoint"]["to_cursor"]["governance_generation"] == target_generation,
             "the final two edges did not complete the frozen 130-transition target")
    main_completed_checkpoint = completed["checkpoint"]["checkpoint_id"]
    stage("frozen-history-completed", "The final two transitions complete exactly the frozen target, excluding the later live D edit. Completion stays advisory.", completed)

    events_before_replay = len(store.events())
    replayed_chunk = remember_audit("main-first-chunk-replay", store.continuity_audit_advance(
        **first_request, credential=admin))
    _require(replayed_chunk["replayed"]
             and canonical(replayed_chunk["checkpoint"]) == canonical(first["checkpoint"])
             and len(store.events()) == events_before_replay
             and store.continuity_audit_show(main_audit_id)["current_checkpoint_id"] == main_completed_checkpoint,
             "an old operation replay changed the current checkpoint or historical chunk")
    expected_exception("changed-idempotency-request", lambda: store.continuity_audit_advance(
        **{**first_request, "limit": 63}, credential=admin), "audit_idempotency_conflict")
    stage("old-chunk-replay-is-historical", "The first operation is replayed after later checkpoints; its old body is returned without new events or head movement.", replayed_chunk)

    after_completed = observe("completed-audit-has-no-authority")
    _require(any(item["code"] == "continuity_reconsideration_required" for item in after_completed["reasons"]),
             "an advisory completed audit removed the bounded acceptance blocker")
    still_blocked = accept("completed-audit-does-not-authorize", False, "continuity_reconsideration_required")
    stage("completed-audit-does-not-authorize", "Store.accept retains the MAX64 contract after the advisory audit has completed; no vote or grant was upgraded.",
          {"observation": after_completed, "acceptance": still_blocked})

    fresh_before_scope = approve("fresh-after-audit")
    fresh_quorum = observe("fresh-quorum-old-objection")
    _require(all(len(item["approval_principals"]) == 2 for item in fresh_quorum["writes"])
             and not fresh_quorum["eligible"]
             and any(item["code"] == "continuity_reconsideration_required" for item in fresh_quorum["reasons"]),
             "fresh approvals bypassed the old current-head objection")
    fresh_still_blocked = accept("fresh-quorum-retains-uncertainty", False, "continuity_reconsideration_required")
    stage("fresh-quorum-does-not-clear-old-head", "Fresh A/B approvals supply a quorum, but C's unresolved old head remains a proposal-level blocker.",
          {"fresh_approval_ids": fresh_before_scope, "observation": fresh_quorum, "acceptance": fresh_still_blocked})

    scope_source = decide("fresh-c-objection-before-scope-change", reviewer_c, REVIEWER_C, DOC_A, "request_changes")
    fresh_objection = observe("fresh-explicit-objection")
    _require(any(item["code"] == "governance_changes_requested" for item in fresh_objection["reasons"])
             and not any(item["code"] == "continuity_reconsideration_required" for item in fresh_objection["reasons"]),
             "explicit current reconsideration did not distinguish a real veto from unresolved history")
    current_veto_blocked = accept("fresh-explicit-veto-blocks", False, "governance_changes_requested")
    stage("fresh-objection-remains-a-veto", "C explicitly records a current request_changes. Historical uncertainty is replaced by an active objection, not an approval.",
          {"source_decision": scope_source, "observation": fresh_objection, "acceptance": current_veto_blocked})

    scope_before = deepcopy(active_catalog)
    scope_changed = update_catalog("selected-a-temporary-change", _scope_edit(active_catalog, "section-a", "temporary"))
    scope_restored = update_catalog("selected-a-restored", scope_before)
    scope_started = start_audit("selected-scope-return", scope_source)
    scope_audit_id = scope_started["audit"]["audit_id"]
    stage("selected-change-return-audit-started", "A's selected policy changes and returns; a separate audit targets both recorded transitions from a fresh source.",
          {"changed": scope_changed, "restored": scope_restored, "audit": scope_started})
    scope_terminal = advance("selected-scope-return-step", scope_started, 64)
    _require(scope_terminal["checkpoint"]["state"] == "scope_changed",
             "the selected-policy change-and-return did not stop at a scope barrier")
    stage("selected-policy-barrier-is-explained", "The audit stops at the selected-context change even though the final selected policy has returned to its former body.", scope_terminal)

    reset_source = decide("fresh-c-objection-before-governance-reset", reviewer_c, REVIEWER_C, DOC_A, "request_changes")
    direct_reset = store.governance_set_policy(deepcopy(editorial_policy), credential=admin,
                                               expected_generation=generation)
    generation = direct_reset["generation"]
    reset_started = start_audit("direct-governance-reset", reset_source)
    reset_audit_id = reset_started["audit"]["audit_id"]
    stage("direct-reset-audit-started", "A fresh source precedes an identical direct governance policy update; this is a reset, not a content-only edge.",
          {"direct_policy_reset": direct_reset, "audit": reset_started})
    reset_terminal = advance("direct-governance-reset-step", reset_started, 64)
    _require(reset_terminal["checkpoint"]["state"] == "governance_reset",
             "the direct governance barrier was not distinguished from an invalid history")
    stage("governance-reset-is-explained", "The separate audit terminates at a known direct policy reset. It does not manufacture a successful continuity edge.", reset_terminal)

    final_approval_ids = approve("final-current")
    final_abstain = decide("final-explicit-c-abstain", reviewer_c, REVIEWER_C, DOC_A, "abstain")
    final_observation = observe("fresh-current-consideration")
    _require(final_observation["eligible"], "fresh current approvals and explicit abstain did not satisfy existing acceptance rules")
    stage("fresh-consideration-under-current-policy", "Current A/B decisions and C's explicit abstain satisfy the existing policy; no advisory checkpoint is used as a vote.",
          {"approval_ids": final_approval_ids, "abstain": final_abstain, "observation": final_observation})

    accepted_packet = packet("accepted-current")
    accepted = accept("accepted-with-current-decisions", True)
    receipt = accepted["governance_receipt"]
    _require(accepted["revision"] == 2 and receipt["schema_version"] == 4
             and set(receipt["review_continuity"]["used_decisions"]) == set(final_approval_ids),
             "the final acceptance did not preserve its actual current decision IDs")
    stage("acceptance-uses-existing-authority", "One acceptance advances revision 1 to 2 with receipt schema 4 and fresh decisions; the audit remains separate advisory material.", accepted)

    replay_events = len(store.events())
    final_chunk_replay = remember_audit("main-first-chunk-after-acceptance", store.continuity_audit_advance(
        **first_request, credential=admin))
    initial_decision_replay = store.governance_decide(**decision_requests["initial-a-1"], credential=reviewer_a)
    accepted_replay = store.accept(main_id, credential=coordinator, expected_policy_generation=generation)
    final_audit_shows = {
        "main": remember_audit("main-final-show", store.continuity_audit_show(main_audit_id)),
        "selected-scope-return": remember_audit("selected-scope-final-show", store.continuity_audit_show(scope_audit_id)),
        "direct-governance-reset": remember_audit("direct-reset-final-show", store.continuity_audit_show(reset_audit_id)),
    }
    _require(final_chunk_replay["replayed"]
             and canonical(final_chunk_replay["checkpoint"]) == canonical(first["checkpoint"])
             and initial_decision_replay["replayed"]
             and initial_decision_replay["decision_id"] == original_approvals[0]
             and accepted_replay["replayed"] and accepted_replay["governance_receipt"] == receipt
             and final_audit_shows["main"]["checkpoint"]["state"] == "history_complete"
             and final_audit_shows["main"]["checkpoint"]["to_cursor"]["governance_generation"] == target_generation
             and len(store.events()) == replay_events,
             "historical reads or replays changed an object, frozen target or event count")
    replays = {"first_chunk_after_completion": replayed_chunk, "first_chunk_after_acceptance": final_chunk_replay,
               "initial_decision": initial_decision_replay, "acceptance": accepted_replay}
    stage("historical-audits-and-replays-stay-frozen", "After acceptance, old checkpoint replays, original decisions and both terminal barrier explanations remain exact historical objects.",
          {"replays": replays, "audit_shows": final_audit_shows})

    final_status = store.status()
    final_governance = store.governance_status(main_id)
    events = store.events()
    transition_records = [item["data"] for item in events if item["type"] == "review_continuity_transition"]
    # Export every stored content packet known from source decisions, audit
    # targets or acceptance. Live observations remain separately labelled.
    stored_packet_ids = {item["result"]["content_packet"]["packet_id"] for item in decision_history}
    stored_packet_ids.update((target_packet["packet_id"], accepted_packet["packet_id"]))
    stored_packet_ids.update(packet_labels["audit-target-" + label]
                             for label in ("main", "selected-scope-return", "direct-governance-reset"))
    stored_packets = {identifier: store.content_packet(identifier) for identifier in sorted(stored_packet_ids)}
    for identifier, value in stored_packets.items():
        remember_packet("stored-" + identifier, value)
    terminal_states = {label: result["checkpoint"]["state"] for label, result in final_audit_shows.items()}
    artifacts = {
        "initial-state.json": initial_snapshot, "final-state.json": store.snapshot(),
        "authors.json": authors, "composition.json": main, "formal-review.json": formal_review,
        "proposals.json": {"composite": store.proposal(main_id),
                           "authors": {label: store.proposal(value["proposal_id"])
                                       for label, value in authors.items()}},
        "activation.json": {"governance": enabled_governance, "authorship": enabled_authorship,
                            "content": enabled_content, "continuity": enabled_continuity},
        "policy.json": active_catalog, "editorial-policy.json": editorial_policy,
        "content-policy-history.json": content_policy_history,
        "governance-policy-history.json": final_governance["policy_history"],
        "catalog-updates.json": catalog_update_summaries, "transitions.json": transition_records,
        "packets.json": {"labels": packet_labels, "packets": packets, "stored_packet_ids": sorted(stored_packets)},
        "observations.json": observations, "decisions.json": decision_history,
        "audit-requests.json": audit_requests, "audits.json": audit_headers,
        "checkpoints.json": checkpoints, "audit-steps.json": audit_results,
        "audit-final.json": final_audit_shows, "audit-writer-race.json": race,
        "operations.json": operations, "grants.json": grants, "events.json": events,
        "acceptance.json": accepted, "replays.json": replays,
        "governance-final.json": final_governance, "continuity-final.json": store.continuity_status(main_id),
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        "kind": "advisory_incremental_review_history_audit_product_example", "workers": workers,
        "actors": {"authenticated_local_capability_principals": True, "persons_authenticated": False,
                   "human_review_recorded": False, "independent_people_or_models_established": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "main_proposal_id": main_id, "main_audit_id": main_audit_id,
        "main_source_decision_id": original_objection["decision_id"],
        "main_target_packet_id": target_packet["packet_id"],
        "main_target_governance_generation": target_generation,
        "main_completed_checkpoint_id": main_completed_checkpoint,
        "main_checkpoint_ids": [started["checkpoint"]["checkpoint_id"], first["checkpoint"]["checkpoint_id"],
                                second["checkpoint"]["checkpoint_id"], completed["checkpoint"]["checkpoint_id"]],
        "scope_changed_audit_id": scope_audit_id, "governance_reset_audit_id": reset_audit_id,
        "accepted_content_packet_id": accepted_packet["packet_id"],
        "final_governance_policy_generation": generation,
        "final_content_policy_generation": content_policy_history[-1]["generation"],
        "main_first_chunk_request": first_request,
        "stages": stages, "initial_runtime_revision": initial_snapshot["revision"], "final_runtime_status": final_status,
        "summary": {"author_job_count": len(authors), "composite_proposal_count": 1,
                    "decision_count": len(decision_history), "stage_count": len(stages),
                    "catalog_update_count": len(catalog_update_summaries), "long_history_update_count": len(sweep),
                    "audit_count": len(audit_headers), "checkpoint_count": len(checkpoints),
                    "main_checked_transition_count": completed["checkpoint"]["total_checked_transition_count"],
                    "main_chunk_sizes": [value["checkpoint"]["checked_transition_count"] for value in (first, second, completed)],
                    "terminal_audit_states": terminal_states,
                    "audit_writer_count": race_workers, "writer_race_success_count": len(winners),
                    "writer_race_conflict_count": len(losers),
                    "expected_api_rejection_count": sum(item.get("rejected", False) for item in operations),
                    "blocked_acceptance_count": sum(item.get("result", {}).get("accepted") is False for item in operations),
                    "runtime_event_count": len(events), "accepted_proposal_count": final_status["proposals"].get("accepted", 0),
                    "acceptance_receipt_schema": receipt["schema_version"], "audit_acceptance_authority": False,
                    "frozen_target_excludes_later_live_edit": completed["checkpoint"]["to_cursor"]["governance_generation"] == target_generation < generation,
                    "completed_audit_did_not_clear_acceptance_blocker": not after_completed["eligible"],
                    "fresh_quorum_did_not_bypass_old_objection": not fresh_quorum["eligible"],
                    "release_created": False, "credential_material_exported": False,
                    "measured_capacity": False, "semantic_equivalence_proved": False},
        "credential_file_layout": {"administrator": "private/admin.credential",
                                   "active_document_author": "private/document-author.credential",
                                   "active_code_author": "private/code-author.credential",
                                   "active_integrator": "private/integrator.credential",
                                   "active_reviewer_a": "private/reviewer-a.credential",
                                   "active_reviewer_b": "private/reviewer-b.credential",
                                   "active_reviewer_c": "private/reviewer-c.credential",
                                   "active_coordinator": "private/coordinator.credential",
                                   "private_database": "private/state.sqlite3"},
        "limitations": [
            "An audit is advisory and has acceptance_authority=false and authority_evaluated=false; completion does not change Store.accept's 64-transition bound.",
            "Every chunk observes a separate SQL transaction and the same frozen target; later live policy changes are outside that audit.",
            "Checkpoint hashes provide internal consistency and CAS/idempotency evidence, not external signatures, human identity or independent expertise.",
            "The workflow performs no raw SQL corruption, executes no proposed Python and calls no external LLM or person.",
            "No history_invalid outcome is fabricated; the public examples cover valid complete history and real selected-policy/governance barriers.",
            "The 130-update sequence and two-writer CAS example are bounded functional examples, not load, throughput or capacity measurements.",
            "Existing grants, current heads, scope, expiry, revocation, author separation, formal review and runtime checks remain acceptance requirements.",
            "Declared field equality and historical scope consistency do not establish prose truth, inventory completeness, program behavior or human understanding.",
            "The SQLite owner, local clock, application implementation and administrative policy remain trusted.",
            "Credentials, their hashes and SQLite stay private; this command does not publish Git or create a release.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример пошагового аудита истории", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Три локальных авторских задания и одна композиция подготовили вымышленный документ и литеральную",
        "Python-конфигурацию. Два проверяющих одобрили обе записи; C записал request_changes. Код не исполнялся.", "",
        "## Последовательность", "", "| Состояние | Наблюдение | Ревизия |", "|---|---|---:|",
    ]
    for item in stages:
        lines.append(f"| [{item['label']}]({item['artifact']}) | {item['explanation']} | {item['runtime_revision']} |")
    lines += [
        "", "## Практический результат", "",
        "После 130 несвязанных обновлений D текущие головы решений превысили предел 64 перехода принятия.",
        "Администратор начал отдельный аудит C с точным целевым пакетом. Следующая живая правка D не передвинула",
        "цель. Три порции 64 + 64 + 2 завершили ровно замороженные 130 переходов.", "",
        "Две операции пытались продвинуть один checkpoint: CAS разрешил одну, другая получила конфликт. Повтор",
        "первой операции после более новых checkpoints вернул прежний сегмент без событий и изменения головы.",
        "Изменение параметра прежнего operation ID было отклонено как idempotency conflict.", "",
        "history_complete остаётся совещательным выводом с acceptance_authority=false и authority_evaluated=false.",
        "Он не снял MAX64 blocker. Свежий кворум A/B также не обошёл старую неопределённую голову C. Явное новое",
        "request_changes C заменило неопределённость действующим возражением и продолжило блокировать принятие.", "",
        "Два отдельных аудита рассмотрели свежие источники возле реальных барьеров. Изменение выбранного A с",
        "последующим возвратом завершилось scope_changed; прямая идентичная governance-политика дала",
        "governance_reset. Повреждение SQL не имитировалось, history_invalid не выдаётся за выполненный случай.", "",
        "Только текущие новые решения A/B и явный abstain C разрешили одно принятие: ревизия 1 → 2, receipt",
        "schema 4. Аудит не стал голосом, полномочием или заменой принятия. Исторические чтения после приёмки",
        "сохранили checkpoints, прежнее решение и квитанцию без новых событий.", "",
        "## Материалы для проверки ссылок и хешей", "",
        "- [Полный отчёт](report.json), [авторы](authors.json), [композиция](composition.json), [предложения](proposals.json).",
        "- [Пакеты с метками](packets.json), [решения](decisions.json), [наблюдения](observations.json).",
        "- [Заголовки аудитов](audits.json), [все checkpoints](checkpoints.json), [ответы шагов](audit-steps.json).",
        "- [Запросы операций](audit-requests.json), [финальные аудиты](audit-final.json), [CAS-гонка](audit-writer-race.json).",
        "- [История content-политик](content-policy-history.json), [история governance](governance-policy-history.json), [полные переходы](transitions.json).",
        "- [Краткие обновления каталога](catalog-updates.json), [активация](activation.json), [операции и отказы](operations.json).",
        "- [Принятие](acceptance.json), [повторы](replays.json), [события](events.json), [конечное состояние](final-state.json).", "",
        "Полные политики и история сохранены один раз в отдельных файлах; файлы этапов не повторяют весь каталог.",
        "Привязки позволяют проверить внутреннюю согласованность сохранённых данных, без внешней подписи.", "",
        "## Границы", "",
        "Проверяется история явно объявленной области и сохранённых ссылок. Истинность прозы, полнота описи,",
        "поведение программы, человеческое понимание и независимость участников не установлены. Все роли",
        "запрограммированы; внешние LLM и человеческая экспертиза отсутствуют. Предлагаемый код не исполняется.", "",
        "Шаги используют разные SQL-снимки; текущий Store.accept остаётся отдельной транзакцией и сохраняет",
        "свой предел 64. Хеши checkpoints и цепочек не заменяют доверие к реализации, SQLite и локальным часам.",
        "130 обновлений и две операции записи являются примером договора, без измерения пропускной способности.", "",
        "private/ имеет права 0700, credentials — 0600. Секреты, их хеши и SQLite не публикуются. Отдельные JSON",
        "наблюдения последовательны; внешняя публикация и выпуск выполняются отдельными действиями.", "",
        f"Пул авторских заданий: {workers} Python workers; пул записи checkpoints: {race_workers}; конкурирующих операций CAS: {len(race_requests)}.",
        f"Решений: {len(decision_history)}; аудитов: {len(audit_headers)}; checkpoints: {len(checkpoints)}; событий: {len(events)}.",
        "Размер пула не является измеренной ёмкостью сервиса или числом независимых моделей.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
