"""Observe declared promise coverage over one captured source-adapter report.

Exact quotations and selectors bind the declared editorial basis. They do not
establish prose semantics, runtime behavior or permission to accept content.
The full parent report and all of its blockers and comparisons remain visible.
Captured Python is data and is never executed or imported by this analyzer.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from . import source_adapter
from .store import WorkbenchError, canonical, digest


RULES_VERSION = "source-promise-coverage-1"
MAX_REPORT_BYTES = 5_000_000
MAX_INVENTORY_BYTES = 256_000
MAX_PROMISES = 100
MAX_REFERENCES = 16
REPORT_FIELDS = {"schema_version", "rules_version", "created_at", "source_report", "inventory", "observation", "limits", "report_id", "report_digest"}
INVENTORY_FIELDS = {"schema_version", "source_report_id", "source_report_digest", "promises"}
PROMISE_FIELDS = {"id", "description", "anchor", "requirement", "mapping_ids"}
REQUIREMENT_FIELDS = {"scope", "value_type", "source_selector", "adapter_symbol", "document_selector"}
ANCHOR_FIELDS = {"quote", "quote_sha256", "document_sha256", "occurrence_count", "snippet", "location", "outside_fenced_blocks", "resolved", "reasons"}
REFERENCE_FIELDS = {"mapping_id", "valid", "actual_requirement_digest", "comparison", "reasons"}
RESULT_FIELDS = {"id", "requirement_digest", "anchor", "document_endpoint", "anchor_verified", "mapping_state", "satisfaction_state", "references", "reasons"}
COUNT_FIELDS = {"total", "assigned", "mapped", "anchor_verified", "covered", "matching", "mismatch", "unresolved", "uncovered", "invalid_mapping", "unresolved_anchor", "unique_requirements", "covered_unique_requirements", "assigned_mapping_ids", "mapped_mapping_ids"}
FLAGS = {"requires_human_review": True, "auto_accept": False, "live_state_checked": False,
         "runtime_binding_verified": False, "prose_semantics_verified": False, "atomic_capture": False}
OBSERVATION_FIELDS = {"source_report_id", "source_report_digest", "upstream_state", "upstream_blockers", "upstream_comparisons", "coverage_state", "counts", "coverage_fraction", "unique_requirement_fraction", "results", "shared_mappings", "duplicate_requirements", "unmapped_mapping_ids", "unknown_mapping_ids", "blockers", "state", *FLAGS}
SUMMARY_FIELDS = {"report_id", "report_digest", "rules_version", *OBSERVATION_FIELDS}
REASON_CODES = {"missing_promise_quote", "ambiguous_promise_quote", "quote_in_fenced_block",
                "unknown_mapping_id", "mapping_requirement_mismatch", "no_assigned_mappings"}
BLOCKER_CODES = {"no_declared_promises", "promise_anchor_unresolved", "promise_uncovered",
                 "promise_invalid_mapping", "promise_mapping_mismatch", "promise_mapping_unresolved"}
LIMITS = [
    "The denominator contains only caller-declared promises; completeness of the document, source or inventory is not established.",
    "A globally unique exact quote outside the supported fenced blocks pins a selected location, not its semantic correspondence to a JSON field or source declaration.",
    "The fenced-block scan handles only the declared backtick and tilde grammar; it does not classify HTML, inline code, indented code or all natural-language prose.",
    "Coverage requires exact scope, value type and all three selectors; coverage and comparison satisfaction are separate observations.",
    "Duplicate requirements and shared mapping references are reported explicitly and do not create independent evidence or independent agent review.",
    "The complete source-adapter parent, all upstream blockers and every full pair comparison are retained even when the inventory does not reference them.",
    "An unresolved source retains separately observed adapter-document mismatches; the bridge does not evaluate expressions, execute captured Python or establish runtime binding.",
    "Scope, descriptions, units and prose-to-field correspondence are authored metadata; hashes and quotation locations do not establish truth, authorship or authenticity.",
    "Only the embedded parent report and explicit inventory are inspected; no live files, database, network, current upstream version or atomic cross-file snapshot is checked.",
    "Matching declared syntax and complete declared coverage do not record human review or grant Store acceptance, editorial decisions, publication rights or automatic inventory conversion.",
    "The bounded outer report includes its full parent; a parent at its own maximum size is not guaranteed to fit the outer report limit.",
]
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_ERRORS = (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError)


def _invalid() -> WorkbenchError:
    return WorkbenchError("source_promise_coverage_invalid", "Source-promise coverage data could not be validated")


def _require(condition: bool) -> None:
    if not condition:
        raise _invalid()


def _copy(value: Any, maximum: int = MAX_REPORT_BYTES) -> Any:
    try:
        return source_adapter._copy(value, maximum)
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def _identifier(value: Any) -> bool:
    return type(value) is str and _IDENTIFIER.fullmatch(value) is not None


def _requirement(mapping: dict[str, Any]) -> dict[str, Any]:
    return {key: mapping[key] for key in sorted(REQUIREMENT_FIELDS)}


def validate_inventory(value: Any) -> dict[str, Any]:
    """Validate a caller-authored inventory without opening embedded paths."""
    try:
        inventory = _copy(value, MAX_INVENTORY_BYTES)
        _require(type(inventory) is dict and set(inventory) == INVENTORY_FIELDS)
        _require(type(inventory["schema_version"]) is int and inventory["schema_version"] == 1)
        address = inventory["source_report_digest"]
        _require(type(address) is str and _HASH.fullmatch(address) is not None
                 and inventory["source_report_id"] == "sad_" + address)
        promises = inventory["promises"]
        _require(type(promises) is list and len(promises) <= MAX_PROMISES)
        identifiers = set()
        for promise in promises:
            _require(type(promise) is dict and set(promise) == PROMISE_FIELDS)
            identifier = promise["id"]
            _require(_identifier(identifier) and identifier not in identifiers)
            identifiers.add(identifier)
            description = promise["description"]
            _require(type(description) is str and 1 <= len(description) <= 2000)
            anchor = promise["anchor"]
            _require(type(anchor) is dict and set(anchor) == {"quote"})
            _require(type(anchor["quote"]) is str and 1 <= len(anchor["quote"]) <= 4000)
            requirement = promise["requirement"]
            _require(type(requirement) is dict and set(requirement) == REQUIREMENT_FIELDS)
            # This validates metadata grammar only; it creates no report or evidence.
            source_adapter.validate_profile({"schema_version": 1, "mappings": [{
                "id": "inventory-requirement", "annotation": "Caller-declared requirement metadata.",
                **requirement,
            }]})
            references = promise["mapping_ids"]
            _require(type(references) is list and len(references) <= MAX_REFERENCES
                     and all(_identifier(item) for item in references)
                     and len(references) == len(set(references)))
        return inventory
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def _bound_inputs(source_report: Any, inventory: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    source = source_adapter.validate_report(source_report)
    descriptor = validate_inventory(inventory)
    _require(descriptor["source_report_id"] == source["report_id"]
             and descriptor["source_report_digest"] == source["report_digest"])
    return source, descriptor


def _fenced_intervals(text: str) -> list[tuple[int, int]]:
    """Return supported whole fence spans, including delimiters and open tails."""
    intervals = []
    opened = None
    offset = 0
    for line in source_adapter.obligations._lines(text):
        content = line.rstrip("\r\n")
        if opened is None:
            match = source_adapter.obligations.OPEN_FENCE.fullmatch(content)
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                opened = (match.group(1)[0], len(match.group(1)), offset)
        else:
            character, length, start = opened
            close = re.fullmatch(r" {0,3}(" + re.escape(character) + r"{3,})[ \t]*", content)
            if close and len(close.group(1)) >= length:
                intervals.append((start, offset + len(line)))
                opened = None
        offset += len(line)
    if opened is not None:
        intervals.append((opened[2], len(text)))
    return intervals


def _anchor(text: str, quote: str, intervals: list[tuple[int, int]]) -> dict[str, Any]:
    result = {"quote": quote, "quote_sha256": hashlib.sha256(quote.encode("utf-8")).hexdigest(),
              "document_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "occurrence_count": 0,
              "snippet": None, "location": None, "outside_fenced_blocks": None,
              "resolved": False, "reasons": []}
    count, first, cursor = 0, None, 0
    while True:
        start = text.find(quote, cursor)
        if start < 0:
            break
        count += 1
        if first is None:
            first = start
        cursor = start + 1
    result["occurrence_count"] = count
    if count != 1:
        result["reasons"] = [{"code": "missing_promise_quote" if count == 0 else "ambiguous_promise_quote"}]
        return result
    end = first + len(quote)
    outside = not any(first < right and end > left for left, right in intervals)
    result.update(snippet=text[first:end], location=source_adapter._location(text, first, end),
                  outside_fenced_blocks=outside, resolved=outside)
    if not outside:
        result["reasons"] = [{"code": "quote_in_fenced_block"}]
    return result


def _observation(source: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    profile = source["captured"]["profile"]["mappings"]
    specifications = {item["id"]: item for item in profile}
    comparisons = {item["id"]: item["comparison"] for item in source["observation"]["results"]}
    text = source["captured"]["texts"]["document"]
    intervals = _fenced_intervals(text)
    results = []
    assigned_ids, valid_ids = set(), set()
    requirements, references, valid_references = {}, {}, {}
    for promise in inventory["promises"]:
        identifier, requirement = promise["id"], promise["requirement"]
        requirement_digest = digest(requirement)
        requirements.setdefault(requirement_digest, []).append(identifier)
        anchor = _anchor(text, promise["anchor"]["quote"], intervals)
        document = source_adapter._endpoint("document", text, requirement["document_selector"], requirement["value_type"])
        _require(set(document) == source_adapter.ENDPOINT_FIELDS)
        anchor_verified = anchor["resolved"] and document["resolved"]
        reasons = [{"endpoint": "quote_anchor", **reason} for reason in anchor["reasons"]]
        reasons.extend({"endpoint": "document_field", **reason} for reason in document["reasons"])
        mapped = []
        for mapping_id in promise["mapping_ids"]:
            assigned_ids.add(mapping_id)
            references.setdefault(mapping_id, []).append(identifier)
            specification = specifications.get(mapping_id)
            actual_digest, comparison, mapping_reasons = None, None, []
            if specification is None:
                mapping_reasons.append({"code": "unknown_mapping_id"})
            else:
                actual = _requirement(specification)
                actual_digest = digest(actual)
                comparison = _copy(comparisons[mapping_id])
                fields = [key for key in sorted(REQUIREMENT_FIELDS) if canonical(requirement[key]) != canonical(actual[key])]
                if fields:
                    mapping_reasons.append({"code": "mapping_requirement_mismatch", "fields": fields})
            valid = not mapping_reasons
            if valid:
                valid_ids.add(mapping_id)
                valid_references.setdefault(mapping_id, []).append(identifier)
            mapped.append({"mapping_id": mapping_id, "valid": valid, "actual_requirement_digest": actual_digest,
                           "comparison": comparison, "reasons": mapping_reasons})
            reasons.extend({"mapping_id": mapping_id, **reason} for reason in mapping_reasons)
        mapping_state = "uncovered" if not mapped else "invalid_mapping" if any(not item["valid"] for item in mapped) else "mapped"
        if not mapped:
            reasons.append({"code": "no_assigned_mappings"})
        satisfaction = "not_evaluated"
        if mapping_state == "mapped":
            states = {item["comparison"]["state"] for item in mapped}
            satisfaction = "mismatch" if "mismatch" in states else "unresolved" if "unresolved" in states else "match"
        results.append({"id": identifier, "requirement_digest": requirement_digest, "anchor": anchor,
                        "document_endpoint": document, "anchor_verified": anchor_verified,
                        "mapping_state": mapping_state, "satisfaction_state": satisfaction,
                        "references": mapped, "reasons": reasons})
    covered = [item for item in results if item["anchor_verified"] and item["mapping_state"] == "mapped"]
    covered_requirements = {item["requirement_digest"] for item in covered}
    counts = {"total": len(results), "assigned": sum(bool(item["references"]) for item in results),
              "mapped": sum(item["mapping_state"] == "mapped" for item in results),
              "anchor_verified": sum(item["anchor_verified"] for item in results), "covered": len(covered),
              "matching": sum(item["satisfaction_state"] == "match" for item in covered),
              "mismatch": sum(item["satisfaction_state"] == "mismatch" for item in covered),
              "unresolved": sum(item["satisfaction_state"] == "unresolved" for item in covered),
              "uncovered": sum(item["mapping_state"] == "uncovered" for item in results),
              "invalid_mapping": sum(item["mapping_state"] == "invalid_mapping" for item in results),
              "unresolved_anchor": sum(not item["anchor_verified"] for item in results),
              "unique_requirements": len(requirements), "covered_unique_requirements": len(covered_requirements),
              "assigned_mapping_ids": len(assigned_ids), "mapped_mapping_ids": len(valid_ids)}
    upstream = source["observation"]
    blockers = _copy(upstream["blockers"])
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
            blockers.append({"code": "promise_mapping_" + result["satisfaction_state"], "promise_id": identifier})
    return {"source_report_id": source["report_id"], "source_report_digest": source["report_digest"],
            "upstream_state": upstream["state"], "upstream_blockers": _copy(upstream["blockers"]),
            "upstream_comparisons": [{"mapping_id": item["id"], "comparison": _copy(comparisons[item["id"]])} for item in profile],
            "coverage_state": "no_declared_promises" if not results else "declared_promises_covered" if len(covered) == len(results) else "declared_coverage_incomplete",
            "counts": counts,
            "coverage_fraction": {"numerator": len(covered), "denominator": len(results)} if results else None,
            "unique_requirement_fraction": {"numerator": len(covered_requirements), "denominator": len(requirements)} if requirements else None,
            "results": results,
            "shared_mappings": [{"mapping_id": name, "promise_ids": references[name], "valid_promise_ids": valid_references.get(name, [])}
                                for name in sorted(references) if len(references[name]) > 1],
            "duplicate_requirements": [{"requirement_digest": address, "promise_ids": requirements[address]}
                                       for address in sorted(requirements) if len(requirements[address]) > 1],
            "unmapped_mapping_ids": sorted(set(specifications) - valid_ids),
            "unknown_mapping_ids": sorted(assigned_ids - set(specifications)), "blockers": blockers,
            "state": "blocked" if blockers or upstream["state"] != "current_for_human_review" else "current_for_human_review", **FLAGS}


def analyze_source_promise_coverage(source_report: dict[str, Any], inventory: dict[str, Any]) -> dict[str, Any]:
    """Capture declared coverage without changing or selecting away parent facts."""
    try:
        source, descriptor = _bound_inputs(source_report, inventory)
        payload = {"schema_version": 1, "rules_version": RULES_VERSION,
                   "created_at": datetime.now(timezone.utc).isoformat(), "source_report": source,
                   "inventory": descriptor, "observation": _observation(source, descriptor), "limits": list(LIMITS)}
        address = digest(payload)
        report = {**payload, "report_id": "spc_" + address, "report_digest": address}
        return validate_report(report)
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def validate_report(value: Any) -> dict[str, Any]:
    """Recompute the complete proof from its full validated embedded inputs."""
    try:
        report = _copy(value)
        _require(type(report) is dict and set(report) == REPORT_FIELDS)
        _require(type(report["schema_version"]) is int and report["schema_version"] == 1
                 and report["rules_version"] == RULES_VERSION and report["limits"] == LIMITS)
        _require(type(report["created_at"]) is str)
        timestamp = datetime.fromisoformat(report["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0)
        source, inventory = _bound_inputs(report["source_report"], report["inventory"])
        expected = _observation(source, inventory)
        _require(canonical(report["observation"]) == canonical(expected))
        payload = {key: child for key, child in report.items() if key not in {"report_id", "report_digest"}}
        address = digest(payload)
        _require(report["report_id"] == "spc_" + address and report["report_digest"] == address)
        return report
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def _read_json(path: str | Path, maximum: int) -> Any:
    try:
        text = source_adapter.read_text(path, maximum)
    except WorkbenchError:
        raise WorkbenchError("source_promise_coverage_io_failed", "Source-promise coverage file could not be captured") from None
    try:
        return source_adapter._json_text(text)
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def load_inventory(path: str | Path) -> dict[str, Any]:
    return validate_inventory(_read_json(path, MAX_INVENTORY_BYTES))


def load_report(path: str | Path) -> dict[str, Any]:
    return validate_report(_read_json(path, MAX_REPORT_BYTES))


def save_report(report: dict[str, Any], path: str | Path) -> None:
    """Create one validated public report exclusively, without replacing data."""
    captured = validate_report(report)
    try:
        encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        _require(len(encoded) <= MAX_REPORT_BYTES)
        with Path(path).open("xb") as stream:
            stream.write(encoded)
    except (OSError, ValueError, TypeError):
        raise WorkbenchError("source_promise_coverage_io_failed", "Source-promise coverage report could not be saved") from None


def summary(report: dict[str, Any]) -> dict[str, Any]:
    captured = validate_report(report)
    return {**{key: captured[key] for key in ("report_id", "report_digest", "rules_version")}, **captured["observation"]}
