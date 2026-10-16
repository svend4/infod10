"""Bounded, deterministic routing of mandatory content bundles.

Every bundle touched by a proposal is selected. Shared artifacts expand that
selection to the complete connected component, without author-supplied routes.
Historical validation checks recorded structure and aggregation, never live
eligibility or the truth of prose.
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any

from . import coverage, governance, obligations
from .store import Store, WorkbenchError, canonical, digest, reason


RULES_VERSION = "content-routing-1"
MAX_SECTIONS = 8
MAX_ARTIFACTS = 32
MAX_OBLIGATIONS = 32
MAX_PROMISES = 32
MAX_POLICY_BYTES = 1_000_000
MAX_PACKET_BYTES = 4_000_000
SECTION_ID = re.compile(r"[a-z][a-z0-9_.-]{0,63}\Z")
LIMITS = [
    "Routing covers only administrator-declared sections; completeness over other text or project requirements is not established.",
    "Every touched section and its complete transitive overlap component is required; disjoint sections remain separate bundles.",
    "Exact unique quotations and resolved fields pin declared locations; correspondence between prose and those fields is not inferred or verified.",
    "Literal Python declarations do not establish program behavior; proposed code is parsed with bounded AST rules and is never executed.",
    "Matches do not establish prose truth, external evidence, human identity, independent people or independent models.",
    "A standalone packet does not authorize acceptance; formal review, current runtime bases, authorship and governance remain separate requirements.",
    "The SQLite owner and application implementation remain trusted; hashes provide internal consistency rather than external certification.",
]
PACKET_FIELDS = {
    "schema_version", "content_rules_version", "obligation_rules_version",
    "coverage_rules_version", "content_policy_generation", "content_policy_digest",
    "governance_policy_generation", "governance_policy_digest", "proposal_id",
    "proposal_digest", "read_set", "queries", "write_ids", "required_write_ids",
    "sections", "routing", "dependencies", "eligible", "reasons", "limits",
    "packet_id", "packet_digest",
}
SECTION_FIELDS = {
    "section_id", "section_policy_digest", "artifact_ids", "relations",
    "promises", "dependencies", "eligible", "reasons",
}
ROUTING_FIELDS = {
    "initial_section_ids", "selected_section_ids", "uncovered_write_ids",
    "missing_write_ids", "extra_write_ids",
}
ANCHOR_FIELDS = {
    "artifact_id", "artifact_digest", "rendered_digest", "dependencies", "quote",
    "quote_digest", "snippet", "snippet_digest", "location", "resolved",
    "reasons", "render_basis",
}
PROMISE_FIELDS = {
    "id", "requirement_digest", "anchor", "document", "mappings",
    "anchor_verified", "eligible", "reasons",
}


def _require(condition: bool, message: str, *, code: str = "content_packet_invalid") -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _same(left: Any, right: Any) -> bool:
    """JSON equality keeps booleans distinct from numbers in nested records."""
    return canonical(left) == canonical(right)


def validate_policy(value: Any) -> dict[str, Any]:
    """Normalize a strict catalog; local rule IDs keep their section namespace."""
    from . import content_gate as gate

    gate._rules()
    try:
        obligations._shape(value)
        encoded = canonical(value).encode("utf-8")
        _require(len(encoded) <= MAX_POLICY_BYTES, "Content policy exceeds 1 MB",
                 code="invalid_content_policy")
        captured = json.loads(encoded)
        _require(isinstance(captured, dict) and set(captured) == {"schema_version", "sections"}
                 and type(captured["schema_version"]) is int and captured["schema_version"] == 2,
                 "Expected schema_version 2 and sections", code="invalid_content_policy")
        sections = captured["sections"]
        _require(isinstance(sections, list) and 1 <= len(sections) <= MAX_SECTIONS,
                 "Expected 1..8 sections", code="invalid_content_policy")
        identifiers: set[str] = set()
        artifact_ids: set[str] = set()
        obligations_count = promises_count = 0
        normalized = []
        for section in sections:
            _require(isinstance(section, dict) and set(section) == {"id", "policy"},
                     "Expected section id and policy", code="invalid_content_policy")
            identifier = section["id"]
            _require(isinstance(identifier, str) and bool(SECTION_ID.fullmatch(identifier))
                     and identifier not in identifiers,
                     "Invalid or duplicate section ID", code="invalid_content_policy")
            identifiers.add(identifier)
            policy = gate._policy_v1(section["policy"])
            artifact_ids.update(policy["artifact_ids"])
            obligations_count += len(policy["spec"]["obligations"])
            promises_count += len(policy["inventory"]["promises"])
            normalized.append({"id": identifier, "policy": policy})
        _require(len(artifact_ids) <= MAX_ARTIFACTS, "Catalog exceeds 32 distinct artifacts",
                 code="invalid_content_policy")
        _require(obligations_count <= MAX_OBLIGATIONS, "Catalog exceeds 32 obligation occurrences",
                 code="invalid_content_policy")
        _require(promises_count <= MAX_PROMISES, "Catalog exceeds 32 promise occurrences",
                 code="invalid_content_policy")
        result = {"schema_version": 2, "sections": sorted(normalized, key=lambda item: item["id"])}
        _require(len(canonical(result).encode("utf-8")) <= MAX_POLICY_BYTES,
                 "Content policy exceeds 1 MB", code="invalid_content_policy")
        return result
    except WorkbenchError as error:
        if error.code == "invalid_content_policy":
            raise
        raise WorkbenchError("invalid_content_policy", "Expected a bounded content section catalog") from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_content_policy", "Expected a bounded content section catalog") from error


def _routing(descriptor: dict[str, Any], write_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
    writes = set(write_ids)
    bundles = {item["id"]: set(item["policy"]["artifact_ids"]) for item in descriptor["sections"]}
    initial = {identifier for identifier, artifacts in bundles.items() if artifacts & writes}
    selected = set(initial)
    # At most eight vertices: every changing pass adds a vertex. This is a
    # fixed point, independent of descriptor order or a first matching rule.
    for _ in range(MAX_SECTIONS):
        required = set().union(*(bundles[identifier] for identifier in selected))
        expanded = selected | {identifier for identifier, artifacts in bundles.items() if artifacts & required}
        if expanded == selected:
            break
        selected = expanded
    required = set().union(*(bundles[identifier] for identifier in selected))
    catalog = set().union(*bundles.values())
    return {
        "initial_section_ids": sorted(initial), "selected_section_ids": sorted(selected),
        "uncovered_write_ids": sorted(writes - catalog),
        "missing_write_ids": sorted(required - writes), "extra_write_ids": sorted(writes - required),
    }, sorted(required)


def _routing_reasons(routing: dict[str, Any]) -> list[dict[str, Any]]:
    reasons = []
    if not routing["selected_section_ids"]:
        reasons.append(reason("content_routing_no_sections"))
    for field, code in (
        ("uncovered_write_ids", "content_routing_uncovered_writes"),
        ("missing_write_ids", "content_routing_missing_writes"),
        ("extra_write_ids", "content_routing_extra_writes"),
    ):
        if routing[field]:
            reasons.append(reason(code, artifact_ids=routing[field]))
    return reasons


def _basis_reasons(read_set: dict[str, Any], queries: dict[str, Any]) -> list[dict[str, Any]]:
    reasons = []
    for identifier, values in sorted(read_set.items()):
        if values["expected"] != values["actual"]:
            reasons.append(reason("content_stale_read", artifact_id=identifier, **values))
    for scope, values in sorted(queries.items()):
        if values["expected"] != values["actual"]:
            reasons.append(reason("content_query_changed", scope=scope, **values))
    return reasons


def _dependencies(endpoints: list[dict[str, Any]], readset: dict[str, Any],
                  *, code: str) -> tuple[dict[str, str], list[dict[str, Any]]]:
    dependencies: dict[str, str] = {}
    reasons = []
    for endpoint in endpoints:
        for identifier, address in endpoint["dependencies"].items():
            _require(identifier not in dependencies or dependencies[identifier] == address,
                     "Conflicting candidate dependency addresses", code=code)
            dependencies[identifier] = address
            missing = reason("content_dependency_untracked", artifact_id=identifier)
            if identifier not in readset and missing not in reasons:
                reasons.append(missing)
    return dict(sorted(dependencies.items())), reasons


def build_packet(connection: sqlite3.Connection, proposal: sqlite3.Row,
                 policy: dict[str, Any]) -> dict[str, Any]:
    """Capture every selected bundle using one full candidate and actual writes."""
    from . import content_gate as gate

    try:
        gate._rules()
        descriptor = validate_policy(policy["policy"])
        governance_policy = governance.current_policy(connection)
        payload = obligations._strict_json(proposal["payload"])
        if digest(payload) != proposal["digest"]:
            raise WorkbenchError("content_proposal_invalid", "Proposal address differs from its payload")
        current = Store._artifacts(connection)
        writes = {body["id"]: body for body in payload["writes"]}
        if len(writes) != len(payload["writes"]):
            raise WorkbenchError("content_proposal_invalid", "Duplicate proposal write IDs")
        candidate = {**current, **writes}
        write_ids = sorted(writes)
        routing, required = _routing(descriptor, write_ids)
        read_set = {
            identifier: {"expected": expected,
                         "actual": digest(current[identifier]) if identifier in current else None}
            for identifier, expected in sorted(payload["readset"].items())
        }
        queries = {
            scope: {"expected": expected, "actual": Store._query_digest(current, scope)}
            for scope, expected in sorted(payload["queries"].items())
        }
        reasons = _routing_reasons(routing) + _basis_reasons(read_set, queries)
        sections = []
        dependencies: dict[str, str] = {}
        for section in descriptor["sections"]:
            if section["id"] not in routing["selected_section_ids"]:
                continue
            result = gate.evaluate_bundle(section["policy"], candidate, writes, payload["readset"])
            sections.append({"section_id": section["id"], "section_policy_digest": digest(section["policy"]),
                             "artifact_ids": section["policy"]["artifact_ids"], **result})
            reasons.extend({"section_id": section["id"], **item} for item in result["reasons"])
            for identifier, address in result["dependencies"].items():
                _require(identifier not in dependencies or dependencies[identifier] == address,
                         "Conflicting section dependency addresses", code="content_proposal_invalid")
                dependencies[identifier] = address
        body = {
            "schema_version": 2, "content_rules_version": RULES_VERSION,
            "obligation_rules_version": "obligations-1", "coverage_rules_version": "coverage-1",
            "content_policy_generation": policy["generation"], "content_policy_digest": policy["policy_digest"],
            "governance_policy_generation": governance_policy["generation"],
            "governance_policy_digest": governance_policy["policy_digest"],
            "proposal_id": proposal["id"], "proposal_digest": proposal["digest"],
            "read_set": read_set, "queries": queries, "write_ids": write_ids,
            "required_write_ids": required, "sections": sections, "routing": routing,
            "dependencies": dict(sorted(dependencies.items())),
            "eligible": not reasons, "reasons": reasons, "limits": list(LIMITS),
        }
        obligations._shape(body)
        address = digest(body)
        packet = {**body, "packet_id": "cgp_" + address, "packet_digest": address}
        if len(canonical(packet).encode("utf-8")) > MAX_PACKET_BYTES:
            raise WorkbenchError("content_packet_too_large", "Content packet exceeds 4 MB")
        return packet
    except WorkbenchError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("content_proposal_invalid", "Cannot build a bounded routed content packet") from error


def _hash(value: Any, *, optional: bool = False) -> bool:
    return (optional and value is None) or (isinstance(value, str) and bool(obligations.HASH.fullmatch(value)))


def _pins(value: Any) -> None:
    _require(isinstance(value, dict) and all(isinstance(identifier, str) and _hash(address)
                                           for identifier, address in value.items()),
             "Invalid dependency pins")


def _fragment(endpoint: dict[str, Any]) -> None:
    _require(type(endpoint["resolved"]) is bool, "Invalid resolved flag")
    obligations._reasons(endpoint["reasons"])
    _pins(endpoint["dependencies"])
    _require(endpoint["render_basis"] == "captured_candidate_current_claims", "Unsupported rendering basis")
    _require(_hash(endpoint["rendered_digest"], optional=True), "Invalid rendered digest")
    if endpoint["rendered_digest"] is None:
        _require(not endpoint["dependencies"], "Unrendered endpoint has dependencies")
    snippet, location = endpoint["snippet"], endpoint["location"]
    if snippet is None:
        _require(endpoint["snippet_digest"] is None and location is None and not endpoint["resolved"],
                 "Missing fragment has a digest, location or resolved flag")
    else:
        _require(isinstance(snippet, str) and len(snippet.encode("utf-8")) <= obligations.MAX_TEXT_BYTES
                 and endpoint["snippet_digest"] == obligations._hash_text(snippet)
                 and endpoint["rendered_digest"] is not None,
                 "Invalid fragment digest or render pin")
        _require(isinstance(location, dict) and set(location) == {
            "start_offset", "end_offset", "start_line", "end_line",
        } and all(type(value) is int for value in location.values()), "Invalid fragment location")
        _require(0 <= location["start_offset"] <= location["end_offset"] <= obligations.MAX_TEXT_BYTES
                 and location["end_offset"] - location["start_offset"] == len(snippet)
                 and 1 <= location["start_line"] <= obligations.MAX_TEXT_BYTES + 1
                 and location["start_line"] - 1 <= location["end_line"] <= obligations.MAX_TEXT_BYTES + 1,
                 "Fragment location exceeds bounded rendered text")
    _require((endpoint["resolved"] and not endpoint["reasons"])
             or (not endpoint["resolved"] and bool(endpoint["reasons"])),
             "Resolved flag and endpoint reasons disagree")


def _endpoint(value: Any, selector: dict[str, Any], kind: str, value_type: str,
              writes: dict[str, Any]) -> None:
    _require(isinstance(value, dict) and set(value) == obligations.ENDPOINT_KEYS,
             "Unexpected endpoint fields")
    _require(value["artifact_id"] == selector["artifact_id"] and value["kind"] == kind,
             "Endpoint differs from section descriptor")
    body = writes.get(selector["artifact_id"])
    _require(value["artifact_digest"] == (digest(body) if body is not None else None),
             "Endpoint digest differs from historical proposal write")
    _fragment(value)
    if value["rendered_digest"] is not None:
        _require(body is not None and body.get("kind") == kind, "Rendered endpoint lacks a matching write")
    if value["snippet"] is None:
        _require(value["value"] is None, "Missing fragment has a recorded value")
    if value["resolved"]:
        try:
            normalized = obligations._normalize(value["value"], value_type)
        except obligations._Unresolved as error:
            raise WorkbenchError("content_packet_invalid", "Recorded value has wrong declared type") from error
        _require(digest(value["normalized_value"]) == digest(normalized), "Recorded normalized value differs")
    else:
        _require(value["normalized_value"] is None, "Unresolved endpoint has a normalized value")


def _anchor(value: Any, selector: dict[str, Any], writes: dict[str, Any]) -> None:
    _require(isinstance(value, dict) and set(value) == ANCHOR_FIELDS, "Unexpected quote anchor fields")
    _require(value["artifact_id"] == selector["artifact_id"] and value["quote"] == selector["quote"]
             and value["quote_digest"] == obligations._hash_text(selector["quote"]),
             "Quote anchor differs from section descriptor")
    body = writes.get(selector["artifact_id"])
    _require(value["artifact_digest"] == (digest(body) if body is not None else None),
             "Quote artifact digest differs from historical proposal write")
    _fragment(value)
    if value["rendered_digest"] is not None:
        _require(body is not None and body.get("kind") == "document", "Rendered quote lacks a document write")
    if value["resolved"]:
        _require(value["snippet"] == selector["quote"], "Resolved quote differs from recorded snippet")
    else:
        _require(value["snippet"] is None, "Unresolved quote has a selected snippet")


def _section(value: Any, descriptor: dict[str, Any], writes: dict[str, Any],
             readset: dict[str, Any]) -> None:
    _require(isinstance(value, dict) and set(value) == SECTION_FIELDS, "Unexpected section result fields")
    policy = descriptor["policy"]
    _require(value["section_id"] == descriptor["id"]
             and value["section_policy_digest"] == digest(policy)
             and _same(value["artifact_ids"], policy["artifact_ids"]), "Section descriptor pins differ")
    specifications = policy["spec"]["obligations"]
    relations = value["relations"]
    _require(isinstance(relations, list) and len(relations) == len(specifications), "Relation count differs")
    reasons = []
    endpoints = []
    for result, specification in zip(relations, specifications):
        _require(isinstance(result, dict) and set(result) == obligations.RESULT_KEYS, "Unexpected relation fields")
        _require(all(_same(result[field], specification[field]) for field in ("id", "scope", "relation", "value_type")),
                 "Relation metadata differs from section descriptor")
        for kind in ("document", "code"):
            _endpoint(result[kind], specification[kind], kind, specification["value_type"], writes)
            endpoints.append(result[kind])
        document, code = result["document"], result["code"]
        if not document["resolved"] or not code["resolved"]:
            status = "unresolved"
            relation_reasons = [
                {"endpoint": kind, **item} for kind, endpoint in (("document", document), ("code", code))
                if not endpoint["resolved"] for item in endpoint["reasons"]
            ]
        else:
            left, right = document["normalized_value"], code["normalized_value"]
            matches = left == right if specification["relation"] == "equals" else set(right) <= set(left)
            status = "match" if matches else "mismatch"
            relation_reasons = [] if matches else [reason(
                "declared_values_differ" if specification["relation"] == "equals" else "code_members_not_documented",
            )]
        _require(result["status"] == status and _same(result["reasons"], relation_reasons),
                 "Recorded relation aggregation differs from its endpoints")
        if status != "match":
            reasons.append(reason("content_relation_" + status, obligation_id=result["id"]))
    promises = value["promises"]
    inventory = policy["inventory"]["promises"]
    _require(isinstance(promises, list) and len(promises) == len(inventory), "Promise count differs")
    by_id = {item["id"]: item for item in relations}
    specifications_by_id = {item["id"]: item for item in specifications}
    for result, item in zip(promises, inventory):
        _require(isinstance(result, dict) and set(result) == PROMISE_FIELDS
                 and result["id"] == item["id"]
                 and result["requirement_digest"] == digest(item["requirement"]), "Promise descriptor pins differ")
        requirement = item["requirement"]
        _anchor(result["anchor"], item["anchor"], writes)
        _endpoint(result["document"], requirement["document"], "document", requirement["value_type"], writes)
        endpoints.extend((result["anchor"], result["document"]))
        mappings = [{
            "obligation_id": identifier,
            "requirement_digest": digest(coverage._requirement(specifications_by_id[identifier])),
            "valid": canonical(coverage._requirement(specifications_by_id[identifier])) == canonical(requirement),
            "comparison_status": by_id[identifier]["status"],
        } for identifier in item["obligation_ids"]]
        _require(_same(result["mappings"], mappings) and all(type(mapping.get("valid")) is bool
                                                        for mapping in result["mappings"]),
                 "Promise mappings differ from section descriptor or relations")
        resolved = result["anchor"]["resolved"] and result["document"]["resolved"]
        matches = bool(mappings) and all(mapping["valid"] and mapping["comparison_status"] == "match"
                                        for mapping in mappings)
        promise_reasons = [{"endpoint": "quote_anchor", **item} for item in result["anchor"]["reasons"]]
        promise_reasons.extend({"endpoint": "document_field", **item} for item in result["document"]["reasons"])
        _require(type(result["anchor_verified"]) is bool and result["anchor_verified"] == resolved
                 and type(result["eligible"]) is bool and result["eligible"] == (resolved and matches)
                 and _same(result["reasons"], promise_reasons), "Recorded promise aggregation differs")
        if not resolved:
            reasons.append(reason("content_promise_unresolved", promise_id=item["id"]))
        if not matches:
            reasons.append(reason("content_promise_mismatch", promise_id=item["id"]))
    dependencies, dependency_reasons = _dependencies(endpoints, readset, code="content_packet_invalid")
    reasons.extend(dependency_reasons)
    _require(_same(value["dependencies"], dependencies) and type(value["eligible"]) is bool
             and value["eligible"] == (not reasons) and _same(value["reasons"], reasons),
             "Section dependencies, eligibility or reasons differ from recorded results")


def validate_packet(packet: Any, policy_descriptor: Any, proposal_payload: Any) -> None:
    """Validate immutable historical references and recorded aggregation only.

    There is deliberately no database connection, candidate reconstruction or
    extractor invocation here. A consistent historical packet is not a fresh
    authorization, an externally signed statement or a proof of text truth.
    """
    try:
        obligations._shape(packet)
        _require(isinstance(packet, dict) and set(packet) == PACKET_FIELDS, "Unexpected routed packet fields")
        _require(len(canonical(packet).encode("utf-8")) <= MAX_PACKET_BYTES, "Routed packet exceeds 4 MB")
        _require(type(packet["schema_version"]) is int and packet["schema_version"] == 2
                 and packet["content_rules_version"] == RULES_VERSION
                 and packet["obligation_rules_version"] == "obligations-1"
                 and packet["coverage_rules_version"] == "coverage-1"
                 and _same(packet["limits"], LIMITS), "Unsupported routed packet schema or rules")
        for field in ("content_policy_generation", "governance_policy_generation"):
            _require(type(packet[field]) is int and packet[field] >= 1, "Invalid historical policy generation")
        for field in ("content_policy_digest", "governance_policy_digest", "proposal_digest", "packet_digest"):
            _require(_hash(packet[field]), "Invalid historical reference digest")
        _require(isinstance(packet["proposal_id"], str) and 1 <= len(packet["proposal_id"]) <= 200,
                 "Invalid proposal identifier")
        body = {key: value for key, value in packet.items() if key not in {"packet_id", "packet_digest"}}
        address = digest(body)
        _require(packet["packet_id"] == "cgp_" + address and packet["packet_digest"] == address,
                 "Routed packet address differs from body")
        descriptor = validate_policy(policy_descriptor)
        _require(canonical(descriptor) == canonical(policy_descriptor)
                 and packet["content_policy_digest"] == digest(descriptor), "Historical section policy pins differ")
        obligations._shape(proposal_payload)
        _require(isinstance(proposal_payload, dict)
                 and set(proposal_payload) == {"readset", "queries", "writes", "rationale"}
                 and digest(proposal_payload) == packet["proposal_digest"], "Historical proposal body differs")
        actual_writes = proposal_payload["writes"]
        _require(isinstance(actual_writes, list) and 1 <= len(actual_writes) <= 100
                 and all(isinstance(item, dict) and isinstance(item.get("id"), str) for item in actual_writes),
                 "Invalid historical proposal writes")
        writes = {item["id"]: item for item in actual_writes}
        _require(len(writes) == len(actual_writes) and _same(packet["write_ids"], sorted(writes)),
                 "Recorded writes differ from historical proposal")
        routing, required = _routing(descriptor, sorted(writes))
        _require(isinstance(packet["routing"], dict) and set(packet["routing"]) == ROUTING_FIELDS
                 and _same(packet["routing"], routing) and _same(packet["required_write_ids"], required),
                 "Recorded routing differs from complete catalog closure")
        for packet_field, proposal_field in (("read_set", "readset"), ("queries", "queries")):
            values, expected = packet[packet_field], proposal_payload[proposal_field]
            _require(isinstance(values, dict) and isinstance(expected, dict) and set(values) == set(expected),
                     "Recorded basis IDs differ from historical proposal")
            for identifier, entry in values.items():
                _require(isinstance(identifier, str) and isinstance(entry, dict) and set(entry) == {"expected", "actual"}
                         and entry["expected"] == expected[identifier]
                         and _hash(entry["expected"], optional=packet_field == "read_set")
                         and _hash(entry["actual"], optional=packet_field == "read_set"),
                         "Invalid recorded expected or actual basis")
        selected = [item for item in descriptor["sections"] if item["id"] in routing["selected_section_ids"]]
        _require(isinstance(packet["sections"], list) and len(packet["sections"]) == len(selected),
                 "Recorded selected sections differ from routing")
        reasons = _routing_reasons(routing) + _basis_reasons(packet["read_set"], packet["queries"])
        dependencies: dict[str, str] = {}
        for result, section_descriptor in zip(packet["sections"], selected):
            _section(result, section_descriptor, writes, proposal_payload["readset"])
            reasons.extend({"section_id": section_descriptor["id"], **item} for item in result["reasons"])
            for identifier, dependency_address in result["dependencies"].items():
                _require(identifier not in dependencies or dependencies[identifier] == dependency_address,
                         "Conflicting recorded section dependency addresses")
                dependencies[identifier] = dependency_address
        _pins(packet["dependencies"])
        for identifier, dependency_address in dependencies.items():
            if identifier in writes:
                _require(dependency_address == digest(writes[identifier]),
                         "Dependency differs from historical candidate write")
            elif identifier in packet["read_set"]:
                _require(dependency_address == packet["read_set"][identifier]["actual"],
                         "Dependency differs from recorded candidate basis")
        _require(_same(packet["dependencies"], dict(sorted(dependencies.items())))
                 and type(packet["eligible"]) is bool and packet["eligible"] == (not reasons)
                 and _same(packet["reasons"], reasons),
                 "Root dependencies, eligibility or reasons differ from routed results")
        obligations._reasons(packet["reasons"])
    except WorkbenchError as error:
        if error.code == "content_packet_invalid":
            raise
        raise WorkbenchError("content_packet_invalid", "Malformed historical routed packet or descriptor") from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("content_packet_invalid", "Malformed historical routed packet or descriptor") from error
