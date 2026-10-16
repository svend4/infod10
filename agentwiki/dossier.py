"""Advisory, proposal-bound editorial snapshots; never an acceptance grant.

The runtime and editorial journal are captured sequentially. A dossier binds
their observed contents and exact file templates, but cannot make their two
transactions atomic or authenticate the people named in them.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from .editorial import EditorialJournal, assess_export, verify_journal_export
from .semantic import validate_sources, validate_statement
from .store import HASH, Store, WorkbenchError, canonical, digest


BRIDGE_RULES_VERSION = "dossier-1"
MAX_DOSSIER_BYTES = 25_000_000
DOSSIER_KEYS = {
    "schema_version", "bridge_rules_version", "created_at", "proposal_id",
    "packet_id", "artifact_id", "pair", "runtime_context", "journal_snapshot",
    "observation", "dossier_id", "dossier_digest",
}
CONTEXT_KEYS = {
    "schema_version", "runtime_rules_version", "observed_revision", "proposal",
    "artifacts", "actual_readset", "actual_queries", "sources", "formal_review",
    "runtime_errors", "formal_errors",
}
OBSERVATION_KEYS = {
    "proposal_id", "proposal_digest", "packet_id", "packet_digest", "artifact_id",
    "observed_revision", "proposal_status", "runtime_basis_current", "runtime_basis_digest",
    "formal_head", "decision_heads", "binding", "coverage", "runtime_errors", "formal_errors",
    "editorial", "blockers", "state", "requires_human_review", "auto_accept", "atomic_capture",
    "limits", "actors",
}
LIMITS = [
    "Runtime and journal snapshots are sequential observations, not one atomic transaction.",
    "Exact template matching binds bytes only; statement fields and their mapping to prose are caller-declared.",
    "Actor identities and kinds are self-declared; agreement does not prove independence or truth.",
    "No dossier or recheck grants Store.accept or publication rights.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_dossier", message)


def _copy(value: Any) -> Any:
    try:
        return json.loads(canonical(value))
    except (ValueError, TypeError, RecursionError) as error:
        raise WorkbenchError("invalid_dossier", "Expected finite JSON data") from error


def _pair(value: Any) -> None:
    _require(isinstance(value, dict) and set(value) == {"before", "after", "sources"}, "Expected before, after and sources")
    validate_statement(value["before"])
    validate_statement(value["after"])
    validate_sources(value["sources"])


def _context(value: Any) -> None:
    """Check the captured context's internal links, without opening a database."""
    _require(isinstance(value, dict), "Runtime context must be an object")
    version = value.get("schema_version")
    _require(type(version) is int and version in {1, 2}, "Unsupported runtime context schema")
    _require(set(value) == CONTEXT_KEYS | ({"components"} if version == 2 else set()), "Unexpected runtime context fields")
    _require(isinstance(value["runtime_rules_version"], str) and bool(value["runtime_rules_version"]), "Missing runtime rules")
    _require(type(value["observed_revision"]) is int and value["observed_revision"] >= 0, "Invalid observed revision")
    proposal = value["proposal"]
    _require(isinstance(proposal, dict) and set(proposal) == {"id", "task_id", "author", "token", "payload", "digest", "status", "accepted_revision"}, "Unexpected proposal fields")
    _require(all(isinstance(proposal[key], str) and proposal[key] for key in ["id", "task_id", "author"]), "Invalid proposal identity")
    _require(type(proposal["token"]) is int and proposal["token"] >= 1, "Invalid proposal token")
    _require(proposal["status"] in {"proposed", "accepted"}, "Invalid proposal status")
    _require(proposal["accepted_revision"] is None or type(proposal["accepted_revision"]) is int, "Invalid accepted revision")
    payload = proposal["payload"]
    _require(isinstance(payload, dict) and set(payload) == {"readset", "queries", "writes", "rationale"}, "Unexpected proposal payload")
    _require(isinstance(payload["readset"], dict) and isinstance(payload["queries"], dict), "Invalid dependency maps")
    _require(isinstance(payload["writes"], list) and bool(payload["writes"]), "Missing proposal writes")
    _require(isinstance(payload["rationale"], str) and bool(payload["rationale"].strip()), "Missing proposal rationale")
    _require(proposal["digest"] == digest(payload), "Proposal payload digest mismatch")
    writes = payload["writes"]
    _require(all(isinstance(body, dict) and isinstance(body.get("id"), str) for body in writes), "Invalid write bodies")
    write_ids = [body["id"] for body in writes]
    _require(len(set(write_ids)) == len(write_ids), "Duplicate proposal write")
    _require(set(write_ids) <= set(payload["readset"]), "Proposal writes lack their read bases")
    if version == 2:
        components = value["components"]
        _require(isinstance(components, list) and len(components) <= 32, "Invalid composition components")
        source_ids, covered_ids, references = [], [], []
        for position, component in enumerate(components, 1):
            _require(isinstance(component, dict) and set(component) == {"position", "proposal_id", "digest", "write_ids"}, "Unexpected component fields")
            _require(type(component["position"]) is int and component["position"] == position, "Component order mismatch")
            _require(isinstance(component["proposal_id"], str) and component["proposal_id"] and component["proposal_id"] != proposal["id"], "Invalid component identity")
            _require(isinstance(component["digest"], str) and bool(HASH.fullmatch(component["digest"])), "Invalid component digest")
            ids = component["write_ids"]
            _require(isinstance(ids, list) and bool(ids) and all(isinstance(key, str) for key in ids) and ids == sorted(set(ids)), "Invalid component writes")
            source_ids.append(component["proposal_id"])
            covered_ids.extend(ids)
            references.append({"proposal_id": component["proposal_id"], "digest": component["digest"], "write_ids": ids})
        _require(len(source_ids) == len(set(source_ids)), "Duplicate component identity")
        if components:
            _require(len(covered_ids) == len(set(covered_ids)) and set(covered_ids) == set(write_ids), "Composition lineage does not cover writes exactly")
            rationale = json.loads(payload["rationale"])
            _require(isinstance(rationale, dict) and set(rationale) == {"text", "components"} and isinstance(rationale["text"], str) and bool(rationale["text"].strip()), "Invalid composition rationale")
            _require(payload["rationale"] == canonical(rationale), "Composition rationale must use canonical JSON")
            _require(digest(rationale["components"]) == digest(references), "Composition references differ from proposal digest basis")
    for key in ["artifacts", "actual_readset", "actual_queries", "sources"]:
        _require(isinstance(value[key], dict), "Invalid runtime context map")
    _require(set(value["actual_readset"]) == set(payload["readset"]), "Read observations do not cover readset")
    _require(set(value["actual_queries"]) == set(payload["queries"]), "Query observations do not cover queries")
    _require(set(value["artifacts"]) == set(payload["readset"]) | set(write_ids), "Artifact observations do not cover proposal")
    for identifier, expected in payload["readset"].items():
        _require(expected is None or isinstance(expected, str) and bool(HASH.fullmatch(expected)), "Invalid expected read digest")
        body = value["artifacts"][identifier]
        _require(body is None or isinstance(body, dict) and body.get("id") == identifier, "Invalid observed artifact")
        _require(value["actual_readset"][identifier] == (digest(body) if body is not None else None), "Observed read digest mismatch")
    for mapping in [payload["queries"], value["actual_queries"]]:
        _require(all(isinstance(item, str) and bool(HASH.fullmatch(item)) for item in mapping.values()), "Invalid query digest")
    for identifier, source in value["sources"].items():
        _require(isinstance(source, dict) and set(source) == {"id", "text", "digest"} and source["id"] == identifier and isinstance(source["text"], str), "Invalid source snapshot")
        _require(source["digest"] == hashlib.sha256(source["text"].encode("utf-8")).hexdigest(), "Runtime source digest mismatch")
    _require(value["formal_review"] is None or isinstance(value["formal_review"], dict), "Invalid formal review")
    if value["formal_review"] is not None:
        _require(type(value["formal_review"].get("review_id")) is int, "Missing formal review identity")
    for key in ["runtime_errors", "formal_errors"]:
        _require(isinstance(value[key], list) and all(isinstance(item, dict) and isinstance(item.get("code"), str) for item in value[key]), "Invalid runtime reasons")


