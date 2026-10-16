"""Inspect declared requirement changes in one frozen inventory amendment.

This read-only proof compares caller-declared metadata and selected literal
values. A finite-set predicate implication is not a conclusion about prose,
program behavior, or the direction of a promise made to a reader. The embedded
amendment remains the authority for blockers, without granting acceptance.

The requirement-delta-1 verifier retains the frozen amendment-1, rebase-1,
coverage-1 and obligations-1 interpretations. New interpretations require a
new version rather than changing the meaning of saved reports.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from . import coverage, inventory_amendment, inventory_rebase, obligations
from .store import WorkbenchError, canonical, digest


REQUIREMENT_DELTA_RULES_VERSION = "requirement-delta-1"
MAX_REPORT_BYTES = obligations.MAX_REPORT_BYTES
REPORT_KEYS = {
    "schema_version", "requirement_delta_rules_version", "created_at",
    "amendment_report", "observation", "report_id", "report_digest",
}
LIMITS = [
    "Only descriptors declared in the embedded amendment are compared; preserved IDs, order and count do not establish semantic equivalence of the promise inventory.",
    "Declared relation implication concerns predicate schemas over arbitrary pairs of finite string sets, not a claim that a particular captured configuration admits strictly more or fewer behaviors.",
    "Equality implies code_subset_of_document for the same scope, string_set type and exact document/code selectors; the reverse is not a general implication.",
    "Changed scope, value type or selectors are structural changes without inferred implication; numerical changes do not establish weaker or stronger guarantees.",
    "Selected value equality is typed and normalized, but does not establish selection continuity; changed selectors are incomparable even when their extracted literals happen to be equal.",
    "Description and quote differences are textual observations, not a semantic comparison of prose, a completeness check or evidence of program behavior.",
    "Full render dependency maps are compared separately from selected values; equal selected values do not establish unchanged rendering foundations.",
    "Attention reasons are advisory observations, not a risk policy; the embedded amendment's blockers are preserved exactly and no new acceptance authority is created.",
    "Author rationale is copied for inspection without authentication, independent verification or proof that it faithfully describes the text.",
    "Only captured candidates are inspected, without observing live state, executing proposed code, calling external models or creating a cross-database transaction.",
    "Hashes establish internal consistency only; every result requires human review and never authorizes automatic acceptance or publication.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_requirement_delta", message)


def _copy(value: Any) -> Any:
    try:
        return obligations._copy(value)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_requirement_delta", str(error)) from error


def _read_json(path: str | Path) -> Any:
    try:
        return obligations._read_json(path)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_requirement_delta", str(error)) from error


def _relation_v1(before: dict[str, Any], after: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Compare declared predicate schemas, never prose or program guarantees."""
    if digest(before) == digest(after):
        return "unchanged", {
            "comparable": True,
            "before_implies_after": True, "after_implies_before": True,
            "basis": "identical_declared_requirement",
        }
    fixed = {"scope", "value_type", "document", "code"}
    same_selection = all(digest(before[key]) == digest(after[key]) for key in fixed)
    if same_selection and before["value_type"] == "string_set":
        if before["relation"] == "equals" and after["relation"] == "code_subset_of_document":
            return "declared_relation_relaxed", {
                "comparable": True,
                "before_implies_after": True, "after_implies_before": False,
                "basis": "finite_string_set_predicate_schema",
            }
        if before["relation"] == "code_subset_of_document" and after["relation"] == "equals":
            return "declared_relation_strengthened", {
                "comparable": True,
                "before_implies_after": False, "after_implies_before": True,
                "basis": "finite_string_set_predicate_schema",
            }
    return "structural_change", {
        "comparable": False,
        "before_implies_after": None, "after_implies_before": None,
        "basis": None,
    }


def _endpoint_v1(kind: str, original: dict[str, Any], mapped: dict[str, Any],
                 final: dict[str, Any], old_candidate: dict[str, Any],
                 old_writes: dict[str, Any], new_candidate: dict[str, Any],
                 new_writes: dict[str, Any]) -> dict[str, Any]:
    before_type, after_type = original["value_type"], final["value_type"]
    before = obligations._endpoint(original[kind], kind, before_type,
                                   old_candidate, old_writes)
    after = obligations._endpoint(final[kind], kind, after_type,
                                  new_candidate, new_writes)
    before_value_digest = (digest({
        "value_type": before_type, "normalized_value": before["normalized_value"],
    }) if before["resolved"] else None)
    after_value_digest = (digest({
        "value_type": after_type, "normalized_value": after["normalized_value"],
    }) if after["resolved"] else None)
    normalized_comparable = (before_type == after_type and before["resolved"] and after["resolved"])
    normalized_equal = (before_value_digest == after_value_digest if normalized_comparable else None)
    selector_same = digest(mapped[kind]) == digest(final[kind])
    comparable = normalized_comparable and selector_same
    value_change = ("incomparable" if not comparable else
                    "unchanged" if before_value_digest == after_value_digest else "changed")
    changed = {
        key + "_changed": digest(before[key]) != digest(after[key])
        for key in ["artifact_id", "artifact_digest", "rendered_digest",
                    "snippet_digest", "location", "dependencies"]
    }
    return {
        "source": inventory_rebase._pin(before),
        "final": inventory_rebase._pin(after),
        "source_value_type": before_type, "final_value_type": after_type,
        "source_typed_value_digest": before_value_digest,
        "final_typed_value_digest": after_value_digest,
        "selector_same_after_mapping": selector_same,
        "normalized_value_equal": normalized_equal,
        "selected_value_change": value_change,
        "render_dependencies_changed": changed["dependencies_changed"],
        "changes": changed,
    }


