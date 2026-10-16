"""Run a bounded workflow with mandatory, exactly pinned content consideration.

All actors are programmed local principals. The fictional document contract and
Python declarations are proposed data; Python candidates are never executed.
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
from .store import Store, WorkbenchError


DOC_ID = "docs/content.md"
CODE_ID = "code/content.py"
EXTRA_ID = "docs/content-extra.md"
QUOTE = "The sample limit is declared below."
DOCUMENT_AUTHOR = "content-document-author"
CODE_AUTHOR = "content-code-author"
INTEGRATOR = "content-integrator"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Content demonstration stopped: " + message)


def _file(identifier: str, kind: str, template: str) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "template": template, "bindings": []}


def _document(limit: int, quote: str = QUOTE) -> dict[str, Any]:
    return _file(DOC_ID, "document", "# Fictional sample contract\n\n" + quote
                 + '\n\n```json agentwiki:contract\n{"limit":' + str(limit) + "}\n```\n")


def _policy() -> dict[str, Any]:
    obligation = {
        "id": "sample-limit", "description": "The fictional literal sample limit must match.",
        "scope": {"sample": "v1"}, "relation": "equals", "value_type": "integer",
        "document": {"artifact_id": DOC_ID, "fence_id": "contract", "path": ["limit"]},
        "code": {"artifact_id": CODE_ID, "symbol": "SAMPLE_LIMIT"},
    }
    return {
        "schema_version": 1, "artifact_ids": [DOC_ID, CODE_ID],
        "spec": {"schema_version": 1, "obligations": [obligation]},
        "inventory": {"schema_version": 1, "promises": [{
            "id": "sample-promise", "description": "The declared fictional sample limit.",
            "anchor": {"artifact_id": DOC_ID, "quote": QUOTE},
            "requirement": {key: deepcopy(obligation[key]) for key in
                            ("scope", "relation", "value_type", "document", "code")},
            "obligation_ids": ["sample-limit"],
        }]},
    }


def run_content_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save an ordinary local workflow, with private authority kept separately."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Content demo needs a new empty workspace; existing data is preserved")
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
    store.bootstrap([_document(3), _file(CODE_ID, "code", "SAMPLE_LIMIT = 3\n")])
    initial_snapshot = store.snapshot()
    editorial_policy = {"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True}
    content_policy = _policy()
    admin = new_credential("admin")
    governance_enabled = store.governance_enable(editorial_policy, "content-administrator", admin)
    generation = 1
    grants: dict[str, dict[str, Any]] = {}
    expires_at = time.time() + 3600
    all_artifacts = [DOC_ID, CODE_ID, EXTRA_ID]

    def grant(label: str, principal: str, role: str, artifact_ids: list[str]) -> str:
        secret = new_credential(label)
        grants[label] = store.governance_grant(
            principal, role, artifact_ids, expires_at, credential=admin,
            new_credential=secret, operation_id="content-grant-" + label)
        return secret

    document_author = grant("document-author", DOCUMENT_AUTHOR, "doc_author", [DOC_ID, EXTRA_ID])
    code_author = grant("code-author", CODE_AUTHOR, "code_author", [CODE_ID])
    integrator = grant("integrator", INTEGRATOR, "integrator", all_artifacts)
    reviewer_a = grant("reviewer-a", "content-reviewer-a", "reviewer", all_artifacts)
    reviewer_b = grant("reviewer-b", "content-reviewer-b", "reviewer", all_artifacts)
    coordinator = grant("coordinator", "content-coordinator", "coordinator", all_artifacts)
    contributor_reviewer = grant("contributor-reviewer", DOCUMENT_AUTHOR, "reviewer", all_artifacts)
    authorship_enabled = store.authorship_enable(credential=admin, expected_policy_generation=generation)
    generation = 2
    _require(authorship_enabled["policy_generation"] == generation, "authorship activation did not advance generation")
    operations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    decision_history: list[dict[str, Any]] = []
    decision_requests: dict[str, dict[str, Any]] = {}
    decisions: dict[tuple[str, str, str], dict[str, Any]] = {}
    formal_reviews: dict[str, dict[str, Any]] = {}
    packet_snapshots: dict[str, dict[str, Any]] = {}

    def expected_exception(label: str, operation: Callable[[], Any], expected_code: str) -> dict[str, Any]:
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == expected_code, "unexpected API rejection for " + label + ": " + error.code)
            result = {"label": label, "rejected": True, "code": error.code}
            operations.append(result)
            return result
        raise RuntimeError("Content demonstration stopped: expected API rejection for " + label)

    def stage(label: str, explanation: str, result: Any = None,
              proposal: dict[str, Any] | None = None) -> None:
        proposal_id = proposal["proposal_id"] if proposal else None
        filename = f"stage-{len(stages) + 1:02d}-{label}.json"
        item = {"label": label, "explanation": explanation, "runtime_revision": store.status()["revision"],
                "content": store.content_status(proposal_id), "operation_result": result}
        write_json(filename, item)
        stages.append({"label": label, "explanation": explanation, "artifact": filename,
                       "runtime_revision": item["runtime_revision"]})

    def packet(proposal: dict[str, Any]) -> dict[str, Any]:
        return store.content_status(proposal["proposal_id"])["packet"]

    def decide(label: str, token: str, principal: str, artifact_id: str,
               proposal: dict[str, Any], content_packet: dict[str, Any] | None = None) -> dict[str, Any]:
        key = (proposal["proposal_id"], principal, artifact_id)
        previous = decisions.get(key)
        request = {
            "proposal_id": proposal["proposal_id"], "artifact_id": artifact_id, "verdict": "approve",
            "rationale": "Programmed consideration of the exact declared sample fields for " + label
                         + "; no human review, prose truth or code behavior is asserted.",
            "expected_proposal_digest": proposal["digest"], "expected_policy_generation": generation,
            "expected_previous_decision": previous["decision_id"] if previous else None,
            "operation_id": "content-decision-" + label,
        }
        if content_packet is not None:
            request["expected_content_packet_digest"] = content_packet["packet_digest"]
        result = store.governance_decide(**request, credential=token)
        decisions[key] = result
        decision_requests[label] = request
        decision_history.append({"label": label, "result": result})
        return result

    def approve_bundle(label: str, proposal: dict[str, Any], content_packet: dict[str, Any] | None = None) -> None:
        for suffix, token, principal in (("a", reviewer_a, "content-reviewer-a"),
                                          ("b", reviewer_b, "content-reviewer-b")):
            for artifact_id, artifact_label in ((DOC_ID, "document"), (CODE_ID, "code")):
                decide(label + "-" + suffix + "-" + artifact_label, token, principal,
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
        "document": _document(4),
        "document-missing-quote": _document(4, "A different fictional sentence."),
        "document-ambiguous-quote": _document(4, QUOTE + "\n" + QUOTE),
        "code": _file(CODE_ID, "code", "SAMPLE_LIMIT = 4\n"),
        "code-mismatch": _file(CODE_ID, "code", "SAMPLE_LIMIT = 5\n"),
        "code-unresolved": _file(CODE_ID, "code", "SAMPLE_LIMIT = 2 + 2\n"),
        "extra": _file(EXTRA_ID, "document", "# Fictional extra file outside the configured bundle\n"),
    }
    for label, body in bodies.items():
        role = "doc_author" if body["kind"] == "document" else "code_author"
        store.enqueue("content-author-" + label, role, {"proposal": _proposal(
            initial_snapshot, body, "Fictional content-gate candidate; proposed code remains unexecuted data.")})
    barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int, label: str) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            barrier.wait(timeout=30)
        role = "doc_author" if bodies[label]["kind"] == "document" else "code_author"
        principal, secret = ((DOCUMENT_AUTHOR, document_author) if role == "doc_author"
                             else (CODE_AUTHOR, code_author))
        task = store.claim_task(principal, roles=[role], credential=secret, lease_seconds=300)
        _require(task is not None, "an authenticated author task was unavailable")
        result = store.submit(task["task_id"], principal, task["token"],
                              task["payload"]["proposal"], credential=secret)
        return {"label": task["task_id"].removeprefix("content-author-"), "principal_id": principal,
                "thread": current_thread().name, "task_id": task["task_id"],
                "fencing_token": task["token"], **result}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="content-author") as executor:
        futures = [executor.submit(author_job, index, label) for index, label in enumerate(bodies)]
        authors = {item["label"]: item for item in (future.result() for future in futures)}
    components = {
        "matching": ("document", "code"), "mismatch": ("document", "code-mismatch"),
        "unresolved": ("document", "code-unresolved"),
        "missing-quote": ("document-missing-quote", "code"),
        "ambiguous-quote": ("document-ambiguous-quote", "code"),
        "extra-write": ("document", "code", "extra"),
    }
    composites: dict[str, dict[str, Any]] = {}
    for label, selected in components.items():
        store.enqueue("content-compose-" + label, "integrator", {
            "components": [{"proposal_id": authors[item]["proposal_id"], "digest": authors[item]["digest"]}
                           for item in selected],
            "rationale": "Compose exact fictional candidates for " + label + " consideration.",
        })
        task = store.claim_task(INTEGRATOR, roles=["integrator"], credential=integrator, lease_seconds=300)
        _require(task is not None, "an authenticated composition task was unavailable")
        composites[label] = store.compose(task["task_id"], INTEGRATOR, task["token"], credential=integrator)
    main = composites["matching"]
    stage("authenticated-candidates", "Seven author jobs and six exact compositions carry local authorship; none has been accepted.",
          {"authors": authors, "composites": composites}, main)

    approve_bundle("generic-matching", main)
    approve_bundle("generic-mismatch", composites["mismatch"])
    stage("generic-opinions-before-content", "The former editorial API can record opinions without inspecting the declared cross-file content.",
          {"generic_decision_count": len(decision_history)}, composites["mismatch"])
    enabled = store.content_enable(content_policy, credential=admin, expected_policy_generation=generation)
    generation = 3
    first_packet = packet(main)
    _require(first_packet["eligible"] and first_packet["content_policy_generation"] == 1
             and first_packet["governance_policy_generation"] == generation,
             "the matching bundle was not eligible under the activated policy")
    packet_snapshots["matching-generation-three"] = first_packet
    stage("content-activation", "Content policy generation 1 advances governance to 3; generic old opinions no longer meet the current gate.",
          {"activation": enabled, "acceptance": accept("old-generic-opinions", main, False)}, main)

    before_formal = first_packet["packet_digest"]
    for label, proposal in {**composites, "partial-write": authors["document"]}.items():
        formal_reviews[label] = store.review(proposal["proposal_id"], credential=reviewer_a)
        _require(formal_reviews[label]["approved"], "a syntactically valid fictional candidate failed formal review")
    _require(packet(main)["packet_digest"] == before_formal, "formal review altered the exact content packet")
    stage("formal-review-is-separate", "All seven inspected candidates pass formal runtime review; this does not establish declared content consistency.",
          {"formal_reviews": formal_reviews, "packet_stable_after_formal_review": True}, main)

    def raw_request(label: str, proposal: dict[str, Any], pin: str | None,
                    token: str = reviewer_a) -> dict[str, Any]:
        kwargs = {"credential": token, "expected_proposal_digest": proposal["digest"],
                  "expected_policy_generation": generation, "expected_previous_decision": None,
                  "operation_id": "content-negative-" + label}
        if pin is not None:
            kwargs["expected_content_packet_digest"] = pin
        return store.governance_decide(proposal["proposal_id"], DOC_ID, "approve",
                                       "Programmed negative API example for " + label + ".", **kwargs)

    pin_rejections = [
        expected_exception("approval-without-packet", lambda: raw_request("missing-pin", main, None), "content_packet_required"),
        expected_exception("approval-with-wrong-packet", lambda: raw_request("wrong-pin", main, "0" * 64), "content_packet_changed"),
        expected_exception("contributor-cannot-approve", lambda: raw_request(
            "contributor", main, first_packet["packet_digest"], contributor_reviewer), "authorship_contributor_separated"),
    ]
    stage("exact-pin-and-contributor-gate", "Every new decision must identify its exact content packet; a new reviewer grant does not separate its author principal.",
          pin_rejections, main)

    blocked_results: dict[str, dict[str, Any]] = {}
    for label, explanation in (
        ("mismatch", "Document limit 4 and code limit 5 disagree despite the old generic approvals."),
        ("unresolved", "A computed Python expression remains unresolved; the workflow never executes it."),
        ("missing-quote", "A matching numeric relation does not repair the absent exact promise quotation."),
        ("ambiguous-quote", "Two occurrences of the exact promise quotation make its anchor ambiguous."),
    ):
        candidate = composites[label]
        candidate_packet = packet(candidate)
        _require(not candidate_packet["eligible"], "a content defect was reported eligible for " + label)
        packet_snapshots[label] = candidate_packet
        rejection = expected_exception("approve-" + label, lambda candidate=candidate, candidate_packet=candidate_packet, label=label:
                                       raw_request(label, candidate, candidate_packet["packet_digest"]), "content_packet_blocked")
        blocked_results[label] = {"approval": rejection, "acceptance": accept("accept-" + label, candidate, False, "content_packet_blocked")}
        stage(label + "-blocked", explanation, blocked_results[label], candidate)
    bundle_results = {}
    for label, candidate in (("partial-write", authors["document"]), ("extra-write", composites["extra-write"])):
        candidate_packet = packet(candidate)
        _require(not candidate_packet["eligible"], "an incomplete or additional write bundle was allowed")
        packet_snapshots[label] = candidate_packet
        bundle_results[label] = accept("accept-" + label, candidate, False, "content_packet_blocked")
    blocked_results.update(bundle_results)
    stage("whole-bundle-required", "The bounded policy requires exactly its two configured write IDs; partial and extra bundles are blocked.",
          bundle_results, composites["extra-write"])

    approve_bundle("generation-three", main, first_packet)
    after_decisions_packet = packet(main)
    _require(after_decisions_packet["packet_digest"] == first_packet["packet_digest"],
             "recording exact-packet decisions altered the packet digest")
    observed = store.governance_observe(main["proposal_id"], credential=coordinator,
                                        expected_policy_generation=generation)
    _require(observed["eligible"] and observed["content"]["packet_digest"] == first_packet["packet_digest"],
             "complete generation-three consideration was unexpectedly blocked")
    stage("eligible-before-policy-refresh", "Two noncontributor reviewers pin the exact packet for each write; decisions themselves do not alter that packet.",
          {"observation": observed, "packet_stable_after_decisions": True}, main)

    refreshed = store.content_set_policy(content_policy, credential=admin, expected_policy_generation=generation)
    generation = 4
    refreshed_packet = packet(main)
    packet_snapshots["matching-generation-four"] = refreshed_packet
    _require(refreshed_packet["eligible"] and refreshed_packet["content_policy_generation"] == 2
             and refreshed_packet["governance_policy_generation"] == generation
             and refreshed_packet["packet_digest"] != first_packet["packet_digest"],
             "the identical-body policy refresh did not produce a new exact packet")
    refreshed_observation = store.governance_observe(main["proposal_id"], credential=coordinator,
                                                    expected_policy_generation=generation)
    _require(not refreshed_observation["eligible"], "old packet opinions remained eligible after policy refresh")
    stage("policy-refresh-invalidates-opinions", "An identical policy body creates content generation 2 and governance generation 4; earlier pins cannot vote for the new packet.",
          {"policy_refresh": refreshed, "observation": refreshed_observation,
           "acceptance": accept("policy-refresh-requires-new-consideration", main, False)}, main)

    replay_label = "generation-three-a-document"
    replayed_decision = store.governance_decide(**decision_requests[replay_label], credential=reviewer_a)
    original_decision = next(item["result"] for item in decision_history if item["label"] == replay_label)
    _require(replayed_decision["replayed"] and replayed_decision["decision_id"] == original_decision["decision_id"]
             and replayed_decision["content_packet"] == original_decision["content_packet"],
             "historical decision replay relabelled the old packet")
    _require(not store.governance_observe(main["proposal_id"], credential=coordinator,
                                        expected_policy_generation=generation)["eligible"],
             "historical operation replay revived an old opinion")
    stale_pin = expected_exception("new-operation-with-old-packet", lambda: raw_request(
        "old-packet", main, first_packet["packet_digest"]), "content_packet_changed")
    stage("historical-decision-replay", "A current original grant can replay the exact historical operation; its old pin remains historical and a new operation rejects it.",
          {"replayed_decision": replayed_decision, "new_operation_rejection": stale_pin}, main)

    approve_bundle("generation-four", main, refreshed_packet)
    current_observation = store.governance_observe(main["proposal_id"], credential=coordinator,
                                                 expected_policy_generation=generation)
    _require(current_observation["eligible"], "fresh exact-packet approvals did not restore eligibility")
    stage("fresh-consideration-under-current-policy", "Both registered reviewers reconsider each write and pin the new packet; formal review, provenance and runtime guards remain separate requirements.",
          current_observation, main)
    accepted = accept("atomic-content-acceptance", main, True)
    receipt = accepted["governance_receipt"]
    _require(accepted["revision"] == 2 and not accepted["replayed"] and receipt["schema_version"] == 3
             and receipt["content_packet"]["packet_digest"] == refreshed_packet["packet_digest"],
             "acceptance did not pin the exact content packet in a schema-three receipt")
    accepted_status = store.content_status(main["proposal_id"])
    _require(any(item["packet_id"] == receipt["content_packet"]["packet_id"]
                 and item["packet_digest"] == receipt["content_packet"]["packet_digest"]
                 for item in accepted_status["stored_packets"]),
             "the acceptance receipt refers to a packet absent from public storage history")
    _require(accepted_status["packet"]["packet_digest"] != receipt["content_packet"]["packet_digest"],
             "the post-acceptance stale basis unexpectedly remained the accepted packet")
    stage("accepted-with-content-receipt", "New acceptance checks the exact packet in its transaction, advances revision 1 to 2, and retains the packet referenced by receipt schema 3.",
          accepted, main)
    events_before_replay = len(store.events())
    accepted_replay = accept("historical-acceptance-replay", main, True)
    _require(accepted_replay["replayed"] and accepted_replay["governance_receipt"] == receipt
             and len(store.events()) == events_before_replay,
             "authorized acceptance replay changed the historical receipt or runtime events")
    stage("historical-acceptance-is-stable", "The accepted candidate now has stale source reads; authorized replay returns its stored receipt before new live packet checks and creates no new event.",
          {"replayed_acceptance": accepted_replay, "current_packet": accepted_status["packet"]}, main)

    final_status = store.status()
    final_content = store.content_status()
    final_governance = store.governance_status()
    events = store.events()
    _require(final_status["revision"] == 2 and final_status["proposals"].get("accepted") == 1,
             "unexpected final revision or accepted candidate count")
    proposal_results = {"author-" + key: value for key, value in authors.items()}
    proposal_results.update({"composite-" + key: value for key, value in composites.items()})
    for name, value in {
        "initial-state.json": initial_snapshot, "final-state.json": store.snapshot(),
        "policy.json": content_policy, "authors.json": authors, "composites.json": composites,
        "proposals.json": {key: store.proposal(value["proposal_id"]) for key, value in proposal_results.items()},
        "packets.json": packet_snapshots, "root-content-final.json": accepted_status,
        "content-final.json": final_content, "governance-final.json": final_governance,
        "authorship-final.json": store.authorship_status(), "grants.json": grants,
        "decisions.json": decision_history, "operations.json": operations, "events.json": events,
        "formal-reviews.json": formal_reviews, "blocked-candidates.json": blocked_results,
        "activation.json": {"governance": governance_enabled, "authorship": authorship_enabled,
                            "content": enabled, "content_policy_refresh": refreshed},
        "replays.json": {"decision": replayed_decision, "acceptance": accepted_replay},
        "acceptance.json": accepted,
    }.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        "kind": "mandatory_exact_content_packet_product_example", "workers": workers,
        "actors": {"authenticated_local_capability_principals": True, "persons_authenticated": False,
                   "human_review_recorded": False, "independent_people_or_models_established": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "editorial_policy": editorial_policy, "content_policy": content_policy,
        "governance_policy_generation": generation, "content_policy_generation": 2, "stages": stages,
        "initial_runtime_revision": initial_snapshot["revision"], "final_runtime_status": final_status,
        "summary": {"author_job_count": len(authors), "composite_proposal_count": len(composites),
                    "registered_author_principal_count": len({item["principal_id"] for item in authors.values()}),
                    "root_contributor_count": len(main["authorship"]["contributor_principals"]),
                    "decision_count": len(decision_history), "stage_count": len(stages),
                    "expected_api_rejection_count": sum(item.get("rejected", False) for item in operations),
                    "blocked_acceptance_count": sum(item.get("result", {}).get("accepted") is False for item in operations),
                    "runtime_event_count": len(events), "accepted_proposal_count": final_status["proposals"].get("accepted", 0),
                    "acceptance_receipt_schema": receipt["schema_version"], "release_created": False,
                    "credential_material_exported": False, "measured_capacity": False,
                    "semantic_equivalence_proved": False, "accepted_content_packet_digest": receipt["content_packet"]["packet_digest"]},
        "credential_file_layout": {"administrator": "private/admin.credential",
                                   "active_document_author": "private/document-author.credential",
                                   "active_code_author": "private/code-author.credential",
                                   "active_integrator": "private/integrator.credential",
                                   "active_reviewer_a": "private/reviewer-a.credential",
                                   "active_reviewer_b": "private/reviewer-b.credential",
                                   "active_coordinator": "private/coordinator.credential",
                                   "private_database": "private/state.sqlite3"},
        "limitations": [
            "The SQLite owner, declared policy and local clock are trusted; local digests are not external attestation.",
            "Programmed local principals and bearer grants do not establish people, models, expertise or independence.",
            "Exact quotations and explicitly selected JSON/AST values do not prove natural-language entailment or factual truth.",
            "The gate checks one declared whole bundle; it does not discover every undocumented requirement or artifact.",
            "A literal relation match does not prove application behavior; computed code remains unresolved and is never executed.",
            "Programmed consideration records explicit exact-packet approval but does not establish human understanding.",
            "Public status is advisory; only the Store.accept transaction grants new runtime acceptance.",
            "Historical replays preserve old packets and receipts without reviving old votes for a current policy.",
            "Configured workers and seven jobs are not measured throughput or a supported limit of external agents.",
            "Credentials, their hashes and SQLite stay private; this command creates no release or Git publication.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример обязательного рассмотрения содержания", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Программируемые локальные авторы предложили документ с явным JSON-контрактом и Python-файлы.",
        "Интегратор составил шесть кандидатов. Политика требует точный комплект документа и кода, совпадение",
        "целого значения limit и SAMPLE_LIMIT, а также уникальную дословную цитату заявленного обещания.", "",
        "Документ и код являются вымышленными данными. AST разбирается без исполнения. Локальные идентификаторы",
        "и секреты допуска не устанавливают личность человека, независимость моделей или фактическое понимание текста.", "",
        "## Последовательность", "", "| Состояние | Наблюдение | Ревизия |", "|---|---|---:|",
    ]
    for item in stages:
        lines.append(f"| [{item['label']}]({item['artifact']}) | {item['explanation']} | {item['runtime_revision']} |")
    lines += [
        "", "## Практический результат", "",
        "До включения нового режима общие одобрения можно было записать для совпадающего и несовпадающего кода.",
        "Включение политики содержания повышает поколение governance с 2 до 3. Прежние голоса остаются историей",
        "и не разрешают новое принятие. Новое решение должно закрепить точный digest вычисленного пакета.", "",
        "Различие 4 и 5, вычисляемое выражение, отсутствующая либо неоднозначная цитата, неполный либо расширенный",
        "комплект блокируют принятие. Формальная проверка синтаксиса при этом проходит: эти проверки имеют разные основания.",
        "Авторы и интегратор исключены из согласования собственного кандидата, даже с другим reviewer grant.", "",
        "Два непричастных principal_id явно одобрили каждый файл по пакету поколения 3. Повторная запись такой же",
        "политики создала поколение содержания 2 и governance 4. Ранее согласованный пакет стал историческим;",
        "повтор старой операции возвращает старый голос, но не переносит его на новый пакет. Потребовались новые решения.", "",
        "Принятие выполнилось один раз: ревизия 1 → 2. Квитанция schema 3 закрепила происхождение и точный пакет",
        "содержания. После изменения базы текущий просмотр имеет устаревшие исходные чтения; разрешённый повтор",
        "принятия возвращает прежнюю квитанцию без нового события и без повторного разрешения публикации.", "",
        "Запись формального review и редакторских решений не меняет digest пакета. Политика и зависимости кандидата",
        "входят в его основание; число событий и несвязанные метаданные не используются как замена этому основанию.", "",
        "## Публичные материалы", "",
        "- [Полный отчёт](report.json), [политика](policy.json), [активация и обновление](activation.json).",
        "- [Авторы](authors.json), [композиции](composites.json), [все предложения](proposals.json).",
        "- [Пакеты по вариантам](packets.json), [текущий и сохранённые пакеты принятого кандидата](root-content-final.json).",
        "- [Решения](decisions.json), [формальные проверки](formal-reviews.json), [отклонённые варианты](blocked-candidates.json).",
        "- [Операции и отказы](operations.json), [принятие с квитанцией](acceptance.json), [повторы](replays.json).",
        "- [Состояние политики содержания](content-final.json), [governance](governance-final.json), [происхождение](authorship-final.json).",
        "- [Допуски без секретов](grants.json), [события](events.json), [начальное](initial-state.json) и [конечное](final-state.json) состояние.", "",
        "## Границы", "",
        "Это обязательная проверка явно объявленного контракта и привязка рассмотрения к точному снимку. Она не",
        "распознаёт все требования естественного языка, не доказывает полноту описи, истинность прозы, поведение",
        "программы, независимость участников или то, что человек прочёл документ. Все решения запрограммированы.",
        "Доверены владелец SQLite, политика и часы; внешнего заверения, SSO и вызовов LLM нет.", "",
        "private/ имеет права 0700; секреты .credential имеют права 0600. Секреты, их хеши и SQLite не входят",
        "в публичные материалы. Публикация Git и выпуск являются отдельными действиями.", "",
        f"Настроено Python workers: {workers}; заданий авторов: {len(authors)}; композиций: {len(composites)}; решений: {len(decision_history)}; событий runtime: {len(events)}.",
        "Размер пула и этот ограниченный сценарий не измеряют ёмкость сервиса или число поддерживаемых LLM.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
