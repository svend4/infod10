"""Run an inspectable local workflow for bounded continuity of review decisions.

The documents and literal Python configurations are fictional proposed data.
Actors are programmed local principals; candidate Python is never executed.
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
from .store import Store, WorkbenchError, canonical


DOC_A = "docs/continuity-a.md"
CODE_A = "code/continuity_a.py"
DOC_D = "docs/continuity-d.md"
CODE_D = "code/continuity_d.py"
QUOTE = "The fictional sample limit is declared below."
DOCUMENT_AUTHOR = "continuity-document-author"
CODE_AUTHOR = "continuity-code-author"
INTEGRATOR = "continuity-integrator"
REVIEWER_A = "continuity-reviewer-a"
REVIEWER_B = "continuity-reviewer-b"
REVIEWER_C = "continuity-reviewer-c"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Review continuity demonstration stopped: " + message)


def _file(identifier: str, kind: str, template: str) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "template": template, "bindings": []}


def _document(identifier: str, limit: int) -> dict[str, Any]:
    return _file(identifier, "document", "# Fictional continuity contract\n\n" + QUOTE
                 + '\n\n```json agentwiki:contract\n{"limit":' + str(limit) + "}\n```\n")


def _bundle(document_id: str, code_id: str) -> dict[str, Any]:
    obligation = {
        "id": "sample-limit", "description": "The fictional literal sample limit must match.",
        "scope": {"sample": "continuity-v1"}, "relation": "equals", "value_type": "integer",
        "document": {"artifact_id": document_id, "fence_id": "contract", "path": ["limit"]},
        "code": {"artifact_id": code_id, "symbol": "SAMPLE_LIMIT"},
    }
    return {
        "schema_version": 1, "artifact_ids": [document_id, code_id],
        "spec": {"schema_version": 1, "obligations": [obligation]},
        "inventory": {"schema_version": 1, "promises": [{
            "id": "sample-promise", "description": "The declared fictional sample limit.",
            "anchor": {"artifact_id": document_id, "quote": QUOTE},
            "requirement": {key: deepcopy(obligation[key]) for key in
                            ("scope", "relation", "value_type", "document", "code")},
            "obligation_ids": ["sample-limit"],
        }]},
    }


def _catalog() -> dict[str, Any]:
    return {"schema_version": 2, "sections": [
        {"id": "section-a", "policy": _bundle(DOC_A, CODE_A)},
        {"id": "section-d", "policy": _bundle(DOC_D, CODE_D)},
    ]}


def _scope_edit(policy: dict[str, Any], section_id: str, value: str) -> dict[str, Any]:
    changed = deepcopy(policy)
    section = next(item for item in changed["sections"] if item["id"] == section_id)
    section["policy"]["spec"]["obligations"][0]["scope"]["revision"] = value
    section["policy"]["inventory"]["promises"][0]["requirement"]["scope"]["revision"] = value
    return changed


def run_continuity_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save one ordinary local workflow; existing material is never replaced."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Continuity demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    private = workspace / "private"
    private.mkdir(mode=0o700)
    credentials: dict[str, str] = {}

    def new_credential(label: str) -> str:
        secret = prepare_credential(private / (label + ".credential"))
        credentials[label] = secret
        return secret

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            result = {}
            for key, child in value.items():
                lowered = key.lower()
                if lowered in {"credential", "credential_hash", "credential_digest", "token_hash", "token_digest"}:
                    continue
                if ("credential" in lowered or "secret" in lowered) and ("hash" in lowered or "digest" in lowered):
                    continue
                result[key] = public(child)
            return result
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            for secret in credentials.values():
                _require(secret not in value and hashlib.sha256(secret.encode("ascii")).hexdigest() not in value,
                         "a public artifact contains private credential material")
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
    enabled_governance = store.governance_enable(editorial_policy, "continuity-administrator", admin)
    generation = 1
    expires_at = time.time() + 3600
    grants: dict[str, dict[str, Any]] = {}
    all_artifacts = [DOC_A, CODE_A, DOC_D, CODE_D]

    def grant(label: str, principal: str, role: str, artifact_ids: list[str]) -> str:
        secret = new_credential(label)
        grants[label] = store.governance_grant(
            principal, role, artifact_ids, expires_at, credential=admin,
            new_credential=secret, operation_id="continuity-grant-" + label)
        return secret

    document_author = grant("document-author", DOCUMENT_AUTHOR, "doc_author", [DOC_A, DOC_D])
    code_author = grant("code-author", CODE_AUTHOR, "code_author", [CODE_A, CODE_D])
    integrator = grant("integrator", INTEGRATOR, "integrator", all_artifacts)
    reviewer_a = grant("reviewer-a", REVIEWER_A, "reviewer", all_artifacts)
    reviewer_b_original = grant("reviewer-b-original", REVIEWER_B, "reviewer", all_artifacts)
    reviewer_b = reviewer_b_original
    reviewer_c = grant("reviewer-c", REVIEWER_C, "reviewer", all_artifacts)
    coordinator = grant("coordinator", "continuity-coordinator", "coordinator", all_artifacts)
    enabled_authorship = store.authorship_enable(credential=admin, expected_policy_generation=generation)
    generation = enabled_authorship["policy_generation"]
    operations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    decisions: dict[tuple[str, str], dict[str, Any]] = {}
    decision_history: list[dict[str, Any]] = []
    decision_requests: dict[str, dict[str, Any]] = {}
    policy_updates: list[dict[str, Any]] = []
    packets: dict[str, dict[str, Any]] = {}
    observations: dict[str, dict[str, Any]] = {}

    def expected_exception(label: str, operation: Callable[[], Any], expected_code: str) -> dict[str, Any]:
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == expected_code, "unexpected API rejection for " + label + ": " + error.code)
            result = {"label": label, "rejected": True, "code": error.code}
            operations.append(result)
            return result
        raise RuntimeError("Review continuity demonstration stopped: expected rejection for " + label)

    bodies = {"document-a": _document(DOC_A, 4),
              "code-a": _file(CODE_A, "code", "SAMPLE_LIMIT = 4\n"),
              "alternative-document": _document(DOC_A, 5)}
    for label, body in bodies.items():
        role = "doc_author" if body["kind"] == "document" else "code_author"
        store.enqueue("continuity-author-" + label, role, {
            "proposal": _proposal(initial_snapshot, body,
                                  "A fictional local candidate; proposed Python remains unexecuted data.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        scheduled = list(bodies)[index]
        role = "doc_author" if bodies[scheduled]["kind"] == "document" else "code_author"
        principal, secret = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author"
                             else (CODE_AUTHOR, code_author))
        task = store.claim_task(principal, roles=[role], credential=secret, lease_seconds=300)
        _require(task is not None, "an authenticated author task was unavailable")
        submitted = store.submit(task["task_id"], principal, task["token"],
                                 task["payload"]["proposal"], credential=secret)
        return {"label": task["task_id"].removeprefix("continuity-author-"),
                "principal_id": principal, "thread": current_thread().name,
                "task_id": task["task_id"], "fencing_token": task["token"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="continuity-author") as executor:
        futures = [executor.submit(author_job, index) for index in range(len(bodies))]
        authors = {item["label"]: item for item in (future.result() for future in futures)}
    store.enqueue("continuity-compose-a", "integrator", {
        "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                       for label in ("document-a", "code-a")],
        "rationale": "Compose the exact fictional document and literal code configuration.",
    })
    lease = store.claim_task(INTEGRATOR, roles=["integrator"], credential=integrator, lease_seconds=300)
    _require(lease is not None, "the integrator lease was unavailable")
    main = store.compose(lease["task_id"], INTEGRATOR, lease["token"], credential=integrator)
    main_id = main["proposal_id"]
    formal_review = store.review(main_id, credential=reviewer_a)
    _require(formal_review["approved"], "the ordinary formal review did not approve the fictional candidate")

    def packet() -> dict[str, Any]:
        return store.content_status(main_id)["packet"]

    def observe(label: str) -> dict[str, Any]:
        result = store.governance_observe(main_id, credential=coordinator,
                                          expected_policy_generation=generation)
        observations[label] = result
        return result

    def stage(label: str, explanation: str, result: Any = None) -> None:
        filename = f"stage-{len(stages) + 1:02d}-{label}.json"
        body = {"label": label, "explanation": explanation,
                "runtime_revision": store.status()["revision"],
                "content": store.content_status(main_id),
                "continuity": store.continuity_status(main_id), "operation_result": result}
        write_json(filename, body)
        stages.append({"label": label, "explanation": explanation, "artifact": filename,
                       "runtime_revision": body["runtime_revision"]})

    def decide(label: str, secret: str, principal: str, artifact_id: str,
               verdict: str = "approve") -> dict[str, Any]:
        previous = decisions.get((principal, artifact_id))
        content_packet = packet()
        request = {
            "proposal_id": main_id, "artifact_id": artifact_id, "verdict": verdict,
            "rationale": "Programmed local consideration for " + label
                         + "; no human understanding, prose truth or program behavior is asserted.",
            "expected_proposal_digest": main["digest"], "expected_policy_generation": generation,
            "expected_previous_decision": previous["decision_id"] if previous else None,
            "expected_content_packet_digest": content_packet["packet_digest"],
            "operation_id": "continuity-decision-" + label,
        }
        result = store.governance_decide(**request, credential=secret)
        decisions[(principal, artifact_id)] = result
        decision_requests[label] = request
        decision_history.append({"label": label, "result": result})
        return result

    def approve(label: str) -> list[str]:
        identifiers = []
        for suffix, secret, principal in (("a", reviewer_a, REVIEWER_A), ("b", reviewer_b, REVIEWER_B)):
            for index, artifact_id in enumerate((DOC_A, CODE_A), 1):
                result = decide(label + "-" + suffix + "-" + str(index), secret, principal, artifact_id)
                identifiers.append(result["decision_id"])
        return identifiers

    def valid_ids(observation: dict[str, Any], verdict: str = "approve") -> set[str]:
        return {item["decision_id"] for item in observation["decisions"]
                if item["valid"] and item["verdict"] == verdict}

    def accept(label: str, expected: bool, blocker: str | None = None) -> dict[str, Any]:
        result = store.accept(main_id, credential=coordinator, expected_policy_generation=generation)
        _require(result["accepted"] is expected, "unexpected acceptance outcome for " + label)
        if blocker:
            _require(any(item["code"] == blocker for item in result["reasons"]),
                     "the expected acceptance blocker is absent for " + label)
        operations.append({"label": label, "result": result})
        return result

    def update_catalog(label: str, descriptor: dict[str, Any]) -> dict[str, Any]:
        nonlocal generation, active_catalog
        changed = store.content_set_policy(descriptor, credential=admin,
                                           expected_policy_generation=generation)
        generation = changed["policy_generation"]
        active_catalog = deepcopy(changed["current_policy"]["policy"])
        policy_updates.append({"label": label, "content_policy_generation": changed["content_policy_generation"],
                               "governance_policy_generation": generation,
                               "content_policy_digest": changed["content_policy_digest"]})
        return changed

    stage("authenticated-candidate", "Three author jobs and one composition retain local provenance; the code is stored data.",
          {"authors": authors, "composition": main, "formal_review": formal_review})
    enabled_content = store.content_enable(active_catalog, credential=admin,
                                           expected_policy_generation=generation)
    generation = enabled_content["policy_generation"]
    active_catalog = deepcopy(enabled_content["current_policy"]["policy"])
    preactivation_packet = packet()
    preactivation_decision = decide("before-activation-c-document", reviewer_c, REVIEWER_C, DOC_A)
    _require(preactivation_decision["schema_version"] == 2
             and "review_continuity" not in preactivation_decision,
             "the preactivation opinion unexpectedly received a continuity stamp")
    packets["before-activation"] = preactivation_packet
    stage("preactivation-opinion", "An ordinary content-pinned opinion is saved before continuity; it receives no future stamp retroactively.",
          preactivation_decision)

    enabled_continuity = store.continuity_enable(credential=admin, expected_policy_generation=generation)
    generation = store.governance_status()["policy_generation"]
    _require(generation == 4, "continuity activation did not create the expected fresh governance generation")
    activated = observe("activated")
    _require(not activated["eligible"]
             and not any(item["valid"] for item in activated["decisions"]),
             "a preactivation opinion acquired continuity authority")
    expected_exception("schema-one-downgrade", lambda: store.content_set_policy(
        _bundle(DOC_A, CODE_A), credential=admin, expected_policy_generation=generation),
        "continuity_schema_required")
    stage("continuity-activated", "Explicit activation advances governance and requires fresh decisions; schema-one downgrade is refused.",
          {"activation": enabled_continuity, "observation": activated})

    initial_ids = approve("initial")
    initial_packet = packet()
    initial_observation = observe("initial-approved")
    initial_basis = initial_observation["review_continuity"]["basis_digest"]
    _require(initial_observation["eligible"] and valid_ids(initial_observation) == set(initial_ids)
             and all(item["result"]["schema_version"] == 3 for item in decision_history[1:]),
             "the exact-packet decisions were not eligible in the explicit mode")
    packets["initial-approved"] = initial_packet
    bad_request = dict(decision_requests["initial-a-1"])
    bad_request.update(operation_id="continuity-decision-wrong-packet",
                       expected_content_packet_digest="0" * 64)
    expected_exception("wrong-current-packet", lambda: store.governance_decide(
        **bad_request, credential=reviewer_a), "content_packet_changed")
    expected_exception("missing-reviewer-credential", lambda: store.governance_decide(
        **decision_requests["initial-a-1"], credential=None), "governance_unauthorized")
    stage("exact-current-approvals", "Two local reviewers pin each write and its local basis; a wrong packet or absent grant is rejected.",
          initial_observation)

    first_update = update_catalog("unrelated-d-first", _scope_edit(active_catalog, "section-d", "unrelated-first"))
    preserved = observe("unrelated-preserved")
    preserved_packet = packet()
    _require(preserved["eligible"] and valid_ids(preserved) == set(initial_ids)
             and preserved_packet["packet_digest"] != initial_packet["packet_digest"]
             and preserved["review_continuity"]["basis_digest"] == initial_basis,
             "an unrelated catalog update did not retain the original exact local basis and decision IDs")
    packets["unrelated-preserved"] = preserved_packet
    stage("unrelated-catalog-keeps-votes", "D changes the full packet, but A's local basis and four original approvals remain current without new votes.",
          {"policy_update": first_update, "observation": preserved})

    first_objection = decide("objection-before-unrelated-edit", reviewer_c, REVIEWER_C, DOC_A, "request_changes")
    veto_before = observe("objection-before-unrelated-edit")
    _require(first_objection["decision_id"] in valid_ids(veto_before, "request_changes"),
             "the current objection was not active")
    veto_blocked = accept("current-objection-blocks", False, "governance_changes_requested")
    stage("current-objection-blocks", "A third current reviewer records request_changes; the existing quorum cannot bypass the veto.",
          {"observation": veto_before, "acceptance": veto_blocked})

    second_update = update_catalog("unrelated-d-second", _scope_edit(active_catalog, "section-d", "unrelated-second"))
    veto_after = observe("objection-after-unrelated-edit")
    _require(first_objection["decision_id"] in valid_ids(veto_after, "request_changes")
             and valid_ids(veto_after) == set(initial_ids),
             "unrelated catalog continuity lost an objection or rewrote the original approvals")
    veto_still_blocked = accept("preserved-objection-blocks", False, "governance_changes_requested")
    stage("objection-survives-unrelated-edit", "The same continuity rule carries request_changes and approvals through another unrelated D edit.",
          {"policy_update": second_update, "observation": veto_after, "acceptance": veto_still_blocked})

    released_objection = decide("objection-explicitly-replaced", reviewer_c, REVIEWER_C, DOC_A, "abstain")
    after_abstain = observe("objection-explicitly-replaced")
    _require(after_abstain["eligible"] and valid_ids(after_abstain) == set(initial_ids),
             "explicit replacement of the objection did not restore the retained quorum")
    stage("objection-explicitly-replaced", "C explicitly supersedes the objection with abstain; A/B approvals are still the original four decisions.",
          {"replacement": released_objection, "observation": after_abstain})

    revoked = store.governance_revoke(grants["reviewer-b-original"]["grant_id"], credential=admin)
    revoked_observation = observe("reviewer-b-revoked")
    _require(not revoked_observation["eligible"], "a revoked original reviewer grant retained voting authority")
    revoked_blocked = accept("revoked-reviewer-blocks", False, "governance_quorum_missing")
    stage("revoked-grant-stops-votes", "Continuity does not override a grant revocation; the original B votes stop counting.",
          {"revocation": revoked, "observation": revoked_observation, "acceptance": revoked_blocked})

    reviewer_b = grant("reviewer-b-current", REVIEWER_B, "reviewer", all_artifacts)
    regranted = observe("reviewer-b-regranted")
    _require(not regranted["eligible"] and not any(item["valid"] and item["principal_id"] == REVIEWER_B
                                                 for item in regranted["decisions"]),
             "a new grant for the same principal restored old votes")
    regrant_blocked = accept("reissued-reviewer-still-blocked", False, "governance_quorum_missing")
    expected_exception("revoked-grant-decision-replay", lambda: store.governance_decide(
        **decision_requests["initial-b-1"], credential=reviewer_b_original), "governance_unauthorized")
    stage("reissued-grant-does-not-restore", "A new B grant has the same principal but cannot reactivate decisions tied to the revoked grant.",
          {"new_grant": grants["reviewer-b-current"], "observation": regranted, "acceptance": regrant_blocked})

    fresh_b = [decide("regranted-b-" + str(index), reviewer_b, REVIEWER_B, identifier)["decision_id"]
               for index, identifier in enumerate((DOC_A, CODE_A), 1)]
    after_fresh_b = observe("fresh-regranted-b")
    _require(after_fresh_b["eligible"] and set(fresh_b).issubset(valid_ids(after_fresh_b))
             and {decisions[(REVIEWER_A, identifier)]["decision_id"] for identifier in (DOC_A, CODE_A)}.issubset(
                 valid_ids(after_fresh_b)), "fresh B opinions did not combine with the retained A opinions")
    stage("fresh-grant-decisions", "B writes two new CAS-linked opinions with the current grant; A's original approvals remain usable.",
          after_fresh_b)

    before_scope_change = deepcopy(active_catalog)
    scope_changed = update_catalog("selected-a-temporary-change", _scope_edit(active_catalog, "section-a", "temporary"))
    changed_packet = packet()
    scope_restored = update_catalog("selected-a-restored", before_scope_change)
    restored = observe("selected-context-restored")
    _require(restored["review_continuity"]["basis_digest"] == initial_basis and not restored["eligible"]
             and not valid_ids(restored),
             "a selected-scope change followed by restore revived the old decisions")
    scope_blocked = accept("selected-scope-aba-blocks", False, "governance_quorum_missing")
    packets.update({"selected-scope-temporary": changed_packet, "selected-scope-restored": packet()})
    stage("selected-change-and-return", "A changed and returned to its old local basis; every intermediate edge is checked, so the old opinions do not revive.",
          {"changed": scope_changed, "restored": scope_restored, "observation": restored, "acceptance": scope_blocked})

    fresh_after_scope = approve("after-selected-change")
    after_scope_review = observe("after-selected-change")
    _require(after_scope_review["eligible"] and valid_ids(after_scope_review) == set(fresh_after_scope),
             "fresh consideration after the selected-scope barrier was not eligible")
    stage("fresh-after-selected-change", "Fresh opinions supersede A/B heads after the selected-context barrier; old objects remain historical.",
          after_scope_review)

    direct_reset = store.governance_set_policy(deepcopy(editorial_policy), credential=admin,
                                               expected_generation=generation)
    generation = direct_reset["generation"]
    after_reset = observe("direct-governance-reset")
    _require(not after_reset["eligible"] and not valid_ids(after_reset),
             "a direct governance reset with identical policy body preserved old opinions")
    reset_blocked = accept("direct-policy-reset-blocks", False, "governance_quorum_missing")
    stage("direct-governance-reset", "An identical direct governance policy creates a generation gap without a catalog transition; fresh review is required.",
          {"policy_reset": direct_reset, "observation": after_reset, "acceptance": reset_blocked})

    fresh_after_reset = approve("after-direct-reset")
    after_reset_review = observe("fresh-after-direct-reset")
    _require(after_reset_review["eligible"] and valid_ids(after_reset_review) == set(fresh_after_reset),
             "fresh consideration did not clear the direct governance barrier")
    stage("fresh-after-direct-reset", "The current A/B grants explicitly approve the current exact packet after the governance reset.",
          after_reset_review)

    long_history_objection = decide("objection-before-history-limit", reviewer_c, REVIEWER_C, DOC_A, "request_changes")
    before_sweep = observe("objection-before-history-limit")
    _require(long_history_objection["decision_id"] in valid_ids(before_sweep, "request_changes"),
             "the objection before the bounded history sweep was not current")
    sweep_start_generation = generation
    stage("objection-before-long-history", "C records a fresh objection at the same generation as the current quorum, before the bounded catalog history grows.",
          before_sweep)

    sweep_policy = _scope_edit(active_catalog, "section-d", "bounded-history-sweep")
    sweep_updates = []
    for index in range(65):
        changed = update_catalog("bounded-unrelated-" + str(index + 1), sweep_policy)
        sweep_updates.append({"index": index + 1, "content_policy_generation": changed["content_policy_generation"],
                              "governance_policy_generation": generation,
                              "content_policy_digest": changed["content_policy_digest"]})
    sweep_result = {"catalog_updates": len(sweep_updates), "from_governance_generation": sweep_start_generation,
                    "to_governance_generation": generation, "updates": sweep_updates,
                    "interpretation": "A bounded functional history example, not a load or throughput measurement."}
    limited = observe("bounded-history-limit")
    _require(any(item["code"] == "continuity_reconsideration_required" for item in limited["reasons"]),
             "unproven current heads disappeared when the bounded history limit was exceeded")
    limited_objection = next(item for item in limited["decisions"]
                             if item["decision_id"] == long_history_objection["decision_id"])
    _require(limited_objection["current_head"]
             and limited_objection["continuity_match"]["reasons"] == ["continuity_history_limit"],
             "the long-history objection did not retain its explicit unresolved reason")
    history_blocked = accept("history-limit-blocks", False, "continuity_reconsideration_required")
    stage("bounded-history-requires-consideration", "After 65 unrelated updates, old current heads exceed the 64-edge reader and require explicit fresh consideration.",
          {"sweep": sweep_result, "observation": limited, "acceptance": history_blocked})

    fresh_after_limit = approve("after-history-limit")
    fresh_quorum_old_veto = observe("fresh-quorum-old-objection")
    _require(valid_ids(fresh_quorum_old_veto) == set(fresh_after_limit)
             and all(len(item["approval_principals"]) == 2 for item in fresh_quorum_old_veto["writes"])
             and not fresh_quorum_old_veto["eligible"]
             and any(item["code"] == "continuity_reconsideration_required"
                     for item in fresh_quorum_old_veto["reasons"]),
             "a fresh quorum bypassed C's unresolved old objection")
    still_limited = accept("fresh-quorum-cannot-bypass-history", False, "continuity_reconsideration_required")
    stage("fresh-quorum-keeps-old-objection-blocker", "A/B supply a fresh quorum, but C's unresolved current-head objection remains a proposal-level blocker.",
          {"observation": fresh_quorum_old_veto, "acceptance": still_limited})

    reconsidered = decide("objection-reconsidered-after-limit", reviewer_c, REVIEWER_C, DOC_A, "abstain")
    reconsidered_observation = observe("objection-reconsidered-after-limit")
    _require(reconsidered_observation["eligible"]
             and valid_ids(reconsidered_observation) == set(fresh_after_limit),
             "C's explicit fresh replacement did not clear the bounded-history blocker")
    stage("objection-explicitly-reconsidered", "C explicitly replaces the old head with a current abstain; the fresh A/B quorum can now be considered.",
          {"replacement": reconsidered, "observation": reconsidered_observation})

    final_update = update_catalog("final-unrelated-d", _scope_edit(active_catalog, "section-d", "final-unrelated"))
    final_observation = observe("final-preserved-quorum")
    _require(final_observation["eligible"] and valid_ids(final_observation) == set(fresh_after_limit),
             "the final unrelated transition did not preserve the newly considered quorum")
    accepted_packet = packet()
    accepted = accept("accepted-with-continuity-receipt", True)
    receipt = accepted["governance_receipt"]
    _require(accepted["revision"] == 2 and receipt["schema_version"] == 4
             and receipt["content_packet"]["packet_digest"] == accepted_packet["packet_digest"]
             and set(receipt["review_continuity"]["used_decisions"]) == set(fresh_after_limit),
             "the acceptance did not pin the retained decisions in a schema-four receipt")
    _require(all(proof["transitions"] for proof in receipt["review_continuity"]["used_decisions"].values()),
             "the final receipt omitted the actual preservation transition")
    packets["accepted"] = accepted_packet
    stage("accepted-with-preserved-quorum", "One final unrelated D transition preserves the same four A/B decisions; receipt schema 4 pins their old/current packets and transition proofs.",
          {"policy_update": final_update, "observation": final_observation, "acceptance": accepted})

    event_count_before_replays = len(store.events())
    replay_initial = store.governance_decide(**decision_requests["initial-a-1"], credential=reviewer_a)
    replay_preactivation = store.governance_decide(
        **decision_requests["before-activation-c-document"], credential=reviewer_c)
    replay_acceptance = store.accept(main_id, credential=coordinator, expected_policy_generation=generation)
    old_packet_read = store.content_packet(initial_packet["packet_id"])
    _require(replay_initial["replayed"]
             and replay_initial["decision_id"] == initial_ids[0]
             and replay_preactivation["replayed"]
             and "review_continuity" not in replay_preactivation
             and replay_acceptance["replayed"] and replay_acceptance["governance_receipt"] == receipt
             and canonical(old_packet_read) == canonical(initial_packet)
             and len(store.events()) == event_count_before_replays,
             "authorized historical replays changed a decision, packet, receipt or event count")
    replays = {"initial_decision": replay_initial, "preactivation_decision": replay_preactivation,
               "acceptance": replay_acceptance, "historical_packet": old_packet_read}
    stage("historical-replays-preserve-objects", "Current authorized replays return old objects, including an unstamped preactivation decision, without new votes or acceptance events.",
          replays)

    final_status = store.status()
    final_continuity = store.continuity_status(main_id)
    final_governance = store.governance_status(main_id)
    events = store.events()
    for name, value in {
        "initial-state.json": initial_snapshot, "final-state.json": store.snapshot(),
        "authors.json": authors, "composition.json": main, "formal-review.json": formal_review,
        "proposals.json": {"composite": store.proposal(main_id),
                           "authors": {label: store.proposal(value["proposal_id"])
                                       for label, value in authors.items()}},
        "policy.json": active_catalog, "editorial-policy.json": editorial_policy,
        "activation.json": {"governance": enabled_governance, "authorship": enabled_authorship,
                            "content": enabled_content, "continuity": enabled_continuity},
        "policy-updates.json": policy_updates, "history-sweep.json": sweep_result,
        "packets.json": packets, "observations.json": observations,
        "decisions.json": decision_history, "operations.json": operations,
        "grants.json": grants, "events.json": events, "acceptance.json": accepted,
        "continuity-final.json": final_continuity, "governance-final.json": final_governance,
        "content-final.json": store.content_status(main_id), "authorship-final.json": store.authorship_status(main_id),
        "replays.json": replays,
    }.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        "kind": "bounded_review_continuity_product_example", "workers": workers,
        "actors": {"authenticated_local_capability_principals": True, "persons_authenticated": False,
                   "human_review_recorded": False, "independent_people_or_models_established": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "main_proposal_id": main_id, "accepted_content_packet_id": accepted_packet["packet_id"],
        "initial_content_packet_id": initial_packet["packet_id"],
        "initial_review_basis_digest": initial_basis,
        "initial_approval_decision_ids": initial_ids,
        "accepted_approval_decision_ids": fresh_after_limit,
        "governance_policy_generation": generation,
        "content_policy_generation": store.content_status()["current_policy"]["generation"],
        "editorial_policy": editorial_policy, "stages": stages,
        "initial_runtime_revision": initial_snapshot["revision"], "final_runtime_status": final_status,
        "summary": {"author_job_count": len(authors), "composite_proposal_count": 1,
                    "registered_author_principal_count": len({item["principal_id"] for item in authors.values()}),
                    "root_contributor_count": len(main["authorship"]["contributor_principals"]),
                    "decision_count": len(decision_history), "stage_count": len(stages),
                    "catalog_update_count": len(policy_updates), "bounded_sweep_update_count": len(sweep_updates),
                    "expected_api_rejection_count": sum(item.get("rejected", False) for item in operations),
                    "blocked_acceptance_count": sum(item.get("result", {}).get("accepted") is False for item in operations),
                    "runtime_event_count": len(events), "accepted_proposal_count": final_status["proposals"].get("accepted", 0),
                    "acceptance_receipt_schema": receipt["schema_version"], "accepted_content_packet_schema": accepted_packet["schema_version"],
                    "unrelated_change_kept_original_decision_ids": valid_ids(preserved) == set(initial_ids),
                    "unrelated_change_kept_local_basis": preserved["review_continuity"]["basis_digest"] == initial_basis,
                    "selected_change_restore_did_not_revive_votes": not valid_ids(restored),
                    "history_limit_kept_objection_blocker": not fresh_quorum_old_veto["eligible"],
                    "final_receipt_preserves_existing_decisions": set(receipt["review_continuity"]["used_decisions"]) == set(fresh_after_limit),
                    "release_created": False, "credential_material_exported": False,
                    "measured_capacity": False, "semantic_equivalence_proved": False},
        "credential_file_layout": {"administrator": "private/admin.credential",
                                   "active_document_author": "private/document-author.credential",
                                   "active_code_author": "private/code-author.credential",
                                   "active_integrator": "private/integrator.credential",
                                   "active_reviewer_a": "private/reviewer-a.credential",
                                   "active_reviewer_b": "private/reviewer-b-current.credential",
                                   "active_reviewer_c": "private/reviewer-c.credential",
                                   "active_coordinator": "private/coordinator.credential",
                                   "private_database": "private/state.sqlite3"},
        "limitations": [
            "The SQLite owner, local clock and declared administrative policy are trusted; internal hashes are not external certification.",
            "Programmed local principal IDs and bearer grants do not authenticate people, models, expertise, independence or understanding.",
            "Local basis equality covers the complete declared packet projection, not the truth or completeness of natural-language documentation.",
            "Every recorded catalog edge is checked; selected-policy change-and-restore cannot reactivate old votes.",
            "Content-data read/query freshness remains digest-based; restoring intermediate data bytes is not a new general ABA detector.",
            "Continuity carries all verdicts only through a proven bounded path; unresolved current heads past the limit require fresh consideration or grant revocation.",
            "The 65-update sweep is a bounded functional history example, not a throughput, load, cost or scalability measurement.",
            "Proposed Python is parsed as declared data and never executed; external LLMs and human review are absent.",
            "Status and separate JSON snapshots are advisory observations; Store.accept is the transaction boundary for new acceptance.",
            "Credentials, their hashes and SQLite remain private; the command creates no release or Git publication.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример сохранения редакторских решений", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Программируемые локальные авторы предложили вымышленный документ и литеральную Python-конфигурацию.",
        "Интегратор создал один общий кандидат A. Каталог также содержит несвязанный раздел D.",
        "Проверяются целые значения JSON и SAMPLE_LIMIT с точной цитатой объявленного обещания; код не исполняется.", "",
        "## Последовательность", "", "| Состояние | Наблюдение | Ревизия |", "|---|---|---:|",
    ]
    for item in stages:
        lines.append(f"| [{item['label']}]({item['artifact']}) | {item['explanation']} | {item['runtime_revision']} |")
    lines += [
        "", "## Что произошло", "",
        "Явное включение continuity создало новое поколение governance. Прежнее решение осталось без нового",
        "штампа и не было переоформлено задним числом. Новые решения schema 3 закрепили полный пакет и локальное основание.", "",
        "Изменение D поменяло полный пакет и поколения, но сохранило локальное основание A и те же четыре одобрения.",
        "Каждый промежуточный переход проверялся отдельно. Возражение C также сохранилось после следующей правки D",
        "и блокировало принятие до явной замены на abstain. Число разных ID не устанавливает независимость людей.", "",
        "Отзыв B остановил его голоса; новый grant того же principal не оживил прежние решения. Потребовались новые",
        "CAS-связанные мнения B. Изменение выбранного A с последующим возвратом прежнего основания также потребовало",
        "свежих решений. Прямая запись идентичной governance-политики создала отдельный барьер для старых голосов.", "",
        "Затем выполнены 65 несвязанных обновлений каталога. Путь старых текущих голов превысил предел 64 перехода.",
        "Свежие одобрения A/B обеспечили кворум, но старое возражение C не исчезло: continuity_reconsideration_required",
        "сохранился как блокер всего предложения. Только явное свежее abstain C устранило этот блокер.", "",
        "Последняя несвязанная правка D сохранила четыре уже существующих одобрения. Принятие прошло один раз:",
        "ревизия 1 → 2. Квитанция schema 4 закрепила использованные decision IDs, старые и текущий пакеты и",
        "хешированную цепочку перехода. Хеш цепочки не является внешней подписью или доказательством личности.", "",
        "Исторические повторы вернули прежние объекты, включая решение до активации, без новых голосов и событий",
        "принятия. Сохранённый исходный пакет прочитан после принятия без изменения тела.", "",
        "## Публичные материалы", "",
        "- [Полный отчёт](report.json), [активация](activation.json), [итоговая политика](policy.json).",
        "- [Авторы](authors.json), [композиция](composition.json), [предложения](proposals.json), [формальная проверка](formal-review.json).",
        "- [Пакеты](packets.json), [наблюдения](observations.json), [решения](decisions.json), [операции](operations.json).",
        "- [Обновления каталога](policy-updates.json), [ограниченная последовательность из 65 обновлений](history-sweep.json).",
        "- [Принятие и квитанция](acceptance.json), [повторы](replays.json), [события](events.json).",
        "- [История continuity](continuity-final.json), [governance](governance-final.json), [content](content-final.json), [происхождение](authorship-final.json).",
        "- [Начальное состояние](initial-state.json), [конечное состояние](final-state.json), [допуски без секретов](grants.json).", "",
        "## Границы", "",
        "Проверяется непрерывность объявленных оснований и локальных допусков. Истинность свободного текста, полнота",
        "описи, поведение программы, человеческое понимание и независимость людей или моделей не установлены.",
        "Внешние LLM и человеческие решения отсутствуют; предложенный код сохраняется как данные и не исполняется.",
        "SQLite, часы и административная политика доверенные. Данные read/query по-прежнему сравниваются по digest;",
        "новое правило не обнаруживает все промежуточные изменения восстановленных байтов данных.", "",
        "История ограничена 64 переходами на решение. Непроверенная действующая голова требует свежего рассмотрения",
        "либо отзыва допуска; один свежий кворум не устраняет незавершённое старое возражение. Последовательность",
        "из 65 обновлений показывает этот договор, без измерения производительности или ёмкости портала.", "",
        "private/ имеет права 0700; .credential — 0600. Секреты, их хеши и SQLite не публикуются. Отдельные снимки",
        "JSON последовательны; новая приёмка выполняется в Store.accept. Git и выпуск остаются отдельными действиями.", "",
        f"Настроено Python workers: {workers}; авторских заданий: {len(authors)}; решений: {len(decision_history)}; обновлений каталога: {len(policy_updates)}; событий: {len(events)}.",
        "Размер пула и этот сценарий не являются измеренной ёмкостью внешних агентов.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
