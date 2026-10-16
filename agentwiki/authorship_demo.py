"""Exercise grant-bound authorship, nested provenance and contributor separation.

All actors are programmed local principals. The workflow stores fictional code
as proposed data and never executes it. Credentials and SQLite remain private.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
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
from .store import Store, WorkbenchError


DOC_ID = "docs/local-authorship.md"
CODE_ID = "code/local_authorship.py"
DOCUMENT_AUTHOR = "local-document-author"
CODE_AUTHOR = "local-code-author"
INTEGRATOR_A = "local-integrator-a"
INTEGRATOR_B = "local-integrator-b"


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete product workflow before recording success."""
    if not condition:
        raise RuntimeError("Authorship demonstration stopped: " + message)


def _file(identifier: str, kind: str, template: str) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "template": template, "bindings": []}


def run_authorship_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save an ordinary bounded workflow without exporting private material."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Authorship demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    private = workspace / "private"
    private.mkdir(mode=0o700)
    credentials: dict[str, str] = {}

    def new_credential(label: str) -> str:
        token = prepare_credential(private / (label + ".credential"))
        credentials[label] = token
        return token

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                lowered = key.lower()
                if lowered in {"credential", "credential_hash", "credential_digest", "token_hash", "token_digest"}:
                    continue
                if ("credential" in lowered or "secret" in lowered) and ("hash" in lowered or "digest" in lowered):
                    continue
                result[key] = public(item)
            return result
        if isinstance(value, (list, tuple)):
            return [public(item) for item in value]
        if isinstance(value, str):
            for secret in credentials.values():
                _require(secret not in value and hashlib.sha256(secret.encode("ascii")).hexdigest() not in value,
                         "a public artifact contains private credential material")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    store = Store(private / "state.sqlite3")
    store.bootstrap([
        _file(DOC_ID, "document", "# Fictional local contract\n\nThe configured sample limit is 3.\n"),
        _file(CODE_ID, "code", "# Fictional configuration stored as data.\nSAMPLE_LIMIT = 3\n"),
    ])
    initial_snapshot = store.snapshot()
    legacy_payload = _proposal(initial_snapshot, _file(
        DOC_ID, "document", "# Fictional legacy contract\n\nThe configured sample limit is 6.\n"),
        "A self-declared proposal created before authenticated authorship was enabled.")
    store.enqueue("authorship-legacy", "doc_author", {"proposal": legacy_payload})
    legacy_task = store.claim_task("legacy-self-declared-author", roles=["doc_author"], lease_seconds=300)
    _require(legacy_task is not None, "the legacy task was unavailable")
    legacy = store.submit(legacy_task["task_id"], "legacy-self-declared-author", legacy_task["token"], legacy_payload)
    legacy_review = store.review(legacy["proposal_id"], reviewer="legacy-formal-reviewer")
    _require(legacy_review["approved"], "the legacy formal review unexpectedly failed")

    policy = {"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True}
    admin = new_credential("admin")
    governance_enabled = store.governance_enable(policy, "local-administrator", admin)
    generation = 1
    expires_at = time.time() + 3600
    grants: dict[str, dict[str, Any]] = {}

    def grant(label: str, principal_id: str, role: str, artifact_ids: list[str]) -> str:
        token = new_credential(label)
        grants[label] = store.governance_grant(principal_id, role, artifact_ids, expires_at,
                                             credential=admin, new_credential=token,
                                             operation_id="grant-" + label)
        return token

    document_author = grant("document-author-original", DOCUMENT_AUTHOR, "doc_author", [DOC_ID])
    code_author = grant("code-author", CODE_AUTHOR, "code_author", [CODE_ID])
    integrator_a = grant("integrator-a", INTEGRATOR_A, "integrator", [DOC_ID, CODE_ID])
    integrator_b = grant("integrator", INTEGRATOR_B, "integrator", [DOC_ID, CODE_ID])
    reviewer_a = grant("reviewer-a", "local-reviewer-a", "reviewer", [DOC_ID, CODE_ID])
    reviewer_b = grant("reviewer-b", "local-reviewer-b", "reviewer", [DOC_ID, CODE_ID])
    coordinator = grant("coordinator", "local-coordinator", "coordinator", [DOC_ID, CODE_ID])
    author_as_reviewer = grant("author-as-reviewer", DOCUMENT_AUTHOR, "reviewer", [DOC_ID, CODE_ID])
    integrator_as_reviewer = grant("integrator-as-reviewer", INTEGRATOR_A, "reviewer", [DOC_ID, CODE_ID])
    integrator_as_coordinator = grant("integrator-as-coordinator", INTEGRATOR_A, "coordinator", [DOC_ID, CODE_ID])
    operations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    decision_history: list[dict[str, Any]] = []
    decisions: dict[tuple[str, str, str], dict[str, Any]] = {}

    def expected_exception(label: str, operation: Callable[[], Any],
                           expected_code: str = "governance_unauthorized") -> dict[str, Any]:
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == expected_code, "unexpected API rejection for " + label)
            result = {"label": label, "rejected": True, "code": error.code}
            operations.append(result)
            return result
        raise RuntimeError("Authorship demonstration stopped: expected API rejection for " + label)

    def stage(label: str, explanation: str, result: Any = None,
              proposal_id: str | None = None) -> None:
        filename = f"stage-{len(stages) + 1:02d}-{label}.json"
        item = {"label": label, "explanation": explanation,
                "runtime_revision": store.status()["revision"],
                "authorship": store.authorship_status(proposal_id),
                "governance": store.governance_status(proposal_id), "operation_result": result}
        write_json(filename, item)
        stages.append({"label": label, "explanation": explanation, "artifact": filename,
                       "runtime_revision": item["runtime_revision"]})

    def decide(label: str, token: str, principal: str, artifact_id: str,
               proposal: dict[str, Any], verdict: str = "approve") -> dict[str, Any]:
        key = (proposal["proposal_id"], principal, artifact_id)
        previous = decisions.get(key)
        result = store.governance_decide(
            proposal["proposal_id"], artifact_id, verdict,
            "Programmed local decision for " + label + "; no human review or semantic proof is asserted.",
            credential=token, expected_proposal_digest=proposal["digest"],
            expected_policy_generation=generation,
            expected_previous_decision=previous["decision_id"] if previous else None,
            operation_id="decision-" + label,
        )
        decisions[key] = result
        decision_history.append({"label": label, "result": result})
        return result

    def accept(label: str, proposal: dict[str, Any], *, accepted: bool,
               token: str = coordinator, expected_code: str | None = None) -> dict[str, Any]:
        result = store.accept(proposal["proposal_id"], credential=token,
                              expected_policy_generation=generation)
        _require(result["accepted"] is accepted, "unexpected acceptance state for " + label)
        if expected_code is not None:
            _require(any(item["code"] == expected_code for item in result["reasons"]),
                     "the expected blocking reason is missing for " + label)
        operations.append({"label": label, "result": result})
        return result

    old_decision = decide("legacy-generation-one", reviewer_a, "local-reviewer-a", DOC_ID, legacy)
    stage("legacy-before-authorship", "A proposal, formal review and editorial opinion exist before authorship activation.",
          {"proposal": legacy, "formal_review": legacy_review, "decision": old_decision}, legacy["proposal_id"])
    enabled = store.authorship_enable(credential=admin, expected_policy_generation=generation)
    generation = enabled["policy_generation"]
    _require(generation == 2, "activation did not advance the policy generation exactly once")
    legacy_after_enable = store.authorship_status(legacy["proposal_id"])
    _require(not legacy_after_enable["integrity_valid"], "activation invented a legacy authorship proof")
    stage("activated-generation-two", "One-way activation advances policy generation and does not backfill old authorship.",
          enabled, legacy["proposal_id"])

    identity_rejections = [
        expected_exception("claim-without-credential", lambda: store.claim_task(DOCUMENT_AUTHOR)),
        expected_exception("forged-worker-label", lambda: store.claim_task(
            "forged-document-author", credential=document_author), "authorship_principal_mismatch"),
        expected_exception("claim-wrong-role", lambda: store.claim_task(
            DOCUMENT_AUTHOR, roles=["code_author"], credential=document_author), "authorship_role_mismatch"),
    ]
    stage("identity-and-role-enforced", "Claims require a current local grant, its exact principal and its author role.", identity_rejections)

    bodies = {
        "document": _file(DOC_ID, "document", "# Fictional local contract\n\nThe configured sample limit is 4.\n"),
        "code": _file(CODE_ID, "code", "# Fictional configuration stored as data.\nSAMPLE_LIMIT = 4\n"),
        "competing-document": _file(DOC_ID, "document", "# Fictional competing contract\n\nThe configured sample limit is 5.\n"),
    }
    for label, body in bodies.items():
        role = "doc_author" if body["kind"] == "document" else "code_author"
        store.enqueue("authorship-" + label, role, {"proposal": _proposal(
            initial_snapshot, body, "Programmed author contribution; proposed code is stored as data and never executed.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int, label: str) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        role = "doc_author" if bodies[label]["kind"] == "document" else "code_author"
        principal, credential = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author"
                                 else (CODE_AUTHOR, code_author))
        task = store.claim_task(principal, roles=[role], lease_seconds=300, credential=credential)
        _require(task is not None, "an authenticated author task was unavailable")
        submitted = store.submit(task["task_id"], principal, task["token"], task["payload"]["proposal"], credential=credential)
        return {"label": task["task_id"].removeprefix("authorship-"), "principal_id": principal,
                "thread": current_thread().name, "task_id": task["task_id"],
                "fencing_token": task["token"], "proposal_payload": task["payload"]["proposal"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="authorship-author") as executor:
        futures = [executor.submit(author_job, index, label) for index, label in enumerate(bodies)]
        authors = {item["label"]: item for item in (future.result() for future in futures)}
    stage("authenticated-worker-queue", "Three jobs create immutable grant-bound proofs for two registered author principals.", authors)

    outside_payload = _proposal(initial_snapshot, _file(
        "docs/outside-authorship-scope.md", "document", "# Fictional out-of-scope candidate\n"),
        "An ordinary valid document payload outside the author's exact artifact grant.")
    store.enqueue("authorship-outside-scope", "doc_author", {"proposal": outside_payload})
    outside_task = store.claim_task(DOCUMENT_AUTHOR, credential=document_author, lease_seconds=300)
    _require(outside_task is not None and outside_task["task_id"] == "authorship-outside-scope", "the scope example claimed another task")
    count_before_scope = sum(store.status()["proposals"].values())
    scope_rejection = expected_exception("submit-outside-exact-scope", lambda: store.submit(
        outside_task["task_id"], DOCUMENT_AUTHOR, outside_task["token"], outside_payload, credential=document_author))
    _require(sum(store.status()["proposals"].values()) == count_before_scope, "an unauthorized proposal survived the transaction")
    store.heartbeat(outside_task["task_id"], DOCUMENT_AUTHOR, outside_task["token"], lease_seconds=300, credential=document_author)
    stage("exact-scope-and-rollback", "Out-of-scope submission rolls back the candidate and proof; the original grant can extend its own lease.",
          {"rejection": scope_rejection, "proposal_count_unchanged": True, "authenticated_heartbeat_completed": True})

    retry_payload = _proposal(initial_snapshot, _file(
        DOC_ID, "document", "# Fictional regrant candidate\n\nThe configured sample limit is 7.\n"),
        "A deterministic expired attempt is reclaimed with a different grant and fencing token.")
    store.enqueue("authorship-regrant", "doc_author", {"proposal": retry_payload})
    old_attempt = store.claim_task(DOCUMENT_AUTHOR, credential=document_author, lease_seconds=0)
    _require(old_attempt is not None and old_attempt["task_id"] == "authorship-regrant", "the regrant example claimed another task")
    original_revocation = store.governance_revoke(grants["document-author-original"]["grant_id"], credential=admin)
    current_document_author = grant("document-author-current", DOCUMENT_AUTHOR, "doc_author", [DOC_ID])
    old_document = authors["document"]
    grant_rejections = [
        expected_exception("replacement-grant-cannot-continue-lease", lambda: store.heartbeat(
            old_attempt["task_id"], DOCUMENT_AUTHOR, old_attempt["token"], credential=current_document_author),
            "authorship_grant_mismatch"),
        expected_exception("replacement-grant-cannot-relabel-completed-submit", lambda: store.submit(
            old_document["task_id"], DOCUMENT_AUTHOR, old_document["fencing_token"], old_document["proposal_payload"],
            credential=current_document_author), "authorship_grant_mismatch"),
        expected_exception("revoked-original-cannot-replay-submit", lambda: store.submit(
            old_document["task_id"], DOCUMENT_AUTHOR, old_document["fencing_token"], old_document["proposal_payload"],
            credential=document_author)),
    ]
    historical_document = store.authorship_status(old_document["proposal_id"])
    _require(historical_document["integrity_valid"], "revocation erased valid historical authorship")
    new_attempt = store.claim_task(DOCUMENT_AUTHOR, credential=current_document_author, lease_seconds=300)
    _require(new_attempt is not None and new_attempt["task_id"] == old_attempt["task_id"]
             and new_attempt["token"] > old_attempt["token"], "reclaim did not create a newer fencing attempt")
    reclaimed = store.submit(new_attempt["task_id"], DOCUMENT_AUTHOR, new_attempt["token"], retry_payload,
                             credential=current_document_author)
    stage("regrant-and-fenced-reclaim", "A replacement grant cannot continue or relabel an old attempt; a fresh fenced attempt succeeds and old proofs remain inspectable.",
          {"old_attempt": old_attempt, "new_attempt": new_attempt, "rejections": grant_rejections,
           "historical_document": historical_document, "reclaimed": reclaimed}, old_document["proposal_id"])

    def compose(task_id: str, principal: str, credential: str,
                components: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, Any]]:
        store.enqueue(task_id, "integrator", {
            "components": [{"proposal_id": item["proposal_id"], "digest": item["digest"]} for item in components],
            "rationale": "Compose exact fictional candidates while preserving every author and intermediate integrator.",
        })
        task = store.claim_task(principal, credential=credential, lease_seconds=300)
        _require(task is not None and task["task_id"] == task_id, "an integrator claimed another task")
        return store.compose(task_id, principal, task["token"], credential=credential), task

    intermediate, intermediate_task = compose("authorship-compose-a", INTEGRATOR_A, integrator_a,
                                             [authors["document"], authors["code"]])
    _require(len(intermediate["authorship"]["contributor_principals"]) == 3, "the first integrator lost source authors")
    stage("compose-after-author-revocation", "Historical author authorization remains valid when a later integrator composes the exact original components.",
          intermediate, intermediate["proposal_id"])
    composite, root_task = compose("authorship-compose-b", INTEGRATOR_B, integrator_b, [intermediate])
    main_id = composite["proposal_id"]
    root_authorship = store.authorship_status(main_id)
    expected_contributors = {DOCUMENT_AUTHOR, CODE_AUTHOR, INTEGRATOR_A, INTEGRATOR_B}
    _require(root_authorship["integrity_valid"] and set(root_authorship["contributor_principals"]) == expected_contributors
             and root_authorship["validated_proposals"] == 4, "nested provenance lost an author or intermediate integrator")
    stage("nested-transitive-provenance", "The root pins a four-proposal ancestry and all four contributors, including the intermediate integrator.",
          composite, main_id)

    store.enqueue("authorship-compose-legacy", "integrator", {
        "components": [{"proposal_id": legacy["proposal_id"], "digest": legacy["digest"]}],
        "rationale": "Attempt to introduce a legacy component without an authenticated authorship proof.",
    })
    legacy_compose_task = store.claim_task(INTEGRATOR_A, credential=integrator_a, lease_seconds=300)
    _require(legacy_compose_task is not None and legacy_compose_task["task_id"] == "authorship-compose-legacy", "the legacy composition task was unavailable")
    legacy_compose_rejection = expected_exception("compose-unproven-legacy-component", lambda: store.compose(
        legacy_compose_task["task_id"], INTEGRATOR_A, legacy_compose_task["token"], credential=integrator_a),
        "authorship_provenance_invalid")
    stage("unproven-component-rejected", "Composition requires an existing valid proof for every direct and transitive source.",
          legacy_compose_rejection, legacy["proposal_id"])

    contributor_rejections = [
        expected_exception("original-author-cannot-formally-review-root", lambda: store.review(
            main_id, credential=author_as_reviewer), "authorship_contributor_separated"),
        expected_exception("intermediate-integrator-cannot-formally-review-root", lambda: store.review(
            main_id, credential=integrator_as_reviewer), "authorship_contributor_separated"),
    ]
    for verdict in ("approve", "request_changes", "abstain"):
        contributor_rejections.append(expected_exception("original-author-cannot-decide-" + verdict, lambda verdict=verdict: store.governance_decide(
            main_id, CODE_ID, verdict, "A different write and a new reviewer grant cannot hide contribution to the root.",
            credential=author_as_reviewer, expected_proposal_digest=composite["digest"],
            expected_policy_generation=generation, expected_previous_decision=None,
            operation_id="contributor-" + verdict.replace("_", "-")), "authorship_contributor_separated"))
    stage("contributors-separated-globally", "New role grants cannot let source authors or intermediate integrators review the root; exclusion covers every write and verdict.",
          contributor_rejections, main_id)
    contributor_accept = accept("intermediate-integrator-cannot-coordinate-root", composite, accepted=False,
                                token=integrator_as_coordinator, expected_code="authorship_coordinator_contributor")
    no_quorum = accept("unreviewed-root-not-accepted", composite, accepted=False, expected_code="governance_quorum_missing")
    stage("contributor-coordinator-blocked", "A separate coordinator grant for an intermediate integrator remains excluded; an unrelated coordinator still needs formal review and every editorial quorum.",
          {"contributor_attempt": contributor_accept, "unreviewed_attempt": no_quorum}, main_id)

    root_review = store.review(main_id, credential=reviewer_a)
    competing = authors["competing-document"]
    competing_review = store.review(competing["proposal_id"], credential=reviewer_a)
    _require(root_review["approved"] and competing_review["approved"], "credentialed formal review failed before acceptance")
    for artifact_id, suffix in ((DOC_ID, "document"), (CODE_ID, "code")):
        decide("root-reviewer-a-" + suffix, reviewer_a, "local-reviewer-a", artifact_id, composite)
    one_reviewer = accept("one-reviewer-cannot-satisfy-two-principal-policy", composite, accepted=False,
                          expected_code="governance_quorum_missing")
    stage("formal-review-and-first-reviewer", "A valid formal review and one reviewer per write cannot meet the two-principal policy.",
          {"formal_review": root_review, "acceptance_attempt": one_reviewer}, main_id)
    for artifact_id, suffix in ((DOC_ID, "document"), (CODE_ID, "code")):
        decide("root-reviewer-b-" + suffix, reviewer_b, "local-reviewer-b", artifact_id, composite)
    for suffix, credential, principal in (("a", reviewer_a, "local-reviewer-a"), ("b", reviewer_b, "local-reviewer-b")):
        decide("competing-reviewer-" + suffix, credential, principal, DOC_ID, competing)
    eligible = store.governance_observe(main_id, credential=coordinator, expected_policy_generation=generation)
    _require(eligible["eligible"], "the root is unexpectedly ineligible after complete noncontributor approvals")
    stage("current-approvals-observed", "Two noncontributor principal IDs approve each write under generation 2; the observation does not itself accept the root.",
          eligible, main_id)

    accepted = accept("atomic-authored-acceptance", composite, accepted=True)
    receipt = accepted["governance_receipt"]
    _require(accepted["revision"] == 2 and not accepted["replayed"] and receipt["schema_version"] == 2,
             "the authored root did not advance exactly once with a schema-2 receipt")
    _require(receipt["authorship"]["authorship_id"] == composite["authorship"]["authorship_id"]
             and receipt["authorship"]["authorship_digest"] == composite["authorship"]["authorship_digest"],
             "the acceptance receipt does not pin the exact root authorship proof")
    stage("accepted-with-provenance-receipt", "Atomic acceptance checks runtime guards, formal review, current local authority, noncontributor quorums and exact authorship; the receipt pins the proof.",
          accepted, main_id)
    event_count_before_replay = len(store.events())
    compose_replay = store.compose(root_task["task_id"], INTEGRATOR_B, root_task["token"], credential=integrator_b)
    accepted_replay = accept("authorized-historical-acceptance-replay", composite, accepted=True)
    _require(compose_replay["replayed"] and compose_replay["proposal_id"] == main_id
             and compose_replay["authorship"] == composite["authorship"], "completed composition replay changed its proof")
    _require(accepted_replay["replayed"] and accepted_replay["revision"] == 2
             and accepted_replay["governance_receipt"] == receipt and len(store.events()) == event_count_before_replay,
             "historical replays created new runtime events or a different receipt")
    stage("completed-replays-preserve-proof", "Current original integrator authority can replay a completed composition despite stale source reads; coordinator replay returns the original receipt without new events.",
          {"composition": compose_replay, "acceptance": accepted_replay}, main_id)
    legacy_blocked = accept("legacy-candidate-has-no-retroactive-proof", legacy, accepted=False,
                            expected_code="authorship_provenance_invalid")
    competing_blocked = accept("authored-competing-candidate-still-has-stale-read", competing, accepted=False,
                               expected_code="stale_read")
    _require(store.authorship_status(competing["proposal_id"])["integrity_valid"], "a stale runtime read corrupted historical authorship")
    stage("historical-proofs-and-runtime-guards", "Legacy proposals receive no retroactive proof; a proven and approved competing proposal still cannot override stale_read.",
          {"legacy": legacy_blocked, "competing": competing_blocked}, competing["proposal_id"])

    final_status = store.status()
    final_authorship = store.authorship_status()
    final_governance = store.governance_status()
    events = store.events()
    _require(final_status["revision"] == 2 and final_status["proposals"].get("accepted") == 1,
             "unexpected final accepted count or runtime revision")
    _require(all(item["integrity_valid"] for item in final_authorship["records"]), "a persisted proof failed final validation")
    proposal_results = {"legacy": legacy, **authors, "reclaimed": reclaimed,
                        "intermediate": intermediate, "root": composite}
    proposals = {label: store.proposal(item["proposal_id"]) for label, item in proposal_results.items()}
    for name, value in {
        "initial-state.json": initial_snapshot, "final-state.json": store.snapshot(),
        "proposals.json": proposals, "authors.json": authors,
        "composition.json": {"intermediate": intermediate, "root": composite},
        "authorship-final.json": final_authorship, "root-authorship.json": store.authorship_status(main_id),
        "governance-final.json": final_governance, "grants.json": grants,
        "decisions.json": decision_history, "operations.json": operations, "events.json": events,
        "formal-reviews.json": {"legacy_before_enable": legacy_review, "root": root_review, "competing": competing_review},
        "activation-and-regrant.json": {"governance_enabled": governance_enabled, "authorship_enabled": enabled,
                                        "original_author_revoked": original_revocation, "old_attempt": old_attempt,
                                        "new_attempt": new_attempt, "reclaimed": reclaimed},
        "replays.json": {"composition": compose_replay, "acceptance": accepted_replay},
        "blocked-candidates.json": {"legacy": legacy_blocked, "competing": competing_blocked},
    }.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        "kind": "local_authenticated_authorship_product_example", "workers": workers,
        "actors": {"authenticated_local_capability_principals": True, "legacy_author_name_self_declared": True,
                   "persons_authenticated": False, "human_review_recorded": False,
                   "independent_people_or_models_established": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "policy": policy, "policy_generation": generation, "stages": stages,
        "initial_runtime_revision": initial_snapshot["revision"], "final_runtime_status": final_status,
        "summary": {"author_job_count": len(authors),
                    "registered_author_principal_count": len({item["principal_id"] for item in authors.values()}),
                    "composite_proposal_count": 2, "root_transitive_contributor_count": len(expected_contributors),
                    "root_validated_proposal_count": root_authorship["validated_proposals"],
                    "authenticated_proof_count": len(final_authorship["records"]),
                    "legacy_unproven_proposal_count": 1, "decision_count": len(decision_history),
                    "stage_count": len(stages),
                    "expected_api_rejection_count": sum(item.get("rejected", False) for item in operations),
                    "blocked_acceptance_count": sum(item.get("result", {}).get("accepted") is False for item in operations),
                    "runtime_event_count": len(events), "accepted_proposal_count": final_status["proposals"].get("accepted", 0),
                    "acceptance_receipt_schema": receipt["schema_version"], "release_created": False,
                    "credential_material_exported": False, "measured_capacity": False,
                    "semantic_equivalence_proved": False},
        "credential_file_layout": {"administrator": "private/admin.credential",
                                   "active_coordinator": "private/coordinator.credential",
                                   "active_document_author": "private/document-author-current.credential",
                                   "active_code_author": "private/code-author.credential",
                                   "active_root_integrator": "private/integrator.credential",
                                   "active_reviewer_a": "private/reviewer-a.credential",
                                   "active_reviewer_b": "private/reviewer-b.credential",
                                   "private_database": "private/state.sqlite3"},
        "limitations": [
            "The SQLite owner and local clock are trusted; local digests do not provide external attestation.",
            "A bearer credential proves possession of a local grant, not a person's identity, SSO, model identity or expertise.",
            "Different registered principal IDs do not establish independent people, models, organizations or evidence.",
            "The proof records authority at submission and exact component ancestry; it does not prove how the text was created.",
            "Legacy proposals receive no backfilled proof; activation also advances the editorial policy generation.",
            "Historical proofs survive later grant revocation, while protected mutation and replay require the current original grant.",
            "All reviews and decisions are programmed local examples, with no human review or external LLM call.",
            "The code is stored data and may be parsed by formal guards; it is never executed by this workflow.",
            "Contributor separation is global to the candidate, not limited to the files personally written by a contributor.",
            "Authority, provenance and voting do not establish prose truth, code behavior, semantic completeness or external publication permission.",
            "Sequential public snapshots are observations; Store.accept is the transaction boundary for new acceptance.",
            "Configured Python workers are not a measurement of throughput, supported agent count or LLM capacity.",
            "Credentials, credential hashes and SQLite stay private; no release or Git publication is created by this command.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример локально удостоверенного авторства", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Три задания очереди выполнили два зарегистрированных принципала авторов. Первый интегратор объединил",
        "документ и код; второй создал вложенную композицию. Корневой кандидат сохраняет обоих авторов и обоих",
        "интеграторов: четыре принципала и четыре предложения в проверенной цепочке происхождения.", "",
        "Допуск удостоверяет владение локальным секретом. Он не устанавливает личность человека, идентичность",
        "модели, независимость участников или фактический способ написания текста. Все роли запрограммированы.", "",
        "## Последовательность", "",
        "| Состояние | Наблюдение | Ревизия |", "|---|---|---:|",
    ]
    for item in stages:
        lines.append(f"| [{item['label']}]({item['artifact']}) | {item['explanation']} | {item['runtime_revision']} |")
    lines += [
        "", "## Практический результат", "",
        "Включение авторства повышает поколение политики с 1 до 2 и не приписывает авторство прежнему предложению.",
        "Новый claim требует действующего допуска автора и точного principal_id. Аренда связывается с конкретным",
        "grant; новый секрет того же принципала не продолжает старую попытку и не переименовывает завершённую.",
        "После fencing reclaim новая попытка получает новый token и новое доказательство авторства.", "",
        "Исходный допуск автора документа отозван до композиции и принятия. Его историческое доказательство",
        "сохраняется: проверяется действительность допуска в момент записи. Повтор изменяющего API с отозванным",
        "допуском запрещён; чтение публичной истории не требует его восстановления.", "",
        "Новые reviewer/coordinator grants для автора и промежуточного интегратора не обходят разделение участников.",
        "Автор документа не может голосовать даже по записи кода этой композиции. Запрет относится к approve,",
        "request_changes и abstain, а также к формальному review и новому принятию координатором.", "",
        "Корневой кандидат получил формальную проверку и по два одобрения от разных непричастных principal_id",
        "для каждой записи. Принятие выполнилось один раз: ревизия 1 → 2. Квитанция schema 2 закрепила точный",
        "authorship_id и digest. Разрешённые повторы вернули прежний результат без новых событий и доказательств.", "",
        "Прежний кандидат остался без ретроспективного доказательства. Другой удостоверенный и одобренный",
        "документ заблокирован stale_read: полномочия и происхождение не отменяют прежние проверки runtime.", "",
        "## Публичные материалы", "",
        "- [Полный отчёт](report.json), [предложения](proposals.json), [авторы очереди](authors.json).",
        "- [Композиции](composition.json), [корневое авторство](root-authorship.json), [все доказательства и аренды](authorship-final.json).",
        "- [Операции и отклонения](operations.json), [решения](decisions.json), [формальные проверки](formal-reviews.json).",
        "- [Полномочия без секретов](grants.json), [политика и квитанции](governance-final.json), [активация и повторная выдача](activation-and-regrant.json).",
        "- [Повторы](replays.json), [заблокированные кандидаты](blocked-candidates.json), [события](events.json).",
        "- [Начальное состояние](initial-state.json), [конечное состояние](final-state.json).", "",
        "## Границы", "",
        "SQLite и локальные часы доверенные. Это не внешнее заверение, SSO, распределённая идентичность или",
        "проверка независимости людей и моделей. Голоса и цепочка происхождения не доказывают смысловую",
        "эквивалентность, истинность текста, полноту документации или поведение программы. Предлагаемый код",
        "сохраняется как данные и не исполняется. Публикация Git и выпуск остаются отдельными действиями.", "",
        "Частные файлы credential имеют права 0600 и находятся в каталоге private/ с правами 0700 вместе с SQLite.",
        "Каталог private/ не входит в публичные артефакты. Публичный числовой token — fencing номер аренды,",
        "а не bearer secret. Секреты и их хеши не публикуются.", "",
        f"Настроено Python workers: {workers}; заданий авторов: {len(authors)}; решений: {len(decision_history)}; событий runtime: {len(events)}.",
        "Размер пула не является измеренной ёмкостью сервиса или числом внешних LLM.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
