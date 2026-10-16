"""Plan conservative reuse of promise descriptors between captured candidates.

The whole declared denominator survives. A descriptor that needs new editorial
work keeps its promise, quote and selectors in the draft, but loses obligation
references until somebody explicitly considers them again. No vote, approval or
authority is transferred. Inputs are immutable reports, never a live database.

The rebase-1 verifier deliberately uses the frozen coverage-1 and obligations-1
helpers. A future interpretation needs a separate versioned verifier.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from . import coverage, obligations
from .store import WorkbenchError, canonical, digest


REBASE_RULES_VERSION = "rebase-1"
MAX_REPORT_BYTES = obligations.MAX_REPORT_BYTES
REPORT_KEYS = {
    "schema_version", "rebase_rules_version", "created_at", "previous_coverage",
    "target_obligations", "mapping", "observation", "report_id", "report_digest",
}
LIMITS = [
    "Only descriptors in the previous caller-declared inventory are considered; preservation of that denominator does not establish completeness over either document.",
    "A reusable descriptor preserves an exact unique quote, selected typed values, declared requirement and rendering dependencies; their semantic correspondence is not inferred.",
    "Artifact and obligation renames require an explicit injective mapping; symbols, JSON paths, scope, relation and quotes are never guessed or rewritten.",
    "Every old promise remains in the draft; obligation references are cleared for descriptors that require review, so omission cannot silently improve coverage.",
    "File, fragment and position changes are reported separately; unchanged selected values do not establish program behavior or truth of prose.",
    "Rendering dependencies cover all bindings of each selected artifact; an unrelated binding change can conservatively require new consideration.",
    "Source blockers are historical observations; target blockers remain effective. No editorial decisions or formal approvals are transferred to the target.",
    "Only captured reports are examined, with no live-state observation or atomic transaction between databases; hashes establish internal consistency only.",
    "A new draft always requires human review and never authorizes acceptance or publication.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_rebase", message)


def _copy(value: Any) -> Any:
    try:
        return obligations._copy(value)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_rebase", str(error)) from error


def _read_json(path: str | Path) -> Any:
    try:
        return obligations._read_json(path)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_rebase", str(error)) from error


def _mapping_schema(value: Any) -> None:
    _require(isinstance(value, dict) and set(value) == {
        "schema_version", "artifact_ids", "obligation_ids",
    }, "Unexpected rebase mapping fields")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1,
             "Unsupported rebase mapping schema")
    for field, maximum in [("artifact_ids", 200), ("obligation_ids", 100)]:
        changes = value[field]
        _require(isinstance(changes, dict) and len(changes) <= maximum,
                 "Too many or invalid mapping entries: " + field)
        for source, target in changes.items():
            _require(isinstance(source, str) and isinstance(target, str),
                     "Mapping entries must be strings")
            if field == "artifact_ids":
                _require(1 <= len(source) <= 200 and 1 <= len(target) <= 200,
                         "Expected bounded artifact IDs")
            else:
                _require(bool(obligations.IDENTIFIER.fullmatch(source)) and
                         bool(obligations.IDENTIFIER.fullmatch(target)),
                         "Invalid obligation mapping ID")
        _require(len(set(changes.values())) == len(changes),
                 "Explicit mapping must be injective: " + field)


def _validate_mapping(value: dict[str, Any], previous: dict[str, Any],
                      target: dict[str, Any]) -> None:
    _mapping_schema(value)
    promises = previous["inventory"]["promises"]
    artifact_ids = set()
    obligation_ids = set()
    for promise in promises:
        artifact_ids.add(promise["anchor"]["artifact_id"])
        artifact_ids.add(promise["requirement"]["document"]["artifact_id"])
        artifact_ids.add(promise["requirement"]["code"]["artifact_id"])
        obligation_ids.update(promise["obligation_ids"])
    previous_spec_ids = {
        item["id"] for item in previous["obligation_report"]["spec"]["obligations"]
    }
    target_spec_ids = {item["id"] for item in target["spec"]["obligations"]}
    _, previous_writes = obligations._candidate(previous["obligation_report"]["review_set"])
    _, target_writes = obligations._candidate(target["review_set"])
    for field, used, known_sources, known_targets in [
        ("artifact_ids", artifact_ids, artifact_ids & set(previous_writes), set(target_writes)),
        ("obligation_ids", obligation_ids,
         obligation_ids & previous_spec_ids, target_spec_ids),
    ]:
        changes = value[field]
        _require(set(changes) <= known_sources,
                 "Mapping contains unknown or unused source IDs: " + field)
        _require(set(changes.values()) <= known_targets,
                 "Explicit mapping contains unknown target IDs: " + field)
        # The implicit identities participate too: A -> B cannot merge with
        # another used old ID B that was left unchanged.
        effective = [changes.get(identifier, identifier) for identifier in used]
        _require(len(effective) == len(set(effective)),
                 "Effective mapping including implicit identities must be injective: " + field)


def _transform(promise: dict[str, Any], mapping: dict[str, Any]) -> dict[str, Any]:
    transformed = _copy(promise)
    artifacts, references = mapping["artifact_ids"], mapping["obligation_ids"]
    anchor = transformed["anchor"]
    anchor["artifact_id"] = artifacts.get(anchor["artifact_id"], anchor["artifact_id"])
    for kind in ["document", "code"]:
        selector = transformed["requirement"][kind]
        selector["artifact_id"] = artifacts.get(selector["artifact_id"], selector["artifact_id"])
    transformed["obligation_ids"] = [references.get(identifier, identifier)
                                     for identifier in promise["obligation_ids"]]
    return transformed


def _pin(endpoint: dict[str, Any]) -> dict[str, Any]:
    """Keep inspectable pins without duplicating possibly large fragments."""
    fields = [
        "artifact_id", "artifact_digest", "rendered_digest", "dependencies",
        "snippet_digest", "location", "resolved", "reasons", "render_basis",
    ]
    fields.extend(["quote_digest"] if "quote_digest" in endpoint
                  else ["normalized_value"])
    return {field: endpoint[field] for field in fields}


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, bool | None]:
    changes = {}
    for field in ["artifact_id", "artifact_digest", "rendered_digest",
                  "snippet_digest", "location", "dependencies"]:
        changes[field + "_changed"] = digest(before[field]) != digest(after[field])
    if "normalized_value" in before:
        changes["normalized_value_changed"] = (
            None if not before["resolved"] or not after["resolved"] else
            digest(before["normalized_value"]) != digest(after["normalized_value"])
        )
    return changes


def _target_mappings(promise: dict[str, Any],
                     specifications: dict[str, Any],
                     comparisons: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    requirement = promise["requirement"]
    for identifier in promise["obligation_ids"]:
        specification = specifications.get(identifier)
        different = []
        reasons = []
        if specification is None:
            reasons.append({"code": "unknown_target_obligation_id"})
        else:
            different = [field for field in sorted(coverage.REQUIREMENT_KEYS)
                         if digest(requirement[field]) != digest(specification[field])]
            if different:
                reasons.append({"code": "target_requirement_mismatch", "fields": different})
        result.append({
            "obligation_id": identifier, "valid": not reasons,
            "different_fields": different,
            "status": comparisons[identifier]["status"] if specification is not None else None,
            "reasons": reasons,
        })
    return result


def _promise_v1(promise: dict[str, Any], old_observation: dict[str, Any],
                transformed: dict[str, Any], old_candidate: dict[str, Any],
                old_writes: dict[str, Any], new_candidate: dict[str, Any],
                new_writes: dict[str, Any], target_specs: dict[str, Any],
                target_comparisons: dict[str, Any]) -> dict[str, Any]:
    old_requirement, new_requirement = promise["requirement"], transformed["requirement"]
    old_anchor = coverage._anchor_v1(promise["anchor"], old_candidate, old_writes)
    new_anchor = coverage._anchor_v1(transformed["anchor"], new_candidate, new_writes)
    before, after = {}, {}
    for kind in ["document", "code"]:
        before[kind] = obligations._endpoint(
            old_requirement[kind], kind, old_requirement["value_type"],
            old_candidate, old_writes,
        )
        after[kind] = obligations._endpoint(
            new_requirement[kind], kind, new_requirement["value_type"],
            new_candidate, new_writes,
        )
    mappings = _target_mappings(transformed, target_specs, target_comparisons)
    source_eligible = (
        old_observation["anchor_verified"] and
        old_observation["mapping_state"] == "mapped" and
        old_observation["satisfaction_state"] == "match"
    )
    checks = {
        "source_eligible": source_eligible,
        "source_anchor_verified": old_anchor["resolved"] and before["document"]["resolved"],
        "target_anchor_verified": new_anchor["resolved"] and after["document"]["resolved"],
        "target_mapping_valid": bool(mappings) and all(item["valid"] for item in mappings),
    }
    reasons = []
    unresolved = False
    for side, anchor, endpoints in [("source", old_anchor, before),
                                    ("target", new_anchor, after)]:
        if not anchor["resolved"]:
            unresolved = True
            reasons.extend({"side": side, "endpoint": "quote_anchor", **reason}
                           for reason in anchor["reasons"])
        for kind in ["document", "code"]:
            if not endpoints[kind]["resolved"]:
                unresolved = True
                reasons.extend({"side": side, "endpoint": kind, **reason}
                               for reason in endpoints[kind]["reasons"])
    if not source_eligible:
        reasons.append({
            "code": "source_descriptor_not_eligible",
            "anchor_verified": old_observation["anchor_verified"],
            "mapping_state": old_observation["mapping_state"],
            "satisfaction_state": old_observation["satisfaction_state"],
        })
    if not mappings:
        reasons.append({"code": "no_assigned_target_obligations"})
    for item in mappings:
        reasons.extend({"obligation_id": item["obligation_id"], **reason}
                       for reason in item["reasons"])
    for kind in ["document", "code"]:
        resolved = before[kind]["resolved"] and after[kind]["resolved"]
        unchanged_value = resolved and digest(before[kind]["normalized_value"]) == digest(after[kind]["normalized_value"])
        unchanged_dependencies = resolved and digest(before[kind]["dependencies"]) == digest(after[kind]["dependencies"])
        checks[kind + "_value_unchanged"] = unchanged_value
        checks[kind + "_dependencies_unchanged"] = unchanged_dependencies
        if resolved and not unchanged_value:
            reasons.append({"code": "selected_value_changed", "endpoint": kind})
        if resolved and not unchanged_dependencies:
            reasons.append({"code": "render_dependencies_changed", "endpoint": kind})
    reusable = all(checks.values()) and not unresolved
    state = "reusable_descriptor" if reusable else "unresolved" if unresolved else "needs_review"
    proposed_references = transformed["obligation_ids"]
    return {
        "id": promise["id"], "state": state,
        "original_obligation_ids": promise["obligation_ids"],
        "proposed_obligation_ids": proposed_references,
        "draft_obligation_ids": proposed_references if reusable else [],
        "transformed_requirement": new_requirement,
        "source": {
            "anchor_verified": old_observation["anchor_verified"],
            "mapping_state": old_observation["mapping_state"],
            "satisfaction_state": old_observation["satisfaction_state"],
            "anchor": _pin(old_anchor),
            "document": _pin(before["document"]), "code": _pin(before["code"]),
        },
        "target": {
            "anchor_verified": checks["target_anchor_verified"],
            "mappings": mappings, "anchor": _pin(new_anchor),
            "document": _pin(after["document"]), "code": _pin(after["code"]),
        },
        "changes": {
            "anchor": _changes(old_anchor, new_anchor),
            "document": _changes(before["document"], after["document"]),
            "code": _changes(before["code"], after["code"]),
        },
        "checks": checks, "reasons": reasons,
    }


def _observation_v1(previous: dict[str, Any], target: dict[str, Any],
                    mapping: dict[str, Any]) -> dict[str, Any]:
    """Frozen proof computation; never query a database or transfer a vote."""
    old_candidate, old_writes = obligations._candidate(previous["obligation_report"]["review_set"])
    new_candidate, new_writes = obligations._candidate(target["review_set"])
    old_observations = {item["id"]: item for item in previous["observation"]["results"]}
    target_specs = {item["id"]: item for item in target["spec"]["obligations"]}
    target_comparisons = {item["id"]: item for item in target["observation"]["results"]}
    draft = {"schema_version": 1, "promises": []}
    results = []
    for promise in previous["inventory"]["promises"]:
        transformed = _transform(promise, mapping)
        result = _promise_v1(
            promise, old_observations[promise["id"]], transformed,
            old_candidate, old_writes, new_candidate, new_writes,
            target_specs, target_comparisons,
        )
        transformed["obligation_ids"] = list(result["draft_obligation_ids"])
        draft["promises"].append(transformed)
        results.append(result)
    coverage._inventory(draft)
    # This is the coverage observation, not another embedded coverage report.
    # The target report is already captured once at the rebase report root.
    draft_coverage = coverage._observation_v1(target, draft)
    migration_blockers = [
        {"code": "descriptor_" + result["state"], "promise_id": result["id"]}
        for result in results if result["state"] != "reusable_descriptor"
    ]
    blockers = list(draft_coverage["blockers"]) + migration_blockers
    counts = {
        "total": len(results),
        "reusable_descriptor": sum(item["state"] == "reusable_descriptor" for item in results),
        "needs_review": sum(item["state"] == "needs_review" for item in results),
        "unresolved": sum(item["state"] == "unresolved" for item in results),
        "preserved_promises": len(draft["promises"]),
        "cleared_reference_promises": sum(item["state"] != "reusable_descriptor" for item in results),
    }
    return {
        "source_coverage_report_id": previous["report_id"],
        "source_coverage_report_digest": previous["report_digest"],
        "target_obligation_report_id": target["report_id"],
        "target_obligation_report_digest": target["report_digest"],
        "source_proposal_id": previous["observation"]["proposal_id"],
        "target_proposal_id": target["observation"]["proposal_id"],
        "historical_source_blockers": previous["observation"]["blockers"],
        "target_upstream_blockers": target["observation"]["blockers"],
        "draft_inventory": draft, "draft_coverage": draft_coverage,
        "results": results, "counts": counts,
        "migration_state": "all_descriptors_reusable" if results and not migration_blockers else "migration_requires_review",
        "migration_blockers": migration_blockers, "blockers": blockers,
        "state": "blocked" if blockers or draft_coverage["state"] != "current_for_human_review" else "current_for_human_review",
        "requires_human_review": True, "auto_accept": False,
        "live_state_checked": False, "atomic_capture": False, "limits": list(LIMITS),
    }


def _validate_report(value: Any) -> None:
    try:
        obligations._shape(value)
        _require(isinstance(value, dict) and set(value) == REPORT_KEYS,
                 "Unexpected rebase report fields")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1,
                 "Unsupported rebase report schema")
        _require(value["rebase_rules_version"] == "rebase-1",
                 "Unsupported rebase rules version; supported frozen verifier: rebase-1")
        _require(isinstance(value["created_at"], str), "Missing creation time")
        timestamp = datetime.fromisoformat(value["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0,
                 "Creation time must have UTC offset")
        _require(len(canonical(value).encode("utf-8")) <= MAX_REPORT_BYTES,
                 "Rebase report exceeds 25 MB")
        payload = {key: child for key, child in value.items()
                   if key not in {"report_id", "report_digest"}}
        address = digest(payload)
        _require(value["report_id"] == "reb_" + address and value["report_digest"] == address,
                 "Rebase report address mismatch")
        previous, target, mapping = value["previous_coverage"], value["target_obligations"], value["mapping"]
        coverage._validate_report(previous)
        obligations._validate_report(target)
        _validate_mapping(mapping, previous, target)
        expected = _observation_v1(previous, target, mapping)
        _require(digest(value["observation"]) == digest(expected),
                 "Rebase proof differs from captured inputs under frozen rebase-1")
    except WorkbenchError as error:
        if error.code == "invalid_rebase":
            raise
        raise WorkbenchError("invalid_rebase", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_rebase", "Malformed rebase report: " + str(error)) from error


def plan_rebase(previous_coverage: dict[str, Any], target_obligations: dict[str, Any],
                mapping: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create a complete advisory draft inventory for a captured new candidate."""
    try:
        previous, target = _copy(previous_coverage), _copy(target_obligations)
        changes = _copy(mapping if mapping is not None else {
            "schema_version": 1, "artifact_ids": {}, "obligation_ids": {},
        })
        coverage._validate_report(previous)
        obligations._validate_report(target)
        _validate_mapping(changes, previous, target)
        payload = {
            "schema_version": 1, "rebase_rules_version": REBASE_RULES_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "previous_coverage": previous, "target_obligations": target,
            "mapping": changes, "observation": _observation_v1(previous, target, changes),
        }
        address = digest(payload)
        result = {**payload, "report_id": "reb_" + address, "report_digest": address}
        _validate_report(result)
        return result
    except WorkbenchError as error:
        if error.code == "invalid_rebase":
            raise
        raise WorkbenchError("invalid_rebase", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_rebase", "Malformed rebase input: " + str(error)) from error


def load_mapping(path: str | Path) -> dict[str, Any]:
    """Read mapping syntax; source and target membership is checked on planning."""
    value = _read_json(path)
    _mapping_schema(value)
    return value


def load_report(path: str | Path) -> dict[str, Any]:
    value = _read_json(path)
    _validate_report(value)
    return value


def save_report(report: dict[str, Any], path: str | Path) -> None:
    """Create a new report exclusively, preserving every historical snapshot."""
    captured = _copy(report)
    _validate_report(captured)
    encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_REPORT_BYTES, "Formatted rebase report exceeds 25 MB")
    with Path(path).open("xb") as stream:
        stream.write(encoded)
