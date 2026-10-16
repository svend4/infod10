"""Advisory coverage of caller-declared promises in a captured proposal.

The inventory names its own denominator. Exact quotation and field extraction
pin the declared basis, without inferring that prose means a structured field.
No database, checkout file or external model is consulted. Proposed Python
is read only as captured data and is never executed.

This module deliberately depends on the private, bounded parsing and frozen
``obligations-1`` helpers in obligations.py. Its version-one verifier must keep
that interpretation when a later analyzer is added.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from . import obligations
from .store import WorkbenchError, canonical, digest


COVERAGE_RULES_VERSION = "coverage-1"
MAX_REPORT_BYTES = obligations.MAX_REPORT_BYTES
REQUIREMENT_KEYS = {"scope", "relation", "value_type", "document", "code"}
REPORT_KEYS = {
    "schema_version", "coverage_rules_version", "created_at", "obligation_report",
    "inventory", "observation", "report_id", "report_digest",
}
LIMITS = [
    "The denominator contains only caller-declared promises; completeness over the document or project is not established.",
    "A unique exact quote and a resolved document field pin declared locations; their semantic correspondence is not inferred or verified.",
    "Coverage requires exact declared requirement metadata and references; coverage and comparison satisfaction are separate observations.",
    "Several promises may share an obligation or repeat a requirement; they are not independent checks or independent evidence.",
    "Only the embedded obligations-1 report and its captured candidate are examined; no live state or cross-database transaction is checked.",
    "A matched configuration declaration does not establish program behavior, prose truth, authorship, or permission to accept or publish.",
    "Coverage does not remove upstream blockers or record human review; content hashes establish internal consistency only.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_coverage", message)


def _copy(value: Any) -> Any:
    try:
        return obligations._copy(value)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_coverage", str(error)) from error


def _read_json(path: str | Path) -> Any:
    try:
        return obligations._read_json(path)
    except WorkbenchError as error:
        raise WorkbenchError("invalid_coverage", str(error)) from error


def _inventory(value: Any) -> None:
    _require(isinstance(value, dict) and set(value) == {"schema_version", "promises"}, "Unexpected inventory fields")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Unsupported promise inventory schema")
    promises = value["promises"]
    _require(isinstance(promises, list) and len(promises) <= 100, "Expected at most 100 declared promises")
    identifiers = set()
    for promise in promises:
        _require(isinstance(promise, dict) and set(promise) == {"id", "description", "anchor", "requirement", "obligation_ids"}, "Unexpected promise fields")
        identifier = promise["id"]
        _require(isinstance(identifier, str) and bool(obligations.IDENTIFIER.fullmatch(identifier)) and identifier not in identifiers, "Invalid or duplicate promise ID")
        identifiers.add(identifier)
        description = promise["description"]
        _require(isinstance(description, str) and 1 <= len(description) <= 2000, "Expected a bounded promise description")
        anchor = promise["anchor"]
        _require(isinstance(anchor, dict) and set(anchor) == {"artifact_id", "quote"}, "Unexpected promise anchor fields")
        _require(isinstance(anchor["artifact_id"], str) and 1 <= len(anchor["artifact_id"]) <= 200, "Expected a bounded anchor artifact ID")
        _require(isinstance(anchor["quote"], str) and 1 <= len(anchor["quote"]) <= 4000, "Expected a nonempty quote of at most 4000 characters")
        requirement = promise["requirement"]
        _require(isinstance(requirement, dict) and set(requirement) == REQUIREMENT_KEYS, "Unexpected promise requirement fields")
        # The same strict selector, type, scope and relation grammar is used on
        # both sides of a mapping. IDs alone never establish coverage.
        obligations._spec({"schema_version": 1, "obligations": [{
            "id": identifier, "description": description, **requirement,
        }]})
        _require(anchor["artifact_id"] == requirement["document"]["artifact_id"], "The quote and required field must belong to the same document")
        mapped = promise["obligation_ids"]
        _require(isinstance(mapped, list) and len(mapped) <= 100, "Expected at most 100 obligation references per promise")
        _require(all(isinstance(item, str) and bool(obligations.IDENTIFIER.fullmatch(item)) for item in mapped), "Invalid referenced obligation ID")
        _require(len(mapped) == len(set(mapped)), "Duplicate obligation reference within one promise")


def _line_at_v1(text: str, offset: int) -> int:
    line = obligations._line_at(text, offset)
    # An arbitrary quote may start or end on the LF half of CRLF. The common
    # prefix helper sees a trailing CR as a complete newline in that case;
    # retain both characters of the actual terminator on the preceding line.
    if 0 < offset < len(text) and text[offset - 1] == "\r" and text[offset] == "\n":
        line -= 1
    return line


def _anchor_v1(selector: dict[str, Any], candidate: dict[str, Any],
               writes: dict[str, Any]) -> dict[str, Any]:
    """Pin a unique exact quote, with overlapping occurrences considered."""
    quote = selector["quote"]
    result = {
        "artifact_id": selector["artifact_id"], "artifact_digest": None,
        "rendered_digest": None, "dependencies": {}, "quote": quote,
        "quote_digest": obligations._hash_text(quote), "snippet": None,
        "snippet_digest": None, "location": None, "resolved": False,
        "reasons": [], "render_basis": "captured_candidate_current_claims",
    }
    try:
        body = writes.get(selector["artifact_id"])
        if body is None:
            raise obligations._Unresolved("anchor_not_proposal_write")
        result["artifact_digest"] = digest(body)
        if body.get("kind") != "document":
            raise obligations._Unresolved("anchor_kind_mismatch", expected="document")
        text, dependencies = obligations._render(body, candidate)
        result["rendered_digest"] = obligations._hash_text(text)
        result["dependencies"] = dependencies
        start = text.find(quote)
        if start < 0:
            raise obligations._Unresolved("missing_promise_quote")
        if text.find(quote, start + 1) >= 0:
            raise obligations._Unresolved("ambiguous_promise_quote")
        end = start + len(quote)
        snippet = text[start:end]
        result.update(
            snippet=snippet, snippet_digest=obligations._hash_text(snippet),
            location={
                "start_offset": start, "end_offset": end,
                "start_line": _line_at_v1(text, start),
                "end_line": _line_at_v1(text, end - 1),
            },
            resolved=True,
        )
    except obligations._Unresolved as error:
        result["reasons"].append(error.reason)
    return result


def _requirement(item: dict[str, Any]) -> dict[str, Any]:
    return {key: item[key] for key in sorted(REQUIREMENT_KEYS)}


def _observation_v1(source: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Frozen coverage-1 interpretation, also used to verify saved proofs.

    This intentionally does not call the public analyzer or dispatch on its
    current rules constant. A future rule version needs a separate verifier.
    """
    review_set = source["review_set"]
    candidate, writes = obligations._candidate(review_set)
    specifications = {item["id"]: item for item in source["spec"]["obligations"]}
    comparisons = {item["id"]: item for item in source["observation"]["results"]}
    results = []
    assigned_ids: set[str] = set()
    valid_ids: set[str] = set()
    references: dict[str, list[str]] = {}
    valid_references: dict[str, list[str]] = {}
    requirements: dict[str, list[str]] = {}
    for promise in inventory["promises"]:
        identifier, requirement = promise["id"], promise["requirement"]
        requirement_digest = digest(requirement)
        requirements.setdefault(requirement_digest, []).append(identifier)
        anchor = _anchor_v1(promise["anchor"], candidate, writes)
        document = obligations._endpoint(requirement["document"], "document", requirement["value_type"], candidate, writes)
        anchor_verified = anchor["resolved"] and document["resolved"]
        reasons = [{"endpoint": "quote_anchor", **reason} for reason in anchor["reasons"]]
        reasons.extend({"endpoint": "document_field", **reason} for reason in document["reasons"])
        mappings = []
        for obligation_id in promise["obligation_ids"]:
            assigned_ids.add(obligation_id)
            references.setdefault(obligation_id, []).append(identifier)
            specification = specifications.get(obligation_id)
            mapping_reasons = []
            actual_digest = None
            comparison_status = None
            if specification is None:
                mapping_reasons.append({"code": "unknown_obligation_id"})
            else:
                actual = _requirement(specification)
                actual_digest = digest(actual)
                comparison_status = comparisons[obligation_id]["status"]
                different = [key for key in sorted(REQUIREMENT_KEYS) if digest(requirement[key]) != digest(actual[key])]
                if different:
                    mapping_reasons.append({"code": "obligation_requirement_mismatch", "fields": different})
            valid = not mapping_reasons
            if valid:
                valid_ids.add(obligation_id)
                valid_references.setdefault(obligation_id, []).append(identifier)
            mappings.append({
                "obligation_id": obligation_id, "valid": valid,
                "comparison_status": comparison_status,
                "requirement_digest": actual_digest, "reasons": mapping_reasons,
            })
            reasons.extend({"obligation_id": obligation_id, **reason} for reason in mapping_reasons)
        if not mappings:
            mapping_state = "uncovered"
            reasons.append({"code": "no_assigned_obligations"})
        elif not all(mapping["valid"] for mapping in mappings):
            mapping_state = "invalid_mapping"
        else:
            mapping_state = "mapped"
        satisfaction = "not_evaluated"
        if mapping_state == "mapped":
            statuses = {mapping["comparison_status"] for mapping in mappings}
            satisfaction = "mismatch" if "mismatch" in statuses else "unresolved" if "unresolved" in statuses else "match"
        results.append({
            "id": identifier, "requirement_digest": requirement_digest,
            "anchor": anchor, "document": document, "anchor_verified": anchor_verified,
            "mapping_state": mapping_state, "satisfaction_state": satisfaction,
            "mappings": mappings, "reasons": reasons,
        })
    covered = [item for item in results if item["anchor_verified"] and item["mapping_state"] == "mapped"]
    covered_requirements = {item["requirement_digest"] for item in covered}
    counts = {
        "total": len(results),
        "assigned": sum(bool(item["mappings"]) for item in results),
        "mapped": sum(item["mapping_state"] == "mapped" for item in results),
        "anchor_verified": sum(item["anchor_verified"] for item in results),
        "covered": len(covered),
        "matching": sum(item["satisfaction_state"] == "match" for item in covered),
        "mismatch": sum(item["satisfaction_state"] == "mismatch" for item in covered),
        "unresolved": sum(item["satisfaction_state"] == "unresolved" for item in covered),
        "uncovered": sum(item["mapping_state"] == "uncovered" for item in results),
        "invalid_mapping": sum(item["mapping_state"] == "invalid_mapping" for item in results),
        "unresolved_anchor": sum(not item["anchor_verified"] for item in results),
        "unique_requirements": len(requirements),
        "covered_unique_requirements": len(covered_requirements),
        "assigned_obligation_ids": len(assigned_ids), "mapped_obligation_ids": len(valid_ids),
    }
    upstream = source["observation"]
    blockers = list(upstream["blockers"])
    if not results:
        blockers.append({"code": "no_declared_promises"})
    for result in results:
        identifier = result["id"]
        if not result["anchor_verified"]:
            blockers.append({"code": "promise_anchor_unresolved", "promise_id": identifier})
        if result["mapping_state"] == "uncovered":
            blockers.append({"code": "promise_uncovered", "promise_id": identifier})
        elif result["mapping_state"] == "invalid_mapping":
            blockers.append({"code": "promise_invalid_mapping", "promise_id": identifier})
        elif result["satisfaction_state"] != "match":
            blockers.append({"code": "promise_obligation_" + result["satisfaction_state"], "promise_id": identifier})
    coverage_state = (
        "no_declared_promises" if not results else
        "declared_promises_covered" if len(covered) == len(results) else
        "declared_coverage_incomplete"
    )
    return {
        "source_obligation_report_id": source["report_id"],
        "source_obligation_report_digest": source["report_digest"],
        "source_review_set_id": review_set["review_set_id"],
        "proposal_id": review_set["proposal_id"],
        "proposal_digest": review_set["runtime_context"]["proposal"]["digest"],
        "upstream_state": upstream["state"], "upstream_blockers": upstream["blockers"],
        "coverage_state": coverage_state, "counts": counts,
        "coverage_fraction": {"numerator": len(covered), "denominator": len(results)} if results else None,
        "unique_requirement_fraction": {"numerator": len(covered_requirements), "denominator": len(requirements)} if requirements else None,
        "results": results,
        "shared_obligations": [
            {"obligation_id": identifier, "promise_ids": references[identifier],
             "valid_promise_ids": valid_references.get(identifier, [])}
            for identifier in sorted(references) if len(references[identifier]) > 1
        ],
        "duplicate_requirements": [
            {"requirement_digest": address, "promise_ids": requirements[address]}
            for address in sorted(requirements) if len(requirements[address]) > 1
        ],
        "unmapped_obligation_ids": sorted(set(specifications) - valid_ids),
        "unknown_obligation_ids": sorted(assigned_ids - set(specifications)),
        "blockers": blockers,
        "state": "blocked" if blockers or upstream["state"] != "current_for_human_review" else "current_for_human_review",
        "requires_human_review": True, "auto_accept": False,
        "live_state_checked": False, "atomic_capture": False, "limits": list(LIMITS),
    }