def _promise_v1(original: dict[str, Any], mapped: dict[str, Any],
                final: dict[str, Any], authored: dict[str, Any],
                source_coverage: dict[str, Any],
                old_candidate: dict[str, Any], old_writes: dict[str, Any],
                new_candidate: dict[str, Any], new_writes: dict[str, Any]) -> dict[str, Any]:
    before_requirement, final_requirement = mapped["requirement"], final["requirement"]
    requirement_change, relation = _relation_v1(before_requirement, final_requirement)
    endpoints = {
        kind: _endpoint_v1(kind, original["requirement"], before_requirement,
                           final_requirement, old_candidate, old_writes,
                           new_candidate, new_writes)
        for kind in ["document", "code"]
    }
    description_changed = digest(mapped["description"]) != digest(final["description"])
    quote_changed = digest(mapped["anchor"]["quote"]) != digest(final["anchor"]["quote"])
    anchors = {
        "source": inventory_rebase._pin(coverage._anchor_v1(
            original["anchor"], old_candidate, old_writes)),
        "final": inventory_rebase._pin(coverage._anchor_v1(
            final["anchor"], new_candidate, new_writes)),
    }
    reasons = []
    for side, anchor in anchors.items():
        if not anchor["resolved"]:
            reasons.append({"code": side + "_anchor_unresolved"})
    if requirement_change != "unchanged":
        reasons.append({"code": requirement_change})
    for kind in ["document", "code"]:
        endpoint = endpoints[kind]
        if endpoint["selected_value_change"] != "unchanged":
            reasons.append({"code": "selected_value_" + endpoint["selected_value_change"],
                            "endpoint": kind})
        if endpoint["render_dependencies_changed"]:
            reasons.append({"code": "render_dependencies_changed", "endpoint": kind})
    if description_changed:
        reasons.append({"code": "description_changed"})
    if quote_changed:
        reasons.append({"code": "quote_changed"})
    if authored["outcome"] in {"pending", "authored_blocked"}:
        reasons.append({"code": "amendment_" + authored["outcome"]})
    mapping = {
        "anchor_artifact_id": {"source": original["anchor"]["artifact_id"],
                               "mapped": mapped["anchor"]["artifact_id"]},
        "obligation_ids": {"source": original["obligation_ids"],
                           "mapped": mapped["obligation_ids"]},
    }
    for kind in ["document", "code"]:
        mapping[kind + "_artifact_id"] = {
            "source": original["requirement"][kind]["artifact_id"],
            "mapped": mapped["requirement"][kind]["artifact_id"],
        }
    return {
        "id": original["id"], "action": authored["action"],
        "amendment_outcome": authored["outcome"],
        "author_rationale": authored["rationale"],
        "source_descriptor": _copy(original),
        "mapped_before_descriptor": _copy(mapped),
        "final_descriptor": _copy(final),
        "source_descriptor_digest": digest(original),
        "mapped_before_descriptor_digest": digest(mapped),
        "final_descriptor_digest": digest(final),
        "identifier_mapping": mapping,
        "anchors": anchors,
        "source_coverage_state": {key: source_coverage[key] for key in [
            "anchor_verified", "mapping_state", "satisfaction_state",
        ]},
        "final_coverage_state": {key: authored[key] for key in [
            "anchor_verified", "mapping_state", "satisfaction_state",
        ]},
        "changed_fields": [key for key in sorted(inventory_amendment.DESCRIPTOR_KEYS)
                           if digest(mapped[key]) != digest(final[key])],
        "requirement_changed_fields": [key for key in sorted(coverage.REQUIREMENT_KEYS)
                                       if digest(before_requirement[key]) != digest(final_requirement[key])],
        "requirement_change": requirement_change,
        "relation_comparison": relation,
        "document": endpoints["document"], "code": endpoints["code"],
        "description_changed": description_changed, "quote_changed": quote_changed,
        "semantic_prose_equivalence_established": False,
        "guarantee_direction_established": False,
        "attention_reasons": reasons,
    }


