"""Exercise opt-in local authority and an atomic acceptance gate.

The principals in this example are programmed local identities. Possession of
a credential authorizes an API operation; it does not establish a person,
independent expertise, the truth of prose or the behavior of proposed code.
Private credentials and SQLite files are never public example artifacts.
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


DOC_ID = "docs/local-governance.md"
CODE_ID = "code/local_governance.py"


def _require(condition: bool, message: str) -> None:
    """Stop an incomplete product workflow before recording success."""
    if not condition:
        raise RuntimeError("Governance demonstration stopped: " + message)


def _file(identifier: str, kind: str, template: str) -> dict[str, Any]:
    return {"id": identifier, "kind": kind, "template": template, "bindings": []}


def run_governance_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a bounded ordinary workflow, with secrets in a private directory."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Governance demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)
    private = workspace / "private"
    private.mkdir(mode=0o700)
    credentials: dict[str, str] = {}

    def new_credential(label: str) -> str:
        value = prepare_credential(private / (label + ".credential"))
        credentials[label] = value
        return value

    def public(value: Any) -> Any:
        """Defensively omit credential fields from ordinary public snapshots."""
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
        if isinstance(value, list):
            return [public(item) for item in value]
        if isinstance(value, tuple):
            return [public(item) for item in value]
        if isinstance(value, str):
            for secret in credentials.values():
                _require(secret not in value and hashlib.sha256(secret.encode("ascii")).hexdigest() not in value,
                         "a public result contains private credential material")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    store = Store(private / "state.sqlite3")
    baseline = [
        _file(DOC_ID, "document", "# Fictional local contract\n\nThe configured sample limit is 3.\n"),
        _file(CODE_ID, "code", "# Fictional local configuration, stored as data.\nSAMPLE_LIMIT = 3\n"),
    ]
    store.bootstrap(baseline)
    initial_snapshot = store.snapshot()
    bodies = {
        "document": _file(DOC_ID, "document", "# Fictional local contract\n\nThe configured sample limit is 4.\n"),
        "code": _file(CODE_ID, "code", "# Fictional local configuration, stored as data.\nSAMPLE_LIMIT = 4\n"),
        "competing-document": _file(DOC_ID, "document", "# Fictional competing contract\n\nThe configured sample limit is 5.\n"),
    }
    for label, body in bodies.items():
        role = "doc_author" if body["kind"] == "document" else "code_author"
        store.enqueue("governance-" + label, role, {
            "proposal": _proposal(initial_snapshot, body, "Programmed local governance example; proposed code is never executed.")})
    author_barrier = Barrier(min(workers, len(bodies)))

    def author_job(index: int, label: str) -> dict[str, Any]:
        if index < min(workers, len(bodies)):
            author_barrier.wait(timeout=30)
        role = "doc_author" if bodies[label]["kind"] == "document" else "code_author"
        author = "programmed-document-author" if role == "doc_author" else "programmed-code-author"
        task = store.claim_task(author, roles=[role], lease_seconds=60)
        _require(task is not None, "an author task was unavailable")
        submitted = store.submit(task["task_id"], author, task["token"], task["payload"]["proposal"])
        return {"label": task["task_id"].removeprefix("governance-"), "author": author,
                "thread": current_thread().name, "task_id": task["task_id"], **submitted}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="governance-author") as executor:
        futures = [executor.submit(author_job, index, label) for index, label in enumerate(bodies)]
        authors = {item["label"]: item for item in (future.result() for future in futures)}
    store.enqueue("governance-compose", "integrator", {
        "components": [{"proposal_id": authors[label]["proposal_id"], "digest": authors[label]["digest"]}
                       for label in ("document", "code")],
        "rationale": "Compose the exact fictional document and code candidates for local authority review.",
    })
    task = store.claim_task("programmed-integrator", roles=["integrator"], lease_seconds=60)
    _require(task is not None, "the integration task was unavailable")
    composite = store.compose(task["task_id"], "programmed-integrator", task["token"])
    main_id, main_digest = composite["proposal_id"], composite["digest"]
    competing = authors["competing-document"]
    initial_formal_reviews = {
        "main": store.review(main_id, reviewer="pre-policy-formal-agent"),
        "competing": store.review(competing["proposal_id"], reviewer="pre-policy-formal-agent"),
    }
    _require(all(item["approved"] for item in initial_formal_reviews.values()), "an initial formal review failed")

    policy = {"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True}
    admin = new_credential("admin")
    enabled = store.governance_enable(policy, "programmed-administrator", admin)
    generation = 1
    expires_at = time.time() + 3600
    grants: dict[str, dict[str, Any]] = {}

    def grant(label: str, principal_id: str, role: str, artifact_ids: list[str]) -> str:
        token = new_credential(label)
        grants[label] = store.governance_grant(principal_id, role, artifact_ids, expires_at,
                                             credential=admin, new_credential=token,
                                             operation_id="grant-" + label)
        return token

    reviewer_a = grant("reviewer-a", "programmed-reviewer-a", "reviewer", [DOC_ID, CODE_ID])
    reviewer_a_extra = grant("reviewer-a-extra", "programmed-reviewer-a", "reviewer", [DOC_ID, CODE_ID])
    reviewer_b = grant("reviewer-b", "programmed-reviewer-b", "reviewer", [DOC_ID, CODE_ID])
    coordinator = grant("coordinator", "programmed-coordinator", "coordinator", [DOC_ID, CODE_ID])
    document_only = grant("document-only", "programmed-document-only-reviewer", "reviewer", [DOC_ID])
    operations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    decisions: dict[tuple[str, str, str], dict[str, Any]] = {}
    decision_history: list[dict[str, Any]] = []

    def expected_exception(label: str, operation: Callable[[], Any],
                           expected_code: str = "governance_unauthorized") -> dict[str, Any]:
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == expected_code, "unexpected API rejection for " + label)
            result = {"label": label, "rejected": True, "code": error.code}
            operations.append(result)
            return result
        raise RuntimeError("Governance demonstration stopped: expected API rejection for " + label)

    def accept(label: str, *, accepted: bool, token: str = coordinator,
               proposal_id: str = main_id, expected_generation: int | None = None,
               expected_code: str | None = "governance_quorum_missing") -> dict[str, Any]:
        result = store.accept(proposal_id, credential=token,
                              expected_policy_generation=generation if expected_generation is None else expected_generation)
        _require(result["accepted"] is accepted, "unexpected acceptance state for " + label)
        if not accepted and expected_code:
            _require(any(item["code"] == expected_code for item in result["reasons"]),
                     "the expected blocking reason is missing for " + label)
        operations.append({"label": label, "result": result})
        return result

    def eligible_observation(token: str = coordinator) -> dict[str, Any]:
        result = store.governance_observe(main_id, credential=token, expected_policy_generation=generation)
        _require(result["eligible"], "a fully refreshed governance observation is unexpectedly blocked")
        return result

    def stage(label: str, explanation: str, result: Any = None) -> None:
        observation = store.governance_status(main_id)
        filename = f"stage-{len(stages) + 1:02d}-{label}.json"
        item = {"label": label, "explanation": explanation, "runtime_revision": store.status()["revision"],
                "governance": observation, "operation_result": result}
        write_json(filename, item)
        stages.append({"label": label, "explanation": explanation, "artifact": filename,
                       "runtime_revision": item["runtime_revision"]})

    def decide(label: str, principal: str, token: str, artifact_id: str, verdict: str = "approve",
               proposal_id: str = main_id, proposal_digest: str = main_digest) -> dict[str, Any]:
        key = (proposal_id, principal, artifact_id)
        previous = decisions.get(key)
        result = store.governance_decide(
            proposal_id, artifact_id, verdict,
            "Programmed local decision for " + label + "; no human review, prose truth or code behavior is asserted.",
            credential=token, expected_proposal_digest=proposal_digest,
            expected_policy_generation=generation,
            expected_previous_decision=previous["decision_id"] if previous else None,
            operation_id="decision-" + label,
        )
        decisions[key] = result
        decision_history.append({"label": label, "result": result})
        return result

    expected_exception("formal-review-without-credential", lambda: store.review(main_id))
    expected_exception("accept-without-credential", lambda: store.accept(main_id))
    expected_exception("administrator-is-not-coordinator", lambda: store.accept(
        main_id, credential=admin, expected_policy_generation=generation))
    formal_review = store.review(main_id, credential=reviewer_a)
    _require(formal_review["approved"], "credentialed formal review unexpectedly failed")
    expected_exception("document-only-reviewer-cannot-review-code", lambda: store.governance_decide(
        main_id, CODE_ID, "approve", "Programmed attempt outside the credential scope.",
        credential=document_only, expected_proposal_digest=main_digest,
        expected_policy_generation=generation, expected_previous_decision=None, operation_id="outside-scope"))
    stage("no-editorial-approvals", "A formal review and management credential do not satisfy the per-write editorial quorum.",
          accept("no-editorial-approvals", accepted=False))
    for artifact_id, suffix in ((DOC_ID, "doc"), (CODE_ID, "code")):
        decide("a-initial-" + suffix, "a", reviewer_a, artifact_id)
        decide("a-extra-token-" + suffix, "a", reviewer_a_extra, artifact_id)
    one_principal = accept("one-principal-two-credentials", accepted=False)
    _require(all(len(item["approval_principals"]) == 1 for item in one_principal["governance"]["writes"]),
             "two credentials of one principal counted as independent approvals")
    stage("one-principal-two-credentials", "Two credentials for the same principal count as one reviewer per write.",
          one_principal)
    decide("b-document-only", "b", reviewer_b, DOC_ID)
    stage("code-missing-second-reviewer", "The document has two reviewers; code still lacks its own second approval.",
          accept("code-missing-second-reviewer", accepted=False))
    first_b_code = decide("b-code-first", "b", reviewer_b, CODE_ID)
    observation = eligible_observation()
    stage("all-writes-approved", "Every write has two distinct active principals; this is an observation, not acceptance.", observation)
    decide("b-code-request-changes", "b", reviewer_b, CODE_ID, verdict="request_changes")
    stage("request-changes-veto", "The latest request_changes replaces the earlier approval and blocks acceptance.",
          accept("request-changes-veto", accepted=False, expected_code="governance_changes_requested"))
    stale_cas = expected_exception("stale-decision-cas", lambda: store.governance_decide(
        main_id, CODE_ID, "approve", "Programmed stale replacement attempt.", credential=reviewer_b,
        expected_proposal_digest=main_digest, expected_policy_generation=generation,
        expected_previous_decision=first_b_code["decision_id"], operation_id="stale-decision-cas"),
        expected_code="governance_decision_conflict")
    decide("b-code-after-veto", "b", reviewer_b, CODE_ID)
    stage("veto-explicitly-replaced", "Only a replacement naming the current decision clears this principal's veto.",
          {"stale_cas": stale_cas, "observation": eligible_observation()})
    revoked_b = store.governance_revoke(grants["reviewer-b"]["grant_id"], credential=admin)
    stage("reviewer-revoked", "Revocation invalidates this grant's former approvals before acceptance.",
          accept("reviewer-revoked", accepted=False))
    reviewer_b = grant("reviewer-b-regrant", "programmed-reviewer-b", "reviewer", [DOC_ID, CODE_ID])
    stage("regrant-does-not-resurrect-decisions", "A new credential for the same principal does not reactivate old grant-bound decisions.",
          accept("regrant-does-not-resurrect-decisions", accepted=False))
    for artifact_id, suffix in ((DOC_ID, "doc"), (CODE_ID, "code")):
        decide("b-regrant-fresh-" + suffix, "b", reviewer_b, artifact_id)
    stage("fresh-regranted-decisions", "New decisions explicitly pin the new grant and the current policy generation.",
          eligible_observation())
    changed_policy = store.governance_set_policy(policy, credential=admin, expected_generation=generation)
    generation += 1
    stage("policy-generation-changed", "A new policy generation invalidates old-generation decisions even when its fields are equal.",
          accept("policy-generation-changed", accepted=False))
    for principal, token in (("a", reviewer_a), ("b", reviewer_b)):
        for artifact_id, suffix in ((DOC_ID, "doc"), (CODE_ID, "code")):
            decide(principal + "-generation-two-" + suffix, principal, token, artifact_id)
    stage("fresh-current-generation", "Both reviewers recorded new decisions against policy generation 2.",
          eligible_observation())
    accepted = accept("atomic-acceptance", accepted=True)
    _require(accepted["revision"] == 2 and not accepted["replayed"], "the main candidate did not advance exactly one revision")
    stage("accepted", "Authorization, quorum, latest decisions and old runtime guards are checked inside acceptance.", accepted)
    replay = accept("accepted-replay", accepted=True)
    _require(replay["replayed"] and replay["revision"] == 2, "authorized replay created another revision")
    revoked_coordinator = store.governance_revoke(grants["coordinator"]["grant_id"], credential=admin)
    revoked_replay = expected_exception("revoked-coordinator-cannot-replay", lambda: store.accept(
        main_id, credential=coordinator, expected_policy_generation=generation))
    final_coordinator = grant("coordinator-final", "programmed-coordinator", "coordinator", [DOC_ID, CODE_ID])
    final_replay = accept("new-authorized-coordinator-replay", accepted=True, token=final_coordinator)
    _require(final_replay["replayed"] and final_replay["revision"] == 2, "historical receipt replay advanced revision")
    stage("authorized-historical-receipt", "A revoked coordinator cannot replay; a newly authorized one receives the original accepted revision.",
          {"revoked_attempt": revoked_replay, "authorized_replay": final_replay})
    for principal, token in (("a", reviewer_a), ("b", reviewer_b)):
        decide(principal + "-competing-document", principal, token, DOC_ID,
               proposal_id=competing["proposal_id"], proposal_digest=competing["digest"])
    stale_candidate = accept("stale-competing-candidate", accepted=False, token=final_coordinator,
                             proposal_id=competing["proposal_id"], expected_code="stale_read")
    _require(any(item["code"] == "stale_read" for item in stale_candidate["reasons"]),
             "editorial approvals bypassed the competing candidate's old read set")
    write_json("competing-final-governance.json", store.governance_status(competing["proposal_id"]))
    stage("old-runtime-guards-preserved", "Fresh approvals cannot override the competing candidate's stale document read.", stale_candidate)

    final_status = store.status()
    final_snapshot = store.snapshot()
    public_governance = store.governance_status()
    proposals = {label: store.proposal(item["proposal_id"]) for label, item in authors.items()}
    proposals["composite"] = store.proposal(main_id)
    events = store.events()
    _require(final_status["revision"] == 2 and final_status["proposals"].get("accepted") == 1,
             "unexpected final acceptance count or revision")
    for name, value in {
        "initial-state.json": initial_snapshot, "final-state.json": final_snapshot,
        "proposals.json": proposals, "composition.json": composite, "authors.json": authors,
        "formal-reviews.json": {"before_enable": initial_formal_reviews, "after_enable": formal_review},
        "governance-final.json": public_governance, "grants.json": grants,
        "decisions.json": decision_history, "operations.json": operations, "events.json": events,
        "policy-transitions.json": {"enabled": enabled, "updated": changed_policy,
                                    "reviewer_revocation": revoked_b, "coordinator_revocation": revoked_coordinator},
    }.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "kind": "local_credential_governance_product_example", "workers": workers,
        "actors": {"author_names_self_declared": True, "authenticated_local_capability_principals": True,
                   "persons_authenticated": False, "human_review_recorded": False,
                   "external_llm_used": False, "proposed_code_executed": False},
        "policy": policy, "policy_generation": generation,
        "stages": stages, "initial_runtime_revision": initial_snapshot["revision"],
        "final_runtime_status": final_status,
        "summary": {"component_proposal_count": len(authors), "composite_proposal_count": 1,
                    "decision_count": len(decision_history), "stage_count": len(stages),
                    "expected_api_rejection_count": sum(item.get("rejected", False) for item in operations),
                    "runtime_event_count": len(events), "accepted_proposal_count": 1,
                    "release_created": False, "credential_material_exported": False,
                    "measured_capacity": False, "semantic_equivalence_proved": False},
        "credential_file_layout": {
            "administrator": "private/admin.credential",
            "active_coordinator": "private/coordinator-final.credential",
            "active_reviewer_a": "private/reviewer-a.credential",
            "active_reviewer_b": "private/reviewer-b-regrant.credential",
            "private_database": "private/state.sqlite3",
        },
        "limitations": [
            "The database owner remains trusted and can bypass the Python API by changing local files.",
            "A credential proves possession of a local capability, not a person's identity, SSO, model identity or expertise.",
            "Distinct principal IDs do not establish independent people, models, evidence or organizations.",
            "Author task names remain self-declared; capability principals authorize reviewer and coordinator operations only.",
            "All decisions are programmed examples; no human review or external LLM call is claimed.",
            "The fictional document and code are stored data; the code is parsed by existing formal guards but never executed.",
            "The policy covers every explicit write; it does not find missing promises, prove natural-language truth or enforce semantic reports.",
            "Public snapshots are sequential observations, not a common transaction; Store.accept is the atomic authorization boundary.",
            "The worker pool size is configuration, not measured throughput or service capacity.",
            "Credential files and the database stay private; published example artifacts contain no bearer secret or credential hash.",
        ],
    }
    write_json("report.json", report)
    lines = [
        "# Выполненный пример локальных полномочий и обязательного принятия", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки политики и решений используют UTC.", "",
        "Два программных автора подготовили три вклада; интегратор объединил документ и код в один кандидат.",
        "Политика требует двух разных действующих принципалов для каждой записи файла и отдельного координатора.",
        "Имена авторов остаются самозаявленными. Полномочия редакторов и координатора определяются владением",
        "локальным секретом с ограниченной областью; люди, модели и независимость экспертизы не удостоверяются.", "",
        "## Последовательность", "",
        "| Состояние | Наблюдение | Ревизия |", "|---|---|---:|",
    ]
    for item in stages:
        lines.append(f"| [{item['label']}]({item['artifact']}) | {item['explanation']} | {item['runtime_revision']} |")
    lines += [
        "", "## Практический результат", "",
        "Один принципал с двумя секретами не создаёт два голоса. Одобрение документа не покрывает код.",
        "`request_changes` заменяет прежний голос; устаревшая попытка замены отклоняется по CAS.",
        "Отзыв полномочия лишает старое решение силы, а новое полномочие того же принципала требует нового решения.",
        "После смены поколения политики нужны новые решения для каждого файла. Решение привязано к точному",
        "предложению, его digest, поколению политики и выдавшему полномочие grant.", "",
        "Основной кандидат принят один раз: ревизия 1 → 2. Разрешённый повтор возвращает прежнюю квитанцию;",
        "повтор с отозванным полномочием координатора отклоняется. Конкурирующий документ получил новые",
        "одобрения, но остался заблокирован существующей проверкой `stale_read`.", "",
        "## Публичные материалы", "",
        "- [Полный отчёт](report.json), [предложения](proposals.json), [композиция](composition.json).",
        "- [Операции и отклонения](operations.json), [редакторские решения](decisions.json), [формальные проверки](formal-reviews.json).",
        "- [Полномочия без секретов](grants.json), [состояние политики](governance-final.json), [смена политики и отзывы](policy-transitions.json).",
        "- [Начальное состояние](initial-state.json), [конечное состояние](final-state.json), [события](events.json).",
        "- [Состояние конкурирующего предложения](competing-final-governance.json), [авторы](authors.json).", "",
        "## Границы", "",
        "Это локальный прототип с доверенным владельцем SQLite. Он не реализует SSO, распределённую идентичность,",
        "проверку независимости участников или неизменяемое внешнее свидетельство. Голосование не устанавливает",
        "истинность текста и не подключает advisory-отчёты семантики к принятой политике.", "",
        "Секреты записаны отдельно с правами 0600 в каталог `private/` с правами 0700 до выдачи полномочий.",
        "Этот каталог, база SQLite и её служебные файлы не входят в публичные материалы.",
        "Публикация Git и runtime-принятие остаются отдельными действиями; release не создавался.", "",
        f"Пул Python workers: {workers}; решений: {len(decision_history)}; событий runtime: {len(events)}.",
        "Размер пула не является измеренной ёмкостью сервиса или числом LLM.", "",
    ]
    with (workspace / "report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(lines))
    return public(report)