def _validate_report(value: Any) -> None:
    try:
        obligations._shape(value)
        _require(isinstance(value, dict) and set(value) == REPORT_KEYS, "Unexpected coverage report fields")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Unsupported coverage report schema")
        _require(value["coverage_rules_version"] == "coverage-1", "Unsupported coverage rules version; supported frozen verifier: coverage-1")
        _require(isinstance(value["created_at"], str), "Missing creation time")
        timestamp = datetime.fromisoformat(value["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0, "Creation time must have UTC offset")
        _require(len(canonical(value).encode("utf-8")) <= MAX_REPORT_BYTES, "Coverage report exceeds 25 MB")
        payload = {key: child for key, child in value.items() if key not in {"report_id", "report_digest"}}
        address = digest(payload)
        _require(value["report_id"] == "cov_" + address and value["report_digest"] == address, "Coverage report address mismatch")
        source, inventory = value["obligation_report"], value["inventory"]
        obligations._validate_report(source)
        _inventory(inventory)
        expected = _observation_v1(source, inventory)
        _require(digest(value["observation"]) == digest(expected), "Coverage proof differs from its captured inputs under frozen coverage-1")
    except WorkbenchError as error:
        if error.code == "invalid_coverage":
            raise
        raise WorkbenchError("invalid_coverage", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_coverage", "Malformed coverage report: " + str(error)) from error


def analyze_coverage(obligation_report: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Observe coverage of explicitly declared promises in one captured report."""
    try:
        source, descriptor = _copy(obligation_report), _copy(inventory)
        obligations._validate_report(source)
        _inventory(descriptor)
        payload = {
            "schema_version": 1, "coverage_rules_version": COVERAGE_RULES_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "obligation_report": source, "inventory": descriptor,
            "observation": _observation_v1(source, descriptor),
        }
        address = digest(payload)
        result = {**payload, "report_id": "cov_" + address, "report_digest": address}
        _validate_report(result)
        return result
    except WorkbenchError as error:
        if error.code == "invalid_coverage":
            raise
        raise WorkbenchError("invalid_coverage", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_coverage", "Malformed coverage input: " + str(error)) from error


def load_inventory(path: str | Path) -> dict[str, Any]:
    value = _read_json(path)
    try:
        _inventory(value)
    except WorkbenchError as error:
        if error.code == "invalid_coverage":
            raise
        raise WorkbenchError("invalid_coverage", str(error)) from error
    return value


def load_report(path: str | Path) -> dict[str, Any]:
    value = _read_json(path)
    _validate_report(value)
    return value


def save_report(report: dict[str, Any], path: str | Path) -> None:
    """Create a new report file exclusively; never replace an observation."""
    captured = _copy(report)
    _validate_report(captured)
    encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_REPORT_BYTES, "Formatted coverage report exceeds 25 MB")
    with Path(path).open("xb") as stream:
        stream.write(encoded)