def _observation_v1(amendment: dict[str, Any]) -> dict[str, Any]:
    """Frozen requirement-delta-1 observations over embedded candidates only."""
    rebase = amendment["rebase_report"]
    original_inventory = rebase["previous_coverage"]["inventory"]["promises"]
    amended = amendment["observation"]
    final_inventory = amended["final_inventory"]["promises"]
    original_ids = [item["id"] for item in original_inventory]
    _require(original_ids == [item["id"] for item in final_inventory],
             "Requirement delta must preserve every source promise ID and position")
    authored = {item["id"]: item for item in amended["results"]}
    source_coverage = {item["id"]: item
                       for item in rebase["previous_coverage"]["observation"]["results"]}
    old_candidate, old_writes = obligations._candidate(
        rebase["previous_coverage"]["obligation_report"]["review_set"])
    new_candidate, new_writes = obligations._candidate(rebase["target_obligations"]["review_set"])
    results = []
    for original, final in zip(original_inventory, final_inventory):
        mapped = inventory_rebase._transform(original, rebase["mapping"])
        results.append(_promise_v1(original, mapped, final, authored[original["id"]],
                                   source_coverage[original["id"]],
                                   old_candidate, old_writes, new_candidate, new_writes))
    attention = [{"id": item["id"], "reasons": _copy(item["attention_reasons"])}
                 for item in results if item["attention_reasons"]]
    counts = {
        "total": len(results),
        "requirements_unchanged": sum(item["requirement_change"] == "unchanged" for item in results),
        "declared_relations_relaxed": sum(item["requirement_change"] == "declared_relation_relaxed" for item in results),
        "declared_relations_strengthened": sum(item["requirement_change"] == "declared_relation_strengthened" for item in results),
        "structural_changes": sum(item["requirement_change"] == "structural_change" for item in results),
        "selected_value_changed_promises": sum(any(item[kind]["selected_value_change"] == "changed"
                                                   for kind in ["document", "code"]) for item in results),
        "attention_promises": len(attention),
    }
    return {
        "source_amendment_id": amendment["report_id"],
        "source_amendment_digest": amendment["report_digest"],
        "source_state": amended["state"],
        "results": results, "counts": counts, "review_attention": attention,
        "attention_state": "attention_required" if attention else "no_structured_attention_detected",
        "blockers": _copy(amended["blockers"]),
        "state": amended["state"],
        "identity_sequence_preserved": True,
        "semantic_prose_equivalence_established": False,
        "guarantee_direction_established": False,
        "requires_human_review": True, "auto_accept": False,
        "live_state_checked": False, "atomic_capture": False,
        "limits": list(LIMITS),
    }


def _validate_report(value: Any) -> None:
    try:
        obligations._shape(value)
        _require(isinstance(value, dict) and set(value) == REPORT_KEYS,
                 "Unexpected requirement delta report fields")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1,
                 "Unsupported requirement delta report schema")
        _require(value["requirement_delta_rules_version"] == "requirement-delta-1",
                 "Unsupported requirement delta rules version; supported frozen verifier: requirement-delta-1")
        _require(isinstance(value["created_at"], str), "Missing creation time")
        timestamp = datetime.fromisoformat(value["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0,
                 "Creation time must have UTC offset")
        _require(len(canonical(value).encode("utf-8")) <= MAX_REPORT_BYTES,
                 "Requirement delta report exceeds 25 MB")
        payload = {key: child for key, child in value.items()
                   if key not in {"report_id", "report_digest"}}
        address = digest(payload)
        _require(value["report_id"] == "rqd_" + address and value["report_digest"] == address,
                 "Requirement delta report address mismatch")
        amendment = value["amendment_report"]
        inventory_amendment._validate_report(amendment)
        expected = _observation_v1(amendment)
        _require(digest(value["observation"]) == digest(expected),
                 "Requirement delta proof differs from captured input under frozen requirement-delta-1")
    except WorkbenchError as error:
        if error.code == "invalid_requirement_delta":
            raise
        raise WorkbenchError("invalid_requirement_delta", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_requirement_delta", "Malformed requirement delta report: " + str(error)) from error


def inspect_amendment(amendment_report: dict[str, Any]) -> dict[str, Any]:
    """Inspect declared changes without removing or adding upstream blockers."""
    try:
        amendment = _copy(amendment_report)
        inventory_amendment._validate_report(amendment)
        payload = {
            "schema_version": 1,
            "requirement_delta_rules_version": REQUIREMENT_DELTA_RULES_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "amendment_report": amendment,
            "observation": _observation_v1(amendment),
        }
        address = digest(payload)
        report = {**payload, "report_id": "rqd_" + address, "report_digest": address}
        _validate_report(report)
        return report
    except WorkbenchError as error:
        if error.code == "invalid_requirement_delta":
            raise
        raise WorkbenchError("invalid_requirement_delta", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_requirement_delta", "Malformed requirement delta input: " + str(error)) from error


def load_report(path: str | Path) -> dict[str, Any]:
    report = _read_json(path)
    _validate_report(report)
    return report


def save_report(report: dict[str, Any], path: str | Path) -> None:
    """Create a new frozen report exclusively, without replacing a file."""
    captured = _copy(report)
    _validate_report(captured)
    encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2,
                          allow_nan=False) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_REPORT_BYTES, "Formatted requirement delta report exceeds 25 MB")
    with Path(path).open("xb") as stream:
        stream.write(encoded)
