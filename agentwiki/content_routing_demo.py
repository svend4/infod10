"""Run a bounded declared-section workflow with authenticated local actors.

The fictional documents and literal Python declarations are proposed data.
No candidate Python is executed and no external model or person is consulted.
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


DOC_A = "docs/routing-a.md"
DOC_B = "docs/routing-b.md"
DOC_D = "docs/routing-d.md"
CODE_SHARED = "code/routing_shared.py"
CODE_C = "code/routing_c.py"
CODE_D = "code/routing_d.py"
OUTSIDE = "docs/routing-outside.md"
QUOTE = "The fictional sample limit is declared below."
DOCUMENT_AUTHOR = "routing-document-author"
CODE_AUTHOR = "routing-code-author"
INTEGRATOR = "routing-integrator"
REVIEWER_A = "routing-reviewer-a"
REVIEWER_B = "routing-reviewer-b"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Content routing demonstration stopped: " + message)


def _file(identifier: str, kind: str, template: str) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "template": template, "bindings": []}


def _document(identifier: str, limit: int) -> dict[str, Any]:
    return _file(identifier, "document", "# Fictional routed sample\n\n" + QUOTE
                 + '\n\n```json agentwiki:contract\n{"limit":' + str(limit) + "}\n```\n")


def _bundle(document_id: str, code_id: str) -> dict[str, Any]:
    """Reuse local obligation IDs deliberately; the section is their namespace."""
    obligation = {
        "id": "sample-limit", "description": "The fictional literal sample limit must match.",
        "scope": {"sample": "routing-v1"}, "relation": "equals", "value_type": "integer",
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
        {"id": "section-a", "policy": _bundle(DOC_A, CODE_SHARED)},
        {"id": "section-b", "policy": _bundle(DOC_B, CODE_SHARED)},
        {"id": "section-c", "policy": _bundle(DOC_B, CODE_C)},
        {"id": "section-d", "policy": _bundle(DOC_D, CODE_D)},
    ]}


def run_routing_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save one ordinary product workflow, keeping authority in private files."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Routing demo needs a new empty workspace; existing data is preserved")
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
    store.bootstrap([_document(identifier, 3) for identifier in (DOC_A, DOC_B, DOC_D)]
                    + [_file(identifier, "code", "SAMPLE_LIMIT = 3\n")
                       for identifier in (CODE_SHARED, CODE_C, CODE_D)])
    initial_snapshot = store.snapshot()
    editorial_policy = {"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True}
    legacy_policy = _bundle(DOC_D, CODE_D)
    routing_policy = _catalog()
    admin = new_credential("admin")
    governance_enabled = store.governance_enable(editorial_policy, "routing-administrator", admin)
    generation = 1
    grants: dict[str, dict[str, Any]] = {}
    expires_at = time.time() + 3600
    document_ids = [DOC_A, DOC_B, DOC_D, OUTSIDE]
    code_ids = [CODE_SHARED, CODE_C, CODE_D]
    all_artifacts = document_ids + code_ids

    def grant(label: str, principal: str, role: str, artifact_ids: list[str]) -> str:
        secret = new_credential(label)
        grants[label] = store.governance_grant(
            principal, role, artifact_ids, expires_at, credential=admin,
            new_credential=secret, operation_id="routing-grant-" + label)
        return secret

    document_author = grant("document-author", DOCUMENT_AUTHOR, "doc_author", document_ids)
    code_author = grant("code-author", CODE_AUTHOR, "code_author", code_ids)
    integrator = grant("integrator", INTEGRATOR, "integrator", all_artifacts)
    reviewer_a = grant("reviewer-a", REVIEWER_A, "reviewer", all_artifacts)
    reviewer_b = grant("reviewer-b", REVIEWER_B, "reviewer", all_artifacts)
    coordinator = grant("coordinator", "routing-coordinator", "coordinator", all_artifacts)
    authorship_enabled = store.authorship_enable(credential=admin, expected_policy_generation=generation)
    generation = authorship_enabled["policy_generation"]
    _require(generation == 2, "authorship activation did not advance generation")
    operations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    decision_history: list[dict[str, Any]] = []
    decision_requests: dict[str, dict[str, Any]] = {}
    decisions: dict[tuple[str, str, str], dict[str, Any]] = {}
    packet_snapshots: dict[str, dict[str, Any]] = {}
    formal_reviews: dict[str, dict[str, Any]] = {}

    def expected_exception(label: str, operation: Callable[[], Any], expected_code: str) -> dict[str, Any]:
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == expected_code, "unexpected API rejection for " + label + ": " + error.code)
            result = {"label": label, "rejected": True, "code": error.code}
            operations.append(result)
            return result
        raise RuntimeError("Content routing demonstration stopped: expected API rejection for " + label)

    def stage(label: str, explanation: str, result: Any = None,
              proposal: dict[str, Any] | None = None) -> None:
        filename = f"stage-{len(stages) + 1:02d}-{label}.json"
        item = {"label": label, "explanation": explanation, "runtime_revision": store.status()["revision"],
                "content": store.content_status(proposal["proposal_id"] if proposal else None),
                "operation_result": result}
        write_json(filename, item)
        stages.append({"label": label, "explanation": explanation, "artifact": filename,
                       "runtime_revision": item["runtime_revision"]})

    def packet(proposal: dict[str, Any]) -> dict[str, Any]:
        return store.content_status(proposal["proposal_id"])["packet"]

    def write_ids(proposal: dict[str, Any]) -> list[str]:
        return sorted(body["id"] for body in store.proposal(proposal["proposal_id"])["payload"]["writes"])

    def decide(label: str, credential: str, principal: str, artifact_id: str,
               proposal: dict[str, Any], content_packet: dict[str, Any]) -> dict[str, Any]:
        key = (proposal["proposal_id"], principal, artifact_id)
        previous = decisions.get(key)
        request = {
            "proposal_id": proposal["proposal_id"], "artifact_id": artifact_id, "verdict": "approve",
            "rationale": "Programmed consideration of the declared section fields for " + label
                         + "; no human review, prose truth or code behavior is asserted.",
            "expected_proposal_digest": proposal["digest"], "expected_policy_generation": generation,
            "expected_previous_decision": previous["decision_id"] if previous else None,
            "expected_content_packet_digest": content_packet["packet_digest"],
            "operation_id": "routing-decision-" + label,
        }
        result = store.governance_decide(**request, credential=credential)
        decisions[key] = result
        decision_requests[label] = request
        decision_history.append({"label": label, "result": result})
        return result

    def approve(label: str, proposal: dict[str, Any], content_packet: dict[str, Any]) -> None:
        for suffix, secret, principal in (("a", reviewer_a, REVIEWER_A), ("b", reviewer_b, REVIEWER_B)):
            for index, artifact_id in enumerate(write_ids(proposal), 1):
                decide(label + "-" + suffix + "-" + str(index), secret, principal,
                       artifact_id, proposal, content_packet)

    def accept(label: str, proposal: dict[str, Any], accepted: bool,
               expected_code: str | None = None) -> dict[str, Any]:
        result = store.accept(proposal["proposal_id"], credential=coordinator,
                              expected_policy_generation=generation)
        _require(result["accepted"] is accepted, "unexpected acceptance state for " + label)
        if expected_code:
            _require(any(item["code"] == expected_code for item in result["reasons"]),
                     "the expected acceptance blocker is absent for " + label)
        operations.append({"label": label, "result": result})
        return result

    bodies = {
        "document-a": _document(DOC_A, 4), "document-b": _document(DOC_B, 4),
        "document-b-mismatch": _document(DOC_B, 5),
        "code-shared": _file(CODE_SHARED, "code", "SAMPLE_LIMIT = 4\n"),
        "code-c": _file(CODE_C, "code", "SAMPLE_LIMIT = 4\n"),
        "document-d": _document(DOC_D, 4),
        "code-d": _file(CODE_D, "code", "SAMPLE_LIMIT = 4\n"),
        "outside": _file(OUTSIDE, "document", "# Fictional file outside every declared section\n"),
    }
    for label, body in bodies.items():
        store.enqueue("routing-author-" + label, "doc_author" if body["kind"] == "document" else "code_author", {
            "proposal": _proposal(initial_snapshot, body,
                                  "Fictional routing candidate; proposed code remains unexecuted data.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        # Each worker claims either author role with its matching local grant.
        # A role hint is derived from the scheduled item; task identity comes
        # from the queue because simultaneous claims may choose another item.
        scheduled = list(bodies)[index]
        role = "doc_author" if bodies[scheduled]["kind"] == "document" else "code_author"
        principal, secret = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author"
                             else (CODE_AUTHOR, code_author))
        task = store.claim_task(principal, roles=[role], credential=secret, lease_seconds=300)
        _require(task is not None, "an authenticated author task was unavailable")
        result = store.submit(task["task_id"], principal, task["token"],
                              task["payload"]["proposal"], credential=secret)
        return {"label": task["task_id"].removeprefix("routing-author-"), "principal_id": principal,
                "thread": current_thread().name, "task_id": task["task_id"],
                "fencing_token": task["token"], **result}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="routing-author") as executor:
        futures = [executor.submit(author_job, index) for index in range(len(bodies))]
        authors = {item["label"]: item for item in (future.result() for future in futures)}
    component_groups = {
        "incomplete-a": ("document-a", "code-shared"),
        "overlap-complete": ("document-a", "document-b", "code-shared", "code-c"),
        "overlap-mismatch": ("document-a", "document-b-mismatch", "code-shared", "code-c"),
        "disjoint-d": ("document-d", "code-d"),
        "disjoint-plus-outside": ("document-d", "code-d", "outside"),
    }
    composites: dict[str, dict[str, Any]] = {}
    for label, selected in component_groups.items():
        store.enqueue("routing-compose-" + label, "integrator", {
            "components": [{"proposal_id": authors[item]["proposal_id"], "digest": authors[item]["digest"]}
                           for item in selected],
            "rationale": "Compose exact fictional candidates for " + label + " consideration.",
        })
        task = store.claim_task(INTEGRATOR, roles=["integrator"], credential=integrator, lease_seconds=300)
        _require(task is not None, "an authenticated composition task was unavailable")
        composites[label] = store.compose(task["task_id"], INTEGRATOR, task["token"], credential=integrator)
    main = composites["overlap-complete"]
    disjoint = composites["disjoint-d"]
    stage("authenticated-candidates", "Eight author jobs and five compositions retain local provenance; all share runtime revision 1.",
          {"authors": authors, "composites": composites}, main)

    content_enabled = store.content_enable(legacy_policy, credential=admin, expected_policy_generation=generation)
    generation = content_enabled["policy_generation"]
    legacy_packet = packet(disjoint)
    _require(generation == 3 and legacy_packet["schema_version"] == 1 and legacy_packet["eligible"],
             "the frozen schema-one policy did not produce its ordinary eligible packet")
    packet_snapshots["legacy-d"] = legacy_packet
    stage("schema-one-policy-enabled", "The existing schema-one content gate covers exactly D; its packet keeps the original schema and rules.",
          content_enabled, disjoint)
    approve("legacy-d", disjoint, legacy_packet)
    historical_packet_before = store.content_packet(legacy_packet["packet_id"])
    legacy_packet_bytes = canonical(historical_packet_before).encode("utf-8")
    _require(historical_packet_before == legacy_packet, "stored schema-one packet differs from its observed body")
    stage("schema-one-packet-pinned", "Two registered reviewers each pin both D writes to the exact stored schema-one packet; D is not accepted yet.",
          {"decision_count": len(decision_history), "stored_packet": historical_packet_before}, disjoint)

    upgraded = store.content_set_policy(routing_policy, credential=admin, expected_policy_generation=generation)
    generation = upgraded["policy_generation"]
    _require(generation == 4 and upgraded["content_policy_generation"] == 2,
             "catalog upgrade did not advance both policy generations")
    root_packet = packet(main)
    disjoint_packet = packet(disjoint)
    _require(root_packet["schema_version"] == 2 and disjoint_packet["schema_version"] == 2
             and root_packet["content_rules_version"] == "content-routing-1"
             and root_packet["eligible"] and disjoint_packet["eligible"],
             "the complete overlapping and disjoint candidates were not eligible under routing")
    _require(root_packet["routing"]["selected_section_ids"] == ["section-a", "section-b", "section-c"]
             and disjoint_packet["routing"]["selected_section_ids"] == ["section-d"],
             "routing did not separate the overlapping group and D")
    packet_snapshots.update({"root-before-disjoint-acceptance": root_packet, "disjoint-d": disjoint_packet})
    stage("section-catalog-upgrade", "A/B/C form one overlap closure; D is separate. Repeated local promise and obligation IDs remain inside section namespaces.",
          {"upgrade": upgraded, "overlap_packet": root_packet, "disjoint_packet": disjoint_packet}, main)

    replay_label = "legacy-d-a-1"
    replayed_decision = store.governance_decide(**decision_requests[replay_label], credential=reviewer_a)
    original_decision = next(item["result"] for item in decision_history if item["label"] == replay_label)
    historical_packet_after = store.content_packet(legacy_packet["packet_id"])
    _require(canonical(historical_packet_after).encode("utf-8") == legacy_packet_bytes
             and replayed_decision["replayed"]
             and replayed_decision["decision_id"] == original_decision["decision_id"]
             and replayed_decision["content_packet"] == original_decision["content_packet"],
             "schema upgrade relabelled a historical schema-one packet or opinion")
    stage("historical-schema-one-replay", "Historical schema-one packet bytes and decision pins survive the schema-two upgrade; replay returns the old operation.",
          {"replayed_decision": replayed_decision, "historical_packet": historical_packet_after,
           "historical_packet_bytes_equal": True}, disjoint)

    inspected = {**composites, "partial-a": authors["document-a"], "outside": authors["outside"]}
    for label, proposal in inspected.items():
        formal_reviews[label] = store.review(proposal["proposal_id"], credential=reviewer_a)
        _require(formal_reviews[label]["approved"], "a syntactically valid fictional candidate failed formal review")
    _require(packet(main) == root_packet and packet(disjoint) == disjoint_packet,
             "formal review changed content packet identity")

    def raw_request(label: str, proposal: dict[str, Any], pin: str | None) -> dict[str, Any]:
        kwargs = {"credential": reviewer_a, "expected_proposal_digest": proposal["digest"],
                  "expected_policy_generation": generation, "expected_previous_decision": None,
                  "operation_id": "routing-negative-" + label}
        if pin is not None:
            kwargs["expected_content_packet_digest"] = pin
        return store.governance_decide(proposal["proposal_id"], write_ids(proposal)[0], "approve",
                                       "Programmed negative API example for " + label + ".", **kwargs)

    pin_rejections = [
        expected_exception("approval-without-packet", lambda: raw_request("missing-pin", main, None), "content_packet_required"),
        expected_exception("approval-with-wrong-packet", lambda: raw_request("wrong-pin", main, "0" * 64), "content_packet_changed"),
    ]
    stage("formal-review-and-exact-pins", "Every inspected candidate passes formal runtime review. Missing and wrong packet pins still reject new content decisions.",
          {"formal_reviews": formal_reviews, "pin_rejections": pin_rejections,
           "packet_stable_after_formal_review": True}, main)

    partial_packet = packet(authors["document-a"])
    _require(partial_packet["routing"]["initial_section_ids"] == ["section-a"]
             and partial_packet["routing"]["selected_section_ids"] == ["section-a", "section-b", "section-c"]
             and partial_packet["routing"]["missing_write_ids"] == sorted([DOC_B, CODE_SHARED, CODE_C])
             and not partial_packet["eligible"], "a partial A write did not close transitively through B to C")
    packet_snapshots["partial-a"] = partial_packet
    partial_acceptance = accept("partial-a", authors["document-a"], False, "content_packet_blocked")
    stage("transitive-routing-closure", "Writing doc A initially selects only A. Shared code adds B, then shared doc B adds C; missing writes block the complete closure.",
          {"packet": partial_packet, "acceptance": partial_acceptance}, authors["document-a"])

    incomplete = composites["incomplete-a"]
    incomplete_packet = packet(incomplete)
    _require(incomplete_packet["routing"]["selected_section_ids"] == ["section-a", "section-b", "section-c"]
             and incomplete_packet["routing"]["missing_write_ids"] == sorted([DOC_B, CODE_C])
             and not incomplete_packet["eligible"], "a locally complete A pair bypassed its overlap closure")
    packet_snapshots["incomplete-a"] = incomplete_packet
    incomplete_acceptance = accept("incomplete-a", incomplete, False, "content_packet_blocked")
    stage("locally-complete-section-is-incomplete", "A has both of its writes, but its connected B/C requirements also require doc B and code C.",
          {"packet": incomplete_packet, "acceptance": incomplete_acceptance}, incomplete)

    mismatch = composites["overlap-mismatch"]
    mismatch_packet = packet(mismatch)
    section_results = {item["section_id"]: item for item in mismatch_packet["sections"]}
    _require(section_results["section-a"]["eligible"]
             and not section_results["section-b"]["eligible"] and not section_results["section-c"]["eligible"]
             and not mismatch_packet["eligible"], "one passing section bypassed mismatching selected sections")
    mismatch_rejection = expected_exception("approve-overlap-mismatch", lambda: raw_request(
        "overlap-mismatch", mismatch, mismatch_packet["packet_digest"]), "content_packet_blocked")
    mismatch_acceptance = accept("overlap-mismatch", mismatch, False, "content_packet_blocked")
    packet_snapshots["overlap-mismatch"] = mismatch_packet
    stage("one-passing-section-is-insufficient", "A matches at 4 while doc B declares 5 against code values 4. B and C fail under the same atomic candidate.",
          {"approval": mismatch_rejection, "acceptance": mismatch_acceptance}, mismatch)

    uncovered_results = {}
    for label, candidate in (("outside", authors["outside"]),
                             ("disjoint-plus-outside", composites["disjoint-plus-outside"])):
        candidate_packet = packet(candidate)
        _require(candidate_packet["routing"]["uncovered_write_ids"] == [OUTSIDE]
                 and not candidate_packet["eligible"], "an uncovered write became eligible for " + label)
        if label == "outside":
            _require(candidate_packet["routing"]["selected_section_ids"] == [],
                     "outside-only proposal selected a declared section")
        packet_snapshots[label] = candidate_packet
        uncovered_results[label] = accept(label, candidate, False, "content_packet_blocked")
    stage("uncovered-writes-remain-blocked", "A write outside the catalog blocks both an outside-only proposal and an otherwise complete D proposal.",
          uncovered_results, composites["disjoint-plus-outside"])

    old_vote_observation = store.governance_observe(disjoint["proposal_id"], credential=coordinator,
                                                  expected_policy_generation=generation)
    _require(not old_vote_observation["eligible"], "schema-one opinion replay revived old votes for the new catalog")
    stale_acceptance = accept("schema-one-opinions-remain-historical", disjoint, False)
    # Use a new reviewer operation on the root, whose decision head is empty,
    # so the demonstrated rejection is the old packet pin rather than CAS.
    old_pin_rejection = expected_exception("new-operation-with-old-schema-one-packet", lambda: raw_request(
        "old-schema-one-pin", main, legacy_packet["packet_digest"]), "content_packet_changed")
    stage("new-policy-requires-fresh-votes", "The catalog upgrade changes global policy generation; old D opinions remain historical and cannot authorize its new packet.",
          {"observation": old_vote_observation, "acceptance": stale_acceptance,
           "old_pin_rejection": old_pin_rejection}, disjoint)

    approve("catalog-d", disjoint, disjoint_packet)
    approve("catalog-overlap", main, root_packet)
    overlap_observation_before = store.governance_observe(main["proposal_id"], credential=coordinator,
                                                         expected_policy_generation=generation)
    disjoint_observation_before = store.governance_observe(disjoint["proposal_id"], credential=coordinator,
                                                          expected_policy_generation=generation)
    _require(overlap_observation_before["eligible"] and disjoint_observation_before["eligible"]
             and packet(main) == root_packet and packet(disjoint) == disjoint_packet,
             "fresh pinned decisions did not authorize both candidates without changing packets")
    stage("independent-candidates-approved", "Both reviewers pin every write of D and A/B/C before either candidate is accepted; opinions themselves do not change packet identity.",
          {"overlap_observation": overlap_observation_before, "disjoint_observation": disjoint_observation_before,
           "decision_count": len(decision_history)}, main)

    accepted_disjoint = accept("accept-disjoint-d", disjoint, True)
    disjoint_receipt = accepted_disjoint["governance_receipt"]
    root_after_disjoint = packet(main)
    overlap_observation_after = store.governance_observe(main["proposal_id"], credential=coordinator,
                                                        expected_policy_generation=generation)
    _require(accepted_disjoint["revision"] == 2 and not accepted_disjoint["replayed"]
             and disjoint_receipt["schema_version"] == 3
             and disjoint_receipt["content_packet"]["packet_digest"] == disjoint_packet["packet_digest"]
             and root_after_disjoint == root_packet and overlap_observation_after["eligible"],
             "the unrelated D acceptance invalidated A/B/C packet identity or approvals")
    packet_snapshots["root-after-disjoint-acceptance"] = root_after_disjoint
    stage("disjoint-acceptance-preserves-other-packet", "D advances runtime revision to 2. A/B/C read/query bases, exact packet and existing votes remain current without another decision.",
          {"acceptance": accepted_disjoint, "overlap_observation": overlap_observation_after,
           "overlap_packet_bytes_equal": canonical(root_after_disjoint) == canonical(root_packet)}, main)

    accepted_root = accept("accept-overlap-from-earlier-revision", main, True)
    root_receipt = accepted_root["governance_receipt"]
    _require(accepted_root["revision"] == 3 and not accepted_root["replayed"]
             and root_receipt["schema_version"] == 3
             and root_receipt["content_packet"]["packet_digest"] == root_packet["packet_digest"],
             "the old-revision but current-basis overlap candidate did not retain its considered packet")
    stage("overlap-accepted-from-earlier-revision", "The A/B/C candidate captured at revision 1 accepts at revision 3 using its unchanged packet and the votes recorded before D acceptance.",
          accepted_root, main)

    events_before_replay = len(store.events())
    replayed_root = accept("replay-overlap-acceptance", main, True)
    replayed_disjoint = accept("replay-disjoint-acceptance", disjoint, True)
    historical_packet_final = store.content_packet(legacy_packet["packet_id"])
    recorded_root_packet = store.content_packet(root_packet["packet_id"])
    recorded_disjoint_packet = store.content_packet(disjoint_packet["packet_id"])
    _require(replayed_root["replayed"] and replayed_disjoint["replayed"]
             and replayed_root["governance_receipt"] == root_receipt
             and replayed_disjoint["governance_receipt"] == disjoint_receipt
             and len(store.events()) == events_before_replay
             and canonical(historical_packet_final).encode("utf-8") == legacy_packet_bytes
             and recorded_root_packet == root_packet and recorded_disjoint_packet == disjoint_packet,
             "acceptance replay or later revisions changed historical packets, receipts or events")
    stage("accepted-replays-preserve-history", "Authorized replays return both stored receipts without new events. Old schema-one and accepted schema-two packet bodies remain exact history.",
          {"root_replay": replayed_root, "disjoint_replay": replayed_disjoint,
           "historical_schema_one_bytes_equal": True}, main)

    duplicate_policy = deepcopy(routing_policy)
    duplicate_policy["sections"][1]["id"] = duplicate_policy["sections"][0]["id"]
    policy_before_invalid = store.content_status()["current_policy"]
    catalog_rejections = [
        expected_exception("duplicate-section-id", lambda: store.content_set_policy(
            duplicate_policy, credential=admin, expected_policy_generation=generation), "invalid_content_policy"),
        expected_exception("empty-section-catalog", lambda: store.content_set_policy(
            {"schema_version": 2, "sections": []}, credential=admin,
            expected_policy_generation=generation), "invalid_content_policy"),
    ]
    _require(store.content_status()["current_policy"] == policy_before_invalid,
             "a rejected catalog changed the active policy")
    stage("catalog-validation-preserves-active-policy", "Duplicate section IDs and an empty catalog are rejected; failed administration does not advance either policy generation.",
          catalog_rejections)

    final_status = store.status()
    final_content = store.content_status()
    final_governance = store.governance_status()
    events = store.events()
    _require(final_status["revision"] == 3 and final_status["proposals"].get("accepted") == 2,
             "unexpected final revision or accepted candidate count")
    proposal_results = {"author-" + key: value for key, value in authors.items()}
    proposal_results.update({"composite-" + key: value for key, value in composites.items()})
    root_final = store.content_status(main["proposal_id"])
    disjoint_final = store.content_status(disjoint["proposal_id"])
    routing_snapshots = {key: value["routing"] for key, value in packet_snapshots.items()
                         if value["schema_version"] == 2}
    history = {"schema_one_before_upgrade": historical_packet_before,
               "schema_one_after_upgrade": historical_packet_after,
               "schema_one_final": historical_packet_final,
               "schema_one_canonical_utf8_bytes_unchanged": True,
               "schema_one_decision_replay": replayed_decision,
               "accepted_overlap_packet": recorded_root_packet,
               "accepted_disjoint_packet": recorded_disjoint_packet}
    for name, value in {
        "initial-state.json": initial_snapshot, "final-state.json": store.snapshot(),
        "policy-schema-one.json": legacy_policy, "policy.json": routing_policy,
        "authors.json": authors, "composites.json": composites,
        "proposals.json": {key: store.proposal(value["proposal_id"]) for key, value in proposal_results.items()},
        "packets.json": packet_snapshots, "routing.json": routing_snapshots, "historical-packets.json": history,
        "root-content-final.json": root_final, "disjoint-content-final.json": disjoint_final,
        "content-final.json": final_content, "governance-final.json": final_governance,
        "authorship-final.json": store.authorship_status(), "grants.json": grants,
        "decisions.json": decision_history, "operations.json": operations, "events.json": events,
        "formal-reviews.json": formal_reviews,
        "activation.json": {"governance": governance_enabled, "authorship": authorship_enabled,
                            "content": content_enabled, "catalog_upgrade": upgraded},
        "observations.json": {"old_d_opinions_after_upgrade": old_vote_observation,
                              "root_before_disjoint_acceptance": overlap_observation_before,
                              "root_after_disjoint_acceptance": overlap_observation_after,
                              "disjoint_before_acceptance": disjoint_observation_before},
        "replays.json": {"schema_one_decision": replayed_decision, "root_acceptance": replayed_root,
                         "disjoint_acceptance": replayed_disjoint},
        "acceptance.json": accepted_root, "disjoint-acceptance.json": accepted_disjoint,
    }.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        "kind": "declared_content_section_routing_product_example", "workers": workers,
        "actors": {"authenticated_local_capability_principals": True, "persons_authenticated": False,
                   "human_review_recorded": False, "independent_people_or_models_established": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "editorial_policy": editorial_policy, "content_policy": routing_policy,
        "governance_policy_generation": generation, "content_policy_generation": 2,
        "main_proposal_id": main["proposal_id"], "disjoint_proposal_id": disjoint["proposal_id"],
        "historical_schema_one_packet_id": legacy_packet["packet_id"],
        "accepted_root_packet_id": root_packet["packet_id"], "accepted_disjoint_packet_id": disjoint_packet["packet_id"],
        "stages": stages, "initial_runtime_revision": initial_snapshot["revision"], "final_runtime_status": final_status,
        "summary": {"author_job_count": len(authors), "composite_proposal_count": len(composites),
                    "registered_author_principal_count": len({item["principal_id"] for item in authors.values()}),
                    "root_contributor_count": len(main["authorship"]["contributor_principals"]),
                    "catalog_section_count": len(routing_policy["sections"]),
                    "root_selected_section_count": len(root_packet["routing"]["selected_section_ids"]),
                    "disjoint_selected_section_count": len(disjoint_packet["routing"]["selected_section_ids"]),
                    "decision_count": len(decision_history), "stage_count": len(stages),
                    "expected_api_rejection_count": sum(item.get("rejected", False) for item in operations),
                    "blocked_acceptance_count": sum(item.get("result", {}).get("accepted") is False for item in operations),
                    "runtime_event_count": len(events), "accepted_proposal_count": final_status["proposals"].get("accepted", 0),
                    "acceptance_receipt_schema": root_receipt["schema_version"],
                    "accepted_content_packet_schema": root_packet["schema_version"],
                    "historical_content_packet_schema": legacy_packet["schema_version"],
                    "root_packet_unchanged_after_disjoint_acceptance": root_after_disjoint == root_packet,
                    "root_approved_before_disjoint_acceptance": overlap_observation_before["eligible"],
                    "root_still_eligible_after_disjoint_acceptance": overlap_observation_after["eligible"],
                    "historical_schema_one_packet_bytes_unchanged": True,
                    "release_created": False, "credential_material_exported": False, "measured_capacity": False,
                    "semantic_equivalence_proved": False,
                    "accepted_content_packet_digest": root_receipt["content_packet"]["packet_digest"]},
        "credential_file_layout": {"administrator": "private/admin.credential",
                                   "active_document_author": "private/document-author.credential",
                                   "active_code_author": "private/code-author.credential",
                                   "active_integrator": "private/integrator.credential",
                                   "active_reviewer_a": "private/reviewer-a.credential",
                                   "active_reviewer_b": "private/reviewer-b.credential",
                                   "active_coordinator": "private/coordinator.credential",
                                   "private_database": "private/state.sqlite3"},
        "limitations": [
            "The catalog is administrator-declared; completeness over undeclared requirements and files is not established.",
            "Overlap closure requires every write of each selected section and every selected section must pass.",
            "Only actual read/query bases invalidate a packet after runtime changes; policy generations still invalidate opinions globally.",
            "Exact quotes and selected JSON/AST fields do not prove natural-language entailment, prose truth or application behavior.",
            "Programmed local principals do not establish people, models, expertise, independent reviewers or human understanding.",
            "The SQLite owner, application and local clock are trusted; internal hashes are not external attestations.",
            "Historical packets and replays preserve recorded facts without reviving old votes for current policy.",
            "Configured workers and this bounded run do not measure service capacity, throughput or supported external agents.",
            "No external LLM, candidate-code execution, release or Git publication is performed by this command.",
            "Credentials, their hashes and SQLite remain in private files outside the public artifacts.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример маршрутизации рассмотрения содержания", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Программируемые локальные авторы предложили вымышленные документы с JSON-контрактом и Python-файлы",
        "с целой константой SAMPLE_LIMIT. Предложенный код разбирается через AST и не исполняется.", "",
        "Каталог содержит четыре раздела: A связывает doc A и общий код; B — doc B и тот же общий код;",
        "C — doc B и код C; D — отдельную пару документа и кода. Идентификаторы sample-limit и",
        "sample-promise намеренно повторяются внутри разных разделов и имеют локальную область.", "",
        "## Последовательность", "", "| Состояние | Наблюдение | Ревизия |", "| --- | --- | --- |",
    ]
    for item in stages:
        lines.append(f"| [{item['label']}]({item['artifact']}) | {item['explanation']} | {item['runtime_revision']} |")
    lines += [
        "", "## Что произошло", "",
        "1. Восемь авторских заданий и пять композиций созданы на ревизии 1 с аутентифицированным локальным происхождением.",
        "2. Прежняя политика schema 1 потребовала точную пару D. Четыре решения закрепили её пакет, но принятия ещё не было.",
        "3. Переход на каталог schema 2 повысил поколение содержания до 2, governance — до 4. Исторический пакет schema 1",
        "   сохранился побайтно в каноническом UTF-8 JSON. Повтор прежней операции вернул старый голос, который не стал действующим.",
        "4. Запись только doc A сначала выбирает A, затем общий код приводит B, а общий doc B приводит C. Полная связная группа",
        "   требует четыре файла. Даже локально полная пара A недостаточна; непокрытые записи блокируют весь кандидат.",
        "5. В варианте несовпадения A проходит, B и C не проходят. Один успешный раздел не предоставляет разрешения общей правке.",
        "6. Два непричастных проверяющих закрепили каждый файл D и A/B/C до первого принятия. D принято на ревизии 2.",
        "   Пакет A/B/C, его основания и действующие голоса остались теми же. Повторные решения не потребовались.",
        "7. Кандидат A/B/C с исходными чтениями ревизии 1 принят на ревизии 3. Обе квитанции schema 3 закрепили пакеты schema 2.",
        "   Разрешённые повторы вернули прежние квитанции без новых событий. Исторический пакет schema 1 остался неизменным.", "",
        "Глобальное поколение политики по-прежнему консервативно обновляет необходимость рассмотрения всего каталога.",
        "Локальность разделов относится к выбору обязательств и фактическим read/query основаниям, а не к отмене проверки политики.", "",
        "## Публичные материалы", "",
        "- [Полный отчёт](report.json), [каталог schema 2](policy.json), [прежняя политика schema 1](policy-schema-one.json).",
        "- [Активация и переход](activation.json), [маршруты](routing.json), [пакеты вариантов](packets.json).",
        "- [Исторические пакеты](historical-packets.json), [наблюдения до и после принятия D](observations.json).",
        "- [Авторы](authors.json), [композиции](composites.json), [все предложения](proposals.json), [происхождение](authorship-final.json).",
        "- [Решения](decisions.json), [формальные проверки](formal-reviews.json), [операции и отказы](operations.json).",
        "- [Принятие A/B/C](acceptance.json), [принятие D](disjoint-acceptance.json), [повторы](replays.json).",
        "- [Текущие и сохранённые пакеты A/B/C](root-content-final.json), [то же для D](disjoint-content-final.json).",
        "- [Политика содержания](content-final.json), [governance](governance-final.json), [допуски без секретов](grants.json).",
        "- [События](events.json), [начальное](initial-state.json) и [конечное](final-state.json) состояние.", "",
        "## Границы", "",
        "Каталог и опись задаёт администратор. Маршрутизация не обнаруживает все неявные требования естественного языка",
        "и не доказывает полноту политики, смысл всей прозы, её истинность или поведение программы. Все решения запрограммированы.",
        "Локальные идентификаторы и допуски не устанавливают личность человека, независимость моделей или понимание текста.",
        "Доверены владелец SQLite, приложение и часы; внешнего заверения, SSO и вызовов LLM нет.", "",
        "private/ имеет права 0700; секреты .credential имеют права 0600. Секреты, их хеши и SQLite не входят в публичные материалы.",
        "Этот сценарий не создаёт выпуск и не публикует Git. Просмотр пакета совещателен; новое принятие разрешает только транзакция Store.accept.", "",
        f"Настроено Python workers: {workers}; заданий авторов: {len(authors)}; композиций: {len(composites)}; решений: {len(decision_history)}; событий runtime: {len(events)}.",
        f"Этапов: {len(stages)}; ожидаемых отказов API: {report['summary']['expected_api_rejection_count']}; заблокированных принятий: {report['summary']['blocked_acceptance_count']}.",
        "Размер пула и этот ограниченный сценарий не измеряют ёмкость сервиса или число поддерживаемых LLM.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