def _packet(snapshot: dict[str, Any], packet_id: str) -> dict[str, Any]:
    for packet in snapshot["packets"]:
        if packet["packet_id"] == packet_id:
            return packet
    raise WorkbenchError("unknown_packet", packet_id)


def _formal_head(context: dict[str, Any]) -> dict[str, Any] | None:
    review = context["formal_review"]
    return None if review is None else {"review_id": review.get("review_id"), "report_digest": digest(review)}


def _basis(context: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    proposal = context["proposal"]
    after = next((body for body in proposal["payload"]["writes"] if body["id"] == artifact_id), None)
    basis = {
        "runtime_rules_version": context["runtime_rules_version"],
        "proposal_digest": proposal["digest"],
        "actual_readset": context["actual_readset"], "actual_queries": context["actual_queries"],
        "selected_before": context["artifacts"].get(artifact_id), "selected_write": after,
    }
    if context.get("components"):
        basis["components"] = context["components"]
    return basis


def _coverage(context: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    write_ids = {body["id"] for body in context["proposal"]["payload"]["writes"]}
    uncovered = sorted(write_ids - {artifact_id})
    return {"selected_write_ids": [artifact_id] if artifact_id in write_ids else [],
            "uncovered_write_ids": uncovered, "complete": artifact_id in write_ids and not uncovered,
            "scope": "write_ids_only_not_semantic_coverage"}


def _binding(context: dict[str, Any], packet: dict[str, Any],
             pair: dict[str, Any], artifact_id: str) -> dict[str, Any]:
    writes = {body["id"]: body for body in context["proposal"]["payload"]["writes"]}
    before = context["artifacts"].get(artifact_id)
    after = writes.get(artifact_id)
    binding_errors = []
    if before is None or before.get("kind") not in {"document", "code"}:
        binding_errors.append({"code": "existing_file_required", "artifact_id": artifact_id})
    if after is None or after.get("kind") not in {"document", "code"}:
        binding_errors.append({"code": "file_write_required", "artifact_id": artifact_id})
    if before is not None and after is not None and before.get("kind") != after.get("kind"):
        binding_errors.append({"code": "artifact_kind_changed", "artifact_id": artifact_id})
    for label, candidate in [("packet", packet["pair"]), ("current_input", pair)]:
        for side, body in [("before", before), ("after", after)]:
            if body is not None and candidate[side]["text"] != body.get("template"):
                binding_errors.append({"code": "template_mismatch", "input": label, "side": side, "artifact_id": artifact_id})
    return {"mode": "exact_template", "matches": not binding_errors, "errors": binding_errors,
            "semantic_mapping": "caller_declared_unverified",
            "scope": "template_text_only_not_rendered_output_or_all_metadata"}


def _source_conflicts(context: dict[str, Any], packet: dict[str, Any],
                      pair: dict[str, Any]) -> list[dict[str, Any]]:
    conflicts = []
    for label, candidate in [("packet", packet["pair"]), ("current_input", pair)]:
        for identifier, source in candidate["sources"].items():
            runtime_source = context["sources"].get(identifier)
            if runtime_source is not None and source["sha256"] != runtime_source["digest"]:
                conflicts.append({"code": "source_snapshot_conflict", "input": label, "source_id": identifier})
    return conflicts


def _blockers(context: dict[str, Any], binding: dict[str, Any], coverage: dict[str, Any],
              source_conflicts: list[dict[str, Any]], editorial: dict[str, Any]) -> list[dict[str, Any]]:
    blockers = [dict(item) for item in context["runtime_errors"] + context["formal_errors"]]
    blockers.extend(binding["errors"])
    blockers.extend(source_conflicts)
    if context["proposal"]["status"] != "proposed":
        blockers.append({"code": "proposal_already_accepted"})
    if coverage["uncovered_write_ids"]:
        blockers.append({"code": "uncovered_writes", "artifact_ids": coverage["uncovered_write_ids"]})
    for code in editorial["blockers"]:
        # Human review is always required, including for a technically current
        # dossier. Its absence does not mislabel current data as stale.
        if code != "human_review_not_recorded":
            blockers.append({"code": code})
    return blockers


def _observe(context: dict[str, Any], snapshot: dict[str, Any], packet_id: str,
             artifact_id: str, pair: dict[str, Any]) -> dict[str, Any]:
    _context(context)
    verify_journal_export(snapshot)
    _pair(pair)
    packet = _packet(snapshot, packet_id)
    editorial = assess_export(snapshot, packet_id, pair)
    proposal = context["proposal"]
    binding = _binding(context, packet, pair, artifact_id)
    coverage = _coverage(context, artifact_id)
    blockers = _blockers(context, binding, coverage, _source_conflicts(context, packet, pair), editorial)
    return {
        "proposal_id": proposal["id"], "proposal_digest": proposal["digest"],
        "packet_id": packet_id, "packet_digest": packet["packet_digest"],
        "artifact_id": artifact_id, "observed_revision": context["observed_revision"],
        "proposal_status": proposal["status"],
        "runtime_basis_current": not context["runtime_errors"], "runtime_basis_digest": digest(_basis(context, artifact_id)),
        "formal_head": _formal_head(context),
        "decision_heads": {item["reviewer"]: item["decision_id"] for item in editorial["latest_decisions"]},
        "binding": binding, "coverage": coverage,
        "actors": {"runtime_author_id": proposal["author"], "packet_proposer": packet["proposer"],
                   "authenticated": False, "independence_verified": False},
        "runtime_errors": context["runtime_errors"], "formal_errors": context["formal_errors"],
        "editorial": editorial, "blockers": blockers,
        "state": "blocked" if blockers else "current_for_human_review",
        "requires_human_review": True, "auto_accept": False,
        "atomic_capture": False, "limits": list(LIMITS),
    }


def create_dossier(store: Store, journal: EditorialJournal, proposal_id: str,
                   packet_id: str, artifact_id: str, current_pair: dict[str, Any]) -> dict[str, Any]:
    """Capture an advisory dossier even when it contains explicit blockers."""
    _require(all(isinstance(item, str) and item for item in [proposal_id, packet_id, artifact_id]), "Nonempty object identifiers required")
    pair = _copy(current_pair)
    _pair(pair)
    context = store.proposal_context(proposal_id)
    snapshot = journal.export_snapshot()
    observation = _observe(context, snapshot, packet_id, artifact_id, pair)
    payload = {
        "schema_version": 1, "bridge_rules_version": BRIDGE_RULES_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "proposal_id": proposal_id, "packet_id": packet_id, "artifact_id": artifact_id,
        "pair": pair, "runtime_context": context, "journal_snapshot": snapshot,
        "observation": observation,
    }
    address = digest(payload)
    dossier = {**payload, "dossier_id": "dos_" + address, "dossier_digest": address}
    _validate(dossier)
    return dossier


def _validate(value: Any) -> None:
    try:
        _require(isinstance(value, dict) and set(value) == DOSSIER_KEYS, "Unexpected dossier fields")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Unsupported dossier schema")
        _require(isinstance(value["bridge_rules_version"], str) and bool(value["bridge_rules_version"]), "Missing bridge rules")
        _require(isinstance(value["created_at"], str), "Missing creation time")
        timestamp = datetime.fromisoformat(value["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0, "Creation time must have a UTC offset")
        _require(all(isinstance(value[key], str) and value[key] for key in ["proposal_id", "packet_id", "artifact_id"]), "Invalid dossier identifiers")
        payload = {key: item for key, item in value.items() if key not in {"dossier_id", "dossier_digest"}}
        _require(value["dossier_digest"] == digest(payload) and value["dossier_id"] == "dos_" + value["dossier_digest"], "Dossier content address mismatch")
        _require(len(canonical(value).encode("utf-8")) <= MAX_DOSSIER_BYTES, "Dossier exceeds 25 MB")
        _pair(value["pair"])
        _context(value["runtime_context"])
        verify_journal_export(value["journal_snapshot"])
        packet = _packet(value["journal_snapshot"], value["packet_id"])
        observation = value["observation"]
        _require(isinstance(observation, dict) and set(observation) == OBSERVATION_KEYS, "Unexpected observation fields")
        _require(observation.get("proposal_id") == value["proposal_id"] == value["runtime_context"]["proposal"]["id"], "Proposal identity mismatch")
        _require(observation.get("proposal_digest") == value["runtime_context"]["proposal"]["digest"], "Proposal observation mismatch")
        _require(observation.get("packet_id") == value["packet_id"] and observation.get("packet_digest") == packet["packet_digest"], "Packet observation mismatch")
        _require(observation.get("artifact_id") == value["artifact_id"], "Artifact observation mismatch")
        _require(observation.get("requires_human_review") is True and observation.get("auto_accept") is False and observation.get("atomic_capture") is False, "Invalid dossier authority flags")
        _require(observation.get("state") in {"current_for_human_review", "blocked"}, "Invalid advisory state")
        _require(isinstance(observation.get("decision_heads"), dict) and isinstance(observation.get("blockers"), list), "Invalid observation summary")
        _require(all(isinstance(observation[key], dict) for key in ["editorial", "binding", "coverage", "actors"]), "Invalid observation objects")
        _require(all(isinstance(observation[key], list) for key in ["runtime_errors", "formal_errors", "limits"]), "Invalid observation lists")
        _require(type(observation["runtime_basis_current"]) is bool and isinstance(observation["runtime_basis_digest"], str) and bool(HASH.fullmatch(observation["runtime_basis_digest"])), "Invalid runtime basis observation")
        _require(observation["formal_head"] == _formal_head(value["runtime_context"]), "Formal review head mismatch")
        _require(observation["runtime_errors"] == value["runtime_context"]["runtime_errors"] and observation["formal_errors"] == value["runtime_context"]["formal_errors"], "Runtime reasons mismatch")
        _require(observation["runtime_basis_current"] == (not observation["runtime_errors"]), "Runtime basis status mismatch")
        _require(observation["runtime_basis_digest"] == digest(_basis(value["runtime_context"], value["artifact_id"])), "Runtime basis digest mismatch")
        _require(observation["coverage"] == _coverage(value["runtime_context"], value["artifact_id"]), "Write coverage summary mismatch")
        _require(observation["observed_revision"] == value["runtime_context"]["observed_revision"] and observation["proposal_status"] == value["runtime_context"]["proposal"]["status"], "Runtime state observation mismatch")
        _require(observation["state"] == ("blocked" if observation["blockers"] else "current_for_human_review"), "Advisory state differs from blockers")
        _require(observation["actors"].get("authenticated") is False and observation["actors"].get("independence_verified") is False, "Invalid actor authority flags")
        _require(observation["actors"].get("runtime_author_id") == value["runtime_context"]["proposal"]["author"] and observation["actors"].get("packet_proposer") == packet["proposer"], "Actor summary mismatch")
        editorial = observation["editorial"]
        _require({"packet_id", "packet_digest", "input_digest", "current_input_digest", "freshness", "is_current", "current_input_provided", "current_input_checked", "review_state", "latest_decisions", "current_basis_decisions", "history_count", "reviewer_count", "human_review_recorded", "requires_human_review", "current_human_review_recorded", "auto_accept", "blockers", "counts", "limits"} == set(editorial), "Unexpected editorial observation fields")
        _require(editorial["packet_id"] == value["packet_id"] and editorial["packet_digest"] == packet["packet_digest"] and editorial["input_digest"] == packet["input_digest"], "Editorial packet summary mismatch")
        _require(editorial["requires_human_review"] is True and editorial["auto_accept"] is False, "Invalid editorial authority flags")
        _require(isinstance(editorial["latest_decisions"], list) and isinstance(editorial["current_basis_decisions"], list) and isinstance(editorial["blockers"], list), "Invalid editorial observation lists")
        history = [item for item in value["journal_snapshot"]["decisions"] if item["packet_id"] == value["packet_id"]]
        latest_by_reviewer = {item["reviewer"]: item for item in history}
        latest = [latest_by_reviewer[reviewer] for reviewer in sorted(latest_by_reviewer)]
        heads = {item["reviewer"]: item["decision_id"] for item in latest}
        _require(observation["decision_heads"] == heads, "Decision head summary mismatch")
        _require(digest(editorial["latest_decisions"]) == digest(latest), "Latest editorial decisions differ from journal")
        _require(type(editorial["history_count"]) is int and editorial["history_count"] == len(history), "Editorial history count mismatch")
        _require(type(editorial["reviewer_count"]) is int and editorial["reviewer_count"] == len(latest), "Editorial reviewer count mismatch")
        counts = {verdict: sum(item["verdict"] == verdict for item in latest) for verdict in ["approve", "request_changes", "abstain"]}
        _require(isinstance(editorial["counts"], dict) and set(editorial["counts"]) == set(counts) and all(type(item) is int for item in editorial["counts"].values()) and editorial["counts"] == counts, "Editorial verdict counts mismatch")
        freshness = editorial["freshness"]
        _require(freshness in {"current", "stale_rules", "stale_policy", "stale_analysis", "stale_input", "input_not_checked"}, "Unknown editorial freshness")
        expected_current = None if freshness == "input_not_checked" else freshness == "current"
        _require(editorial["is_current"] is expected_current, "Editorial current status mismatch")
        _require(digest(editorial["current_basis_decisions"]) == digest(latest if freshness == "current" else []), "Current editorial decisions differ from freshness")
        human_recorded = any(item["actor_kind"] == "human" for item in latest)
        _require(editorial["human_review_recorded"] is human_recorded and editorial["current_human_review_recorded"] is (human_recorded and freshness == "current"), "Editorial human-review summary mismatch")
        _require(editorial["current_input_provided"] is True and type(editorial["current_input_checked"]) is bool, "Editorial input status mismatch")
        _require(editorial["current_input_digest"] == (digest(value["pair"]) if editorial["current_input_checked"] else None), "Current editorial input digest mismatch")
        approvals = [item for item in latest if item["verdict"] == "approve"]
        objections = [item for item in latest if item["verdict"] == "request_changes"]
        threshold = value["journal_snapshot"]["current_policy"]["agent_agreement_min"]
        _require(type(threshold) is int and threshold >= 1, "Invalid captured agreement threshold")
        expected_state = (
            "disagreement" if approvals and objections else
            "changes_requested" if objections else
            "agent_agreement" if sum(item["actor_kind"] == "agent" for item in approvals) >= threshold else
            "review_recorded" if approvals else "abstained" if latest else "awaiting_reviews"
        )
        _require(editorial["review_state"] == expected_state, "Editorial review state differs from journal")
        required_editorial_blockers = ([] if freshness == "current" else [freshness])
        if not human_recorded:
            required_editorial_blockers.append("human_review_not_recorded")
        if objections:
            required_editorial_blockers.append("unresolved_changes_requested")
        # The historical result of a changed semantic ruleset is preserved.
        # Its current-pair analysis is not rerun by this file integrity loader.
        semantic_blocker = "semantic_context_incomplete"
        if freshness in {"current", "stale_rules", "stale_policy", "input_not_checked"}:
            if packet["analysis"]["relation"] == "undetermined":
                required_editorial_blockers.append(semantic_blocker)
            _require(editorial["blockers"] == required_editorial_blockers, "Editorial blocker summary mismatch")
        else:
            _require(editorial["blockers"] in [required_editorial_blockers, required_editorial_blockers + [semantic_blocker]], "Editorial blocker summary mismatch")
        binding = _binding(value["runtime_context"], packet, value["pair"], value["artifact_id"])
        _require(digest(observation["binding"]) == digest(binding), "Template binding summary mismatch")
        expected_blockers = _blockers(value["runtime_context"], binding, observation["coverage"], _source_conflicts(value["runtime_context"], packet, value["pair"]), editorial)
        _require(digest(observation["blockers"]) == digest(expected_blockers), "Dossier blocker summary mismatch")
    except WorkbenchError as error:
        if error.code == "invalid_dossier":
            raise
        raise WorkbenchError("invalid_dossier", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError) as error:
        raise WorkbenchError("invalid_dossier", "Malformed dossier: " + str(error)) from error


def recheck_dossier(dossier: dict[str, Any], store: Store, journal: EditorialJournal,
                    current_pair: dict[str, Any]) -> dict[str, Any]:
    """Recapture both stores and compare only the dossier's relevant basis."""
    dossier = _copy(dossier)
    _validate(dossier)
    pair = _copy(current_pair)
    context = store.proposal_context(dossier["proposal_id"])
    snapshot = journal.export_snapshot()
    current = _observe(context, snapshot, dossier["packet_id"], dossier["artifact_id"], pair)
    original = dossier["observation"]
    changes = []
    comparisons = [
        ("proposal_digest", "proposal_changed"), ("runtime_basis_digest", "runtime_basis_changed"),
        ("formal_head", "formal_review_head_changed"), ("decision_heads", "decision_heads_changed"),
        ("packet_digest", "packet_changed"), ("proposal_status", "proposal_status_changed"),
    ]
    for field, code in comparisons:
        if digest(original.get(field)) != digest(current[field]):
            changes.append({"code": code})
    if digest(dossier["pair"]) != digest(pair):
        changes.append({"code": "current_input_changed"})
    if dossier["bridge_rules_version"] != BRIDGE_RULES_VERSION:
        changes.append({"code": "bridge_rules_changed"})
    if digest(original["editorial"]) != digest(current["editorial"]):
        # This assessment concerns the selected packet only. Other packets and
        # the whole-journal event head do not invalidate a relevant dossier.
        changes.append({"code": "editorial_assessment_changed"})
    if digest(original.get("blockers")) != digest(current["blockers"]):
        changes.append({"code": "blockers_changed"})
    state = "blocked" if current["blockers"] else "needs_refresh" if changes else "current_for_human_review"
    return {
        "schema_version": 1, "bridge_rules_version": BRIDGE_RULES_VERSION,
        "dossier_id": dossier["dossier_id"], "dossier_digest": dossier["dossier_digest"],
        "freshness": "stale" if changes else "current", "state": state,
        "changes": changes, "blockers": current["blockers"], "current_observation": current,
        "original_observed_revision": dossier["runtime_context"]["observed_revision"],
        "current_observed_revision": context["observed_revision"],
        "journal_snapshot_changed": dossier["journal_snapshot"]["export_digest"] != snapshot["export_digest"],
        "requires_human_review": True, "auto_accept": False, "atomic_capture": False,
        "limits": list(LIMITS),
    }


def load_dossier(path: str | Path) -> dict[str, Any]:
    """Read one bounded, unambiguous standalone JSON dossier."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            _require(key not in result, "Duplicate JSON object key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise WorkbenchError("invalid_dossier", "Non-finite JSON constant")

    with Path(path).open("rb") as stream:
        data = stream.read(MAX_DOSSIER_BYTES + 1)
    _require(len(data) <= MAX_DOSSIER_BYTES, "Dossier file exceeds 25 MB")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("invalid_dossier", "Invalid dossier JSON") from error
    _validate(value)
    return value


def save_dossier(dossier: dict[str, Any], path: str | Path) -> None:
    """Save exclusively: an existing snapshot is never overwritten."""
    captured = _copy(dossier)
    _validate(captured)
    encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_DOSSIER_BYTES, "Formatted dossier exceeds 25 MB")
    with Path(path).open("xb") as stream:
        stream.write(encoded)
