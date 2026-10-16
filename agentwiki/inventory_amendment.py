"""Apply explicit author proposals to a captured promise-inventory rebase.

Every original promise ID and its position survive. Changed descriptors are
proposals for new consideration; a rationale never records a vote or an
authenticated author. Fresh coverage uses only the captured target candidate,
without consulting a live database, executing code, or granting acceptance.

The amendment-1 verifier deliberately retains the frozen rebase-1,
coverage-1, and obligations-1 interpretations. Future rules need a separate
versioned verifier rather than changing saved proofs retrospectively.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from . import coverage, inventory_rebase, obligations
from .store import WorkbenchError, canonical, digest


AMENDMENT_RULES_VERSION = "amendment-1"
MAX_REPORT_BYTES = obligations.MAX_REPORT_BYTES
REPORT_KEYS = {
    "schema_version", "amendment_rules_version", "created_at", "rebase_report",
    "authored_plan", "observation", "report_id", "report_digest",
}
PLAN_KEYS = {"schema_version", "rebase_report_id", "rebase_report_digest", "actions"}
DESCRIPTOR_KEYS = {"id", "description", "anchor", "requirement", "obligation_ids"}
LIMITS = [
    "Only explicit revise or reaffirm proposals for nonreusable source descriptors are considered; reusable descriptors are retained without transferring editorial decisions.",
    "All original promise IDs, positions and count survive, but a revision can change meaning; preserved identity does not establish semantic equivalence of the denominator.",
    "A rationale is author-supplied text, not an authenticated identity, independent evidence, a human decision or permission to accept or publish.",
    "Ready-for-human-review describes fresh anchor, mapping and comparison status of a descriptor; target upstream blockers remain effective independently.",
    "Exact quotation, field extraction and matching concern only caller-declared metadata; their semantic correspondence and completeness over prose are not inferred.",
    "Only the embedded captured target is examined, without live-state observation, a cross-database transaction, code execution or external models.",
    "An exported standard coverage report omits amendment lineage; keep the amendment report alongside it to explain descriptor revisions and reaffirmations.",
    "Hashes establish internal consistency only; every result requires human review and never authorizes automatic acceptance or publication.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_amendment", message)


def _copy(value: Any) -> Any:
    try:
        return obligations._copy(value)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_amendment", str(error)) from error


def _read_json(path: str | Path) -> Any:
    try:
        return obligations._read_json(path)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_amendment", str(error)) from error


def _plan_schema_v1(value: Any) -> None:
    """Check bounded syntax without guessing source membership or intent."""
    _require(isinstance(value, dict) and set(value) == PLAN_KEYS,
             "Unexpected amendment plan fields")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1,
             "Unsupported amendment plan schema")
    address = value["rebase_report_digest"]
    _require(isinstance(address, str) and bool(obligations.HASH.fullmatch(address)),
             "Invalid bound rebase report digest")
    _require(value["rebase_report_id"] == "reb_" + address,
             "Bound rebase report ID must match its full digest")
    actions = value["actions"]
    _require(isinstance(actions, list) and len(actions) <= 100,
             "Expected at most 100 amendment actions")
    identifiers = set()
    for action in actions:
        _require(isinstance(action, dict), "Expected an amendment action object")
        kind = action.get("action")
        _require(isinstance(kind, str) and kind in {"revise", "reaffirm"},
                 "Unsupported amendment action")
        keys = {"promise_id", "action", "rationale"}
        if kind == "revise":
            keys.add("descriptor")
        _require(set(action) == keys, "Unexpected amendment action fields")
        identifier = action["promise_id"]
        _require(isinstance(identifier, str) and
                 bool(obligations.IDENTIFIER.fullmatch(identifier)) and
                 identifier not in identifiers,
                 "Invalid or duplicate amended promise ID")
        identifiers.add(identifier)
        rationale = action["rationale"]
        _require(isinstance(rationale, str) and 1 <= len(rationale) <= 4000 and
                 bool(rationale.strip()),
                 "Expected a nonblank rationale of at most 4000 characters")
        if kind == "revise":
            descriptor = action["descriptor"]
            coverage._inventory({"schema_version": 1, "promises": [descriptor]})
            _require(descriptor["id"] == identifier,
                     "A revised descriptor must preserve its promise ID")


def _validate_plan(value: Any) -> None:
    try:
        obligations._shape(value)
        _require(len(canonical(value).encode("utf-8")) <= MAX_REPORT_BYTES,
                 "Amendment plan exceeds 25 MB")
        _plan_schema_v1(value)
    except WorkbenchError as error:
        if error.code == "invalid_amendment":
            raise
        raise WorkbenchError("invalid_amendment", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_amendment", "Malformed amendment plan: " + str(error)) from error


def _validate_binding_v1(plan: dict[str, Any], rebase: dict[str, Any]) -> None:
    _validate_plan(plan)
    _require(plan["rebase_report_id"] == rebase["report_id"] and
             plan["rebase_report_digest"] == rebase["report_digest"],
             "Amendment plan is bound to a different rebase report")
    promises = {item["id"]: item
                for item in rebase["previous_coverage"]["inventory"]["promises"]}
    source_results = {item["id"]: item for item in rebase["observation"]["results"]}
    for action in plan["actions"]:
        identifier = action["promise_id"]
        _require(identifier in promises, "Action refers to an unknown source promise")
        _require(source_results[identifier]["state"] != "reusable_descriptor",
                 "Only nonreusable source descriptors can be amended")
        if action["action"] == "revise":
            transformed = inventory_rebase._transform(promises[identifier], rebase["mapping"])
            fields = DESCRIPTOR_KEYS - {"obligation_ids"}
            _require(any(digest(action["descriptor"][field]) != digest(transformed[field])
                         for field in fields),
                     "A revision must change descriptor content; use reaffirm to restore references without changing content")


def _observation_v1(rebase: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    """Frozen amendment-1 interpretation over one captured target only."""
    previous = rebase["previous_coverage"]["inventory"]
    source = rebase["observation"]
    source_results = {item["id"]: item for item in source["results"]}
    prior_draft = {item["id"]: item for item in source["draft_inventory"]["promises"]}
    actions = {item["promise_id"]: item for item in plan["actions"]}
    final_inventory = {"schema_version": 1, "promises": []}
    results = []
    for original in previous["promises"]:
        identifier = original["id"]
        transformed = inventory_rebase._transform(original, rebase["mapping"])
        source_state = source_results[identifier]["state"]
        authored = actions.get(identifier)
        if source_state == "reusable_descriptor":
            action, candidate, rationale = "retain", transformed, None
        elif authored is None:
            action, candidate, rationale = "pending", prior_draft[identifier], None
        else:
            action, rationale = authored["action"], authored["rationale"]
            candidate = authored["descriptor"] if action == "revise" else transformed
        final_inventory["promises"].append(_copy(candidate))
        results.append({
            "id": identifier, "action": action, "source_rebase_state": source_state,
            "source_descriptor_digest": digest(original),
            "transformed_descriptor_digest": digest(transformed),
            "candidate_descriptor_digest": digest(candidate),
            "changed_fields": [field for field in sorted(DESCRIPTOR_KEYS)
                               if digest(candidate[field]) != digest(transformed[field])],
            "rationale": rationale,
        })
    coverage._inventory(final_inventory)
    original_ids = [item["id"] for item in previous["promises"]]
    final_ids = [item["id"] for item in final_inventory["promises"]]
    _require(original_ids == final_ids, "Amendment must preserve every source promise ID and position")
    target = rebase["target_obligations"]
    final_coverage = coverage._observation_v1(target, final_inventory)
    comparisons = {item["id"]: item for item in final_coverage["results"]}
    action_blockers = []
    for result in results:
        fresh = comparisons[result["id"]]
        result.update({field: fresh[field] for field in [
            "anchor_verified", "mapping_state", "satisfaction_state",
        ]})
        ready = (fresh["anchor_verified"] and fresh["mapping_state"] == "mapped" and
                 fresh["satisfaction_state"] == "match")
        if result["action"] == "pending":
            result["outcome"] = "pending"
            action_blockers.append({"code": "amendment_action_pending", "promise_id": result["id"]})
        elif ready:
            result["outcome"] = "ready_for_human_review"
        else:
            result["outcome"] = "authored_blocked"
            action_blockers.append({
                "code": "amendment_candidate_blocked", "promise_id": result["id"],
                "action": result["action"],
            })
    blockers = list(final_coverage["blockers"]) + action_blockers
    counts = {
        "total": len(results),
        "retained": sum(item["action"] == "retain" for item in results),
        "revisions_requested": sum(item["action"] == "revise" for item in results),
        "reaffirmations_requested": sum(item["action"] == "reaffirm" for item in results),
        "pending": sum(item["action"] == "pending" for item in results),
        "candidate_covered": final_coverage["counts"]["covered"],
        "candidate_matching": final_coverage["counts"]["matching"],
    }
    return {
        "final_inventory": final_inventory, "final_coverage": final_coverage,
        "results": results, "counts": counts,
        "historical_source_blockers": source["historical_source_blockers"],
        "historical_migration_blockers": source["migration_blockers"],
        "target_upstream_blockers": target["observation"]["blockers"],
        "blockers": blockers,
        "state": "blocked" if blockers or final_coverage["state"] != "current_for_human_review" else "current_for_human_review",
        "identity_sequence_preserved": True,
        "semantic_denominator_equivalence_established": False,
        "requires_human_review": True, "auto_accept": False,
        "live_state_checked": False, "atomic_capture": False, "limits": list(LIMITS),
    }


def _validate_report(value: Any) -> None:
    try:
        obligations._shape(value)
        _require(isinstance(value, dict) and set(value) == REPORT_KEYS,
                 "Unexpected amendment report fields")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1,
                 "Unsupported amendment report schema")
        _require(value["amendment_rules_version"] == "amendment-1",
                 "Unsupported amendment rules version; supported frozen verifier: amendment-1")
        _require(isinstance(value["created_at"], str), "Missing creation time")
        timestamp = datetime.fromisoformat(value["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0,
                 "Creation time must have UTC offset")
        _require(len(canonical(value).encode("utf-8")) <= MAX_REPORT_BYTES,
                 "Amendment report exceeds 25 MB")
        payload = {key: child for key, child in value.items()
                   if key not in {"report_id", "report_digest"}}
        address = digest(payload)
        _require(value["report_id"] == "amd_" + address and value["report_digest"] == address,
                 "Amendment report address mismatch")
        rebase, plan = value["rebase_report"], value["authored_plan"]
        inventory_rebase._validate_report(rebase)
        _validate_binding_v1(plan, rebase)
        expected = _observation_v1(rebase, plan)
        _require(digest(value["observation"]) == digest(expected),
                 "Amendment proof differs from captured inputs under frozen amendment-1")
    except WorkbenchError as error:
        if error.code == "invalid_amendment":
            raise
        raise WorkbenchError("invalid_amendment", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_amendment", "Malformed amendment report: " + str(error)) from error


def apply_amendments(rebase_report: dict[str, Any], authored_plan: dict[str, Any]) -> dict[str, Any]:
    """Propose explicit amendments and recompute coverage without dropping IDs."""
    try:
        rebase, plan = _copy(rebase_report), _copy(authored_plan)
        inventory_rebase._validate_report(rebase)
        _validate_binding_v1(plan, rebase)
        payload = {
            "schema_version": 1, "amendment_rules_version": AMENDMENT_RULES_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "rebase_report": rebase, "authored_plan": plan,
            "observation": _observation_v1(rebase, plan),
        }
        address = digest(payload)
        report = {**payload, "report_id": "amd_" + address, "report_digest": address}
        _validate_report(report)
        return report
    except WorkbenchError as error:
        if error.code == "invalid_amendment":
            raise
        raise WorkbenchError("invalid_amendment", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_amendment", "Malformed amendment input: " + str(error)) from error


def load_plan(path: str | Path) -> dict[str, Any]:
    """Load strict plan syntax; source membership is checked when applying it."""
    plan = _read_json(path)
    _validate_plan(plan)
    return plan


def load_report(path: str | Path) -> dict[str, Any]:
    report = _read_json(path)
    _validate_report(report)
    return report


def save_report(report: dict[str, Any], path: str | Path) -> None:
    """Create a new immutable snapshot exclusively, without replacing files."""
    captured = _copy(report)
    _validate_report(captured)
    encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_REPORT_BYTES, "Formatted amendment report exceeds 25 MB")
    with Path(path).open("xb") as stream:
        stream.write(encoded)


def export_coverage(report: dict[str, Any]) -> dict[str, Any]:
    """Export standard coverage; retain the amendment separately for lineage."""
    try:
        captured = _copy(report)
        _validate_report(captured)
        return coverage.analyze_coverage(
            captured["rebase_report"]["target_obligations"],
            captured["observation"]["final_inventory"],
        )
    except WorkbenchError as error:
        if error.code == "invalid_amendment":
            raise
        raise WorkbenchError("invalid_amendment", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_amendment", "Malformed amendment export: " + str(error)) from error
