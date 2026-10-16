"""Advisory comparisons of explicitly declared fields in captured proposals.

This module reads no runtime database. A comparison concerns a selected JSON
field and a literal declaration in a deliberately restricted Python module.
It neither interprets prose nor establishes the behavior of a program.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import keyword
import math
from pathlib import Path
import re
from typing import Any

from . import review_set as review_sets
from .store import PLACEHOLDER, WorkbenchError, canonical, digest


OBLIGATION_RULES_VERSION = "obligations-1"
MAX_REPORT_BYTES = 25_000_000
MAX_TEXT_BYTES = 1_000_000
MAX_AST_NODES = 20_000
MAX_DEPTH = 64
MAX_JSON_NODES = 200_000
MAX_INTEGER_BITS = 256
IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,79}\Z")
HASH = re.compile(r"[0-9a-f]{64}\Z")
OPEN_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})([^\r\n]*)\Z")
REPORT_KEYS = {
    "schema_version", "obligation_rules_version", "created_at", "review_set",
    "spec", "observation", "report_id", "report_digest",
}
ENDPOINT_KEYS = {
    "artifact_id", "kind", "artifact_digest", "rendered_digest", "dependencies",
    "snippet", "snippet_digest", "location", "resolved", "value",
    "normalized_value", "reasons", "render_basis",
}
RESULT_KEYS = {
    "id", "scope", "relation", "value_type", "status", "reasons", "document", "code",
}
OBSERVATION_KEYS = {
    "source_review_set_id", "source_review_set_digest", "proposal_id", "proposal_digest",
    "upstream_state", "upstream_blockers", "declared_relation_state", "results",
    "blockers", "state", "requires_human_review", "auto_accept", "atomic_capture",
    "live_state_checked", "limits",
}
LIMITS = [
    "Only caller-declared obligations are compared; completeness of their selection is not established.",
    "Scope and mappings from prose to fields are caller-declared metadata, not inferred or verified semantics.",
    "A Python literal declaration does not establish program behavior or fulfillment of a prose promise.",
    "Only the embedded review-set snapshot is examined; no live state or cross-database transaction is checked.",
    "Rendering uses the captured candidate and its currently observed claims; it does not repair stale proposal read expectations.",
    "A match does not clear upstream blockers, record human review, or grant Store.accept or publication rights.",
    "Content hashes establish internal consistency, not authorship, independent review, truth, or completeness.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_obligations", message)


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _lines(value: str) -> list[str]:
    # Python/Markdown line endings; Unicode string separators are characters.
    return [line for line in re.findall(r"[^\r\n]*(?:\r\n|\r|\n|$)", value) if line]


def _line_at(value: str, offset: int) -> int:
    return len(re.findall(r"\r\n|\r|\n", value[:offset])) + 1


def _shape(value: Any, *, maximum_depth: int = MAX_DEPTH) -> None:
    """Bound decoded JSON before recursive serialization or processing."""
    pending = [(value, 0)]
    count = 0
    while pending:
        item, depth = pending.pop()
        count += 1
        _require(count <= MAX_JSON_NODES and depth <= maximum_depth, "JSON structure exceeds limits")
        if isinstance(item, dict):
            _require(all(isinstance(key, str) for key in item), "JSON object keys must be strings")
            for key in item:
                key.encode("utf-8")
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is int:
            _require(item.bit_length() <= MAX_INTEGER_BITS, "JSON integer exceeds 256 bits")
        elif type(item) is float:
            _require(math.isfinite(item), "Non-finite JSON number")
        elif type(item) is str:
            item.encode("utf-8")
        else:
            _require(item is None or type(item) is bool, "Expected JSON data")


def _copy(value: Any) -> Any:
    try:
        _shape(value)
        encoded = canonical(value).encode("utf-8")
        _require(len(encoded) <= MAX_REPORT_BYTES, "JSON data exceeds 25 MB")
        return json.loads(encoded)
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_obligations", "Expected bounded finite JSON data") from error


def _strict_json(text: str) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        value = {}
        for key, child in items:
            _require(key not in value, "Duplicate JSON object key")
            value[key] = child
        return value

    def integer(value: str) -> int:
        _require(len(value.lstrip("-")) <= 78, "JSON integer is too long")
        number = int(value)
        _require(number.bit_length() <= MAX_INTEGER_BITS, "JSON integer exceeds 256 bits")
        return number

    def floating(value: str) -> float:
        _require(len(value) <= 128, "JSON number is too long")
        number = Decimal(value)
        _require(number.is_finite() and abs(number.adjusted()) <= 1000, "JSON exponent exceeds limits")
        result = float(number)
        _require(math.isfinite(result), "JSON floating point number is not finite")
        return result

    def constant(value: str) -> None:
        raise WorkbenchError("invalid_obligations", "Non-finite JSON constant: " + value)

    try:
        value = json.loads(text, object_pairs_hook=pairs, parse_int=integer,
                           parse_float=floating, parse_constant=constant)
        _shape(value)
        return value
    except (ValueError, UnicodeError, RecursionError, InvalidOperation) as error:
        raise WorkbenchError("invalid_obligations", "Invalid bounded JSON") from error


def _read_json(path: str | Path) -> Any:
    with Path(path).open("rb") as stream:
        data = stream.read(MAX_REPORT_BYTES + 1)
    _require(len(data) <= MAX_REPORT_BYTES, "JSON file exceeds 25 MB")
    try:
        return _strict_json(data.decode("utf-8"))
    except UnicodeError as error:
        raise WorkbenchError("invalid_obligations", "Expected UTF-8 JSON") from error


def _spec(value: Any) -> None:
    _require(isinstance(value, dict) and set(value) == {"schema_version", "obligations"}, "Unexpected specification fields")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Unsupported obligation specification schema")
    items = value["obligations"]
    _require(isinstance(items, list) and len(items) <= 100, "Expected at most 100 obligations")
    ids = set()
    for item in items:
        _require(isinstance(item, dict) and set(item) == {"id", "description", "scope", "relation", "value_type", "document", "code"}, "Unexpected obligation fields")
        identifier = item["id"]
        _require(isinstance(identifier, str) and bool(IDENTIFIER.fullmatch(identifier)) and identifier not in ids, "Invalid or duplicate obligation ID")
        ids.add(identifier)
        _require(isinstance(item["description"], str) and 1 <= len(item["description"]) <= 2000, "Expected a bounded description")
        scope = item["scope"]
        _require(isinstance(scope, dict) and len(scope) <= 16 and all(isinstance(key, str) and 1 <= len(key) <= 128 and isinstance(child, str) and 1 <= len(child) <= 256 for key, child in scope.items()), "Expected at most 16 bounded scope entries")
        _require(isinstance(item["relation"], str) and item["relation"] in {"equals", "code_subset_of_document"}, "Unsupported relation")
        _require(isinstance(item["value_type"], str) and item["value_type"] in {"string", "boolean", "integer", "string_set"}, "Unsupported value type")
        _require(item["relation"] != "code_subset_of_document" or item["value_type"] == "string_set", "Subset relation requires string_set")
        document, code = item["document"], item["code"]
        _require(isinstance(document, dict) and set(document) == {"artifact_id", "fence_id", "path"}, "Unexpected document selector")
        _require(isinstance(code, dict) and set(code) == {"artifact_id", "symbol"}, "Unexpected code selector")
        for endpoint in [document, code]:
            _require(isinstance(endpoint["artifact_id"], str) and 1 <= len(endpoint["artifact_id"]) <= 200, "Expected a bounded artifact ID")
        _require(isinstance(document["fence_id"], str) and bool(IDENTIFIER.fullmatch(document["fence_id"])), "Invalid fence ID")
        path = document["path"]
        _require(isinstance(path, list) and len(path) <= 16 and all(isinstance(key, str) and len(key) <= 200 for key in path), "Expected at most 16 bounded object keys")
        _require(isinstance(code["symbol"], str) and bool(SYMBOL.fullmatch(code["symbol"])) and not keyword.iskeyword(code["symbol"]), "Invalid Python symbol")


class _Unresolved(Exception):
    def __init__(self, code: str, **details: Any):
        self.reason = {"code": code, **details}
        super().__init__(code)


def _candidate(review_set: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    context = review_set["runtime_context"]
    writes = {body["id"]: body for body in context["proposal"]["payload"]["writes"]}
    return {**context["artifacts"], **writes}, writes


def _render(body: dict[str, Any], candidate: dict[str, Any]) -> tuple[str, dict[str, str]]:
    template, bindings = body.get("template"), body.get("bindings")
    if not isinstance(template, str) or not isinstance(bindings, list) or any(not isinstance(key, str) for key in bindings):
        raise _Unresolved("unsupported_artifact_body")
    if len(template.encode("utf-8")) > MAX_TEXT_BYTES:
        raise _Unresolved("template_too_large")
    if len(bindings) != len(set(bindings)):
        raise _Unresolved("duplicate_bindings")
    if {match.group(1) for match in PLACEHOLDER.finditer(template)} != set(bindings):
        raise _Unresolved("placeholder_binding_mismatch")
    dependencies = {}
    replacements = {}
    for identifier in bindings:
        claim = candidate.get(identifier)
        if not isinstance(claim, dict) or claim.get("kind") != "claim" or "value" not in claim:
            raise _Unresolved("missing_captured_claim", claim_id=identifier)
        dependencies[identifier] = digest(claim)
        replacements[identifier] = repr(claim["value"]) if body["kind"] == "code" else str(claim["value"])
    rendered = PLACEHOLDER.sub(lambda match: replacements[match.group(1)], template)
    if "{{" in rendered or "}}" in rendered:
        raise _Unresolved("unresolved_placeholder")
    if len(rendered.encode("utf-8")) > MAX_TEXT_BYTES:
        raise _Unresolved("rendered_text_too_large")
    return rendered, dict(sorted(dependencies.items()))


def _document_fragment(text: str, fence_id: str) -> tuple[str, dict[str, int]]:
    """Find one whole fenced block while respecting enclosing code fences."""
    offset = 0
    opened = None
    targets = []
    for line_number, line in enumerate(_lines(text), 1):
        content = line.rstrip("\r\n")
        if opened is None:
            match = OPEN_FENCE.fullmatch(content)
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                info = match.group(2).strip()
                opened = {
                    "character": match.group(1)[0], "length": len(match.group(1)),
                    "target": info == "json agentwiki:" + fence_id,
                    "start_offset": offset + len(line), "start_line": line_number + 1,
                }
        else:
            close = re.fullmatch(r" {0,3}(" + re.escape(opened["character"]) + r"{3,})[ \t]*", content)
            if close and len(close.group(1)) >= opened["length"]:
                if opened["target"]:
                    targets.append({
                        "start_offset": opened["start_offset"], "end_offset": offset,
                        "start_line": opened["start_line"], "end_line": line_number - 1,
                    })
                opened = None
        offset += len(line)
    if opened and opened["target"]:
        raise _Unresolved("unclosed_document_fence")
    if len(targets) != 1:
        raise _Unresolved("missing_document_fence" if not targets else "duplicate_document_fence")
    location = targets[0]
    return text[location["start_offset"]:location["end_offset"]], location


def _literal(node: ast.AST) -> Any:
    if isinstance(node, ast.Constant) and (node.value is None or type(node.value) in {str, bool, int}):
        if type(node.value) is int and node.value.bit_length() > MAX_INTEGER_BITS:
            raise _Unresolved("python_integer_too_large")
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant) and type(node.operand.value) is int:
        if node.operand.value.bit_length() > MAX_INTEGER_BITS:
            raise _Unresolved("python_integer_too_large")
        return -node.operand.value
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        result = []
        for child in node.elts:
            if not isinstance(child, ast.Constant) or type(child.value) is not str:
                raise _Unresolved("unsupported_python_literal")
            result.append(child.value)
        # Preserve source duplicates, including duplicates in a set literal.
        if len(result) != len(set(result)):
            raise _Unresolved("duplicate_python_string_member")
        return result
    raise _Unresolved("unsupported_python_literal")


def _node_location(text: str, node: ast.AST) -> dict[str, int]:
    """Convert AST UTF-8 byte columns to character offsets for exact slicing."""
    lines = _lines(text)
    start_line, end_line = node.lineno, node.end_lineno
    start = sum(map(len, lines[:start_line - 1]))
    end = sum(map(len, lines[:end_line - 1]))
    start += len(lines[start_line - 1].encode("utf-8")[:node.col_offset].decode("utf-8"))
    end += len(lines[end_line - 1].encode("utf-8")[:node.end_col_offset].decode("utf-8"))
    return {"start_offset": start, "end_offset": end, "start_line": start_line, "end_line": end_line}


def _code_fragment(text: str, symbol: str) -> tuple[Any, str, dict[str, int]]:
    try:
        tree = ast.parse(text, mode="exec")
    except (SyntaxError, ValueError, RecursionError, OverflowError) as error:
        raise _Unresolved("python_syntax_unsupported", detail=str(error)[:200]) from error
    count = 0
    pending = [(tree, 0)]
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_AST_NODES or depth > MAX_DEPTH:
            raise _Unresolved("python_ast_limit")
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    declarations = {}
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and type(node.value.value) is str:
            continue
        if not isinstance(node, ast.Assign) or len(node.targets) != 1 or not isinstance(node.targets[0], ast.Name) or node.type_comment is not None:
            raise _Unresolved("unsupported_python_module_statement", line=node.lineno)
        name = node.targets[0].id
        if name in declarations:
            raise _Unresolved("duplicate_python_assignment", symbol=name)
        declarations[name] = (_literal(node.value), node)
    if symbol not in declarations:
        raise _Unresolved("missing_python_symbol", symbol=symbol)
    value, node = declarations[symbol]
    location = _node_location(text, node)
    return value, text[location["start_offset"]:location["end_offset"]], location


def _normalize(value: Any, value_type: str) -> Any:
    if value_type == "string":
        if type(value) is not str:
            raise _Unresolved("value_type_mismatch", expected=value_type)
    elif value_type == "boolean":
        if type(value) is not bool:
            raise _Unresolved("value_type_mismatch", expected=value_type)
    elif value_type == "integer":
        if type(value) is not int or value.bit_length() > MAX_INTEGER_BITS:
            raise _Unresolved("value_type_mismatch", expected=value_type)
    elif value_type == "string_set":
        if not isinstance(value, list) or any(type(member) is not str for member in value):
            raise _Unresolved("value_type_mismatch", expected=value_type)
        if len(value) != len(set(value)):
            raise _Unresolved("duplicate_string_member")
        return sorted(value)
    return value


def _endpoint(selector: dict[str, Any], kind: str, value_type: str,
              candidate: dict[str, Any], writes: dict[str, Any]) -> dict[str, Any]:
    result = {
        "artifact_id": selector["artifact_id"], "kind": kind, "artifact_digest": None,
        "rendered_digest": None, "dependencies": {}, "snippet": None,
        "snippet_digest": None, "location": None, "resolved": False,
        "value": None, "normalized_value": None, "reasons": [],
        "render_basis": "captured_candidate_current_claims",
    }
    try:
        body = writes.get(selector["artifact_id"])
        if body is None:
            raise _Unresolved("endpoint_not_proposal_write")
        result["artifact_digest"] = digest(body)
        if body.get("kind") != kind:
            raise _Unresolved("endpoint_kind_mismatch", expected=kind)
        text, dependencies = _render(body, candidate)
        result["rendered_digest"] = _hash_text(text)
        result["dependencies"] = dependencies
        if kind == "document":
            snippet, location = _document_fragment(text, selector["fence_id"])
            result.update(snippet=snippet, snippet_digest=_hash_text(snippet), location=location)
            try:
                value = _strict_json(snippet)
            except WorkbenchError as error:
                raise _Unresolved("invalid_document_json", detail=str(error)[:200]) from error
            for key in selector["path"]:
                if not isinstance(value, dict) or key not in value:
                    raise _Unresolved("missing_document_object_key", key=key)
                value = value[key]
        else:
            value, snippet, location = _code_fragment(text, selector["symbol"])
            result.update(snippet=snippet, snippet_digest=_hash_text(snippet), location=location)
        result["value"] = value
        result["normalized_value"] = _normalize(value, value_type)
        result["resolved"] = True
    except _Unresolved as error:
        result["reasons"].append(error.reason)
    return result


def _declared_state(results: list[dict[str, Any]]) -> str:
    if not results:
        return "no_obligations"
    if any(result["status"] == "mismatch" for result in results):
        return "relations_mismatch"
    if any(result["status"] == "unresolved" for result in results):
        return "unresolved"
    return "declared_relations_match"


def _blockers(upstream: list[dict[str, Any]], results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    blockers = list(upstream)
    if not results:
        blockers.append({"code": "no_declared_obligations"})
    for result in results:
        if result["status"] != "match":
            blockers.append({"code": "obligation_" + result["status"], "obligation_id": result["id"]})
    return blockers


def analyze_obligations(review_set: dict[str, Any], spec: dict[str, Any]) -> dict[str, Any]:
    """Compare explicit declarations in one captured, immutable review set."""
    try:
        captured, descriptor = _copy(review_set), _copy(spec)
        review_sets._validate(captured)
        _spec(descriptor)
        candidate, writes = _candidate(captured)
        results = []
        for item in descriptor["obligations"]:
            document = _endpoint(item["document"], "document", item["value_type"], candidate, writes)
            code = _endpoint(item["code"], "code", item["value_type"], candidate, writes)
            reasons = []
            if not document["resolved"] or not code["resolved"]:
                status = "unresolved"
                for label, endpoint in [("document", document), ("code", code)]:
                    reasons.extend({"endpoint": label, **reason} for reason in endpoint["reasons"])
            else:
                left, right = document["normalized_value"], code["normalized_value"]
                matches = left == right if item["relation"] == "equals" else set(right) <= set(left)
                status = "match" if matches else "mismatch"
                if not matches:
                    reasons.append({"code": "declared_values_differ" if item["relation"] == "equals" else "code_members_not_documented"})
            results.append({
                "id": item["id"], "scope": item["scope"], "relation": item["relation"],
                "value_type": item["value_type"], "status": status, "reasons": reasons,
                "document": document, "code": code,
            })
        upstream = captured["observation"]
        blockers = _blockers(upstream["blockers"], results)
        observation = {
            "source_review_set_id": captured["review_set_id"],
            "source_review_set_digest": captured["review_set_digest"],
            "proposal_id": captured["proposal_id"],
            "proposal_digest": captured["runtime_context"]["proposal"]["digest"],
            "upstream_state": upstream["state"], "upstream_blockers": upstream["blockers"],
            "declared_relation_state": _declared_state(results), "results": results,
            "blockers": blockers,
            "state": "blocked" if blockers or upstream["state"] != "current_for_human_review" else "current_for_human_review",
            "requires_human_review": True, "auto_accept": False,
            "atomic_capture": False, "live_state_checked": False, "limits": list(LIMITS),
        }
        payload = {
            "schema_version": 1, "obligation_rules_version": OBLIGATION_RULES_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "review_set": captured, "spec": descriptor, "observation": observation,
        }
        address = digest(payload)
        result = {**payload, "report_id": "obl_" + address, "report_digest": address}
        _validate_report(result)
        return result
    except WorkbenchError as error:
        if error.code == "invalid_obligations":
            raise
        raise WorkbenchError("invalid_obligations", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_obligations", "Malformed obligation input: " + str(error)) from error


def _reasons(value: Any) -> None:
    _require(isinstance(value, list) and all(isinstance(item, dict) and isinstance(item.get("code"), str) and bool(item["code"]) for item in value), "Invalid recorded reasons")


def _validate_endpoint(endpoint: Any, selector: dict[str, Any], kind: str, value_type: str,
                       candidate: dict[str, Any], writes: dict[str, Any]) -> None:
    """Validate stored pins without rerunning the historic extraction verdict."""
    _require(isinstance(endpoint, dict) and set(endpoint) == ENDPOINT_KEYS, "Unexpected endpoint fields")
    _require(endpoint["artifact_id"] == selector["artifact_id"] and endpoint["kind"] == kind, "Endpoint selector mismatch")
    _require(endpoint["render_basis"] == "captured_candidate_current_claims", "Unsupported rendering basis")
    _require(type(endpoint["resolved"]) is bool, "Invalid resolved flag")
    _reasons(endpoint["reasons"])
    _require(isinstance(endpoint["dependencies"], dict) and all(isinstance(key, str) and isinstance(value, str) and HASH.fullmatch(value) for key, value in endpoint["dependencies"].items()), "Invalid dependency pins")
    body = writes.get(selector["artifact_id"])
    _require(endpoint["artifact_digest"] == (digest(body) if body is not None else None), "Artifact digest differs from captured proposal write")
    rendered = None
    if endpoint["rendered_digest"] is not None:
        _require(body is not None and body.get("kind") == kind, "Rendered pin has no matching body")
        try:
            rendered, dependencies = _render(body, candidate)
        except _Unresolved as error:
            raise WorkbenchError("invalid_obligations", "Rendered pin cannot be reconstructed") from error
        _require(endpoint["rendered_digest"] == _hash_text(rendered) and endpoint["dependencies"] == dependencies, "Rendered text or dependencies differ from captured inputs")
    else:
        _require(endpoint["dependencies"] == {}, "Unrendered endpoint has dependency pins")
    snippet, location = endpoint["snippet"], endpoint["location"]
    if snippet is None:
        _require(endpoint["snippet_digest"] is None and location is None, "Missing snippet has location or hash")
        _require(endpoint["value"] is None, "Endpoint without a fragment has a recorded value")
    else:
        _require(isinstance(snippet, str) and endpoint["snippet_digest"] == _hash_text(snippet) and rendered is not None, "Invalid snippet digest")
        _require(isinstance(location, dict) and set(location) == {"start_offset", "end_offset", "start_line", "end_line"} and all(type(value) is int for value in location.values()), "Invalid snippet location")
        start, end = location["start_offset"], location["end_offset"]
        _require(0 <= start <= end <= len(rendered) and rendered[start:end] == snippet, "Snippet differs from pinned rendered text")
        _require(location["start_line"] == _line_at(rendered, start), "Snippet start line mismatch")
        # A document block may be empty or end immediately before a fence.
        expected_end = _line_at(rendered, end) - (1 if end > start and rendered[end - 1] in "\r\n" else 0)
        _require(location["end_line"] == expected_end or (start == end and location["end_line"] == location["start_line"] - 1), "Snippet end line mismatch")
        # This verifier is explicitly bound to the frozen obligations-1
        # extraction grammar. Future rules need their own version dispatch.
        # Validate the selected fragment and its cached value, rather than
        # trusting an arbitrary subslice with a valid self-computed hash.
        value_known = False
        try:
            if kind == "document":
                selected_snippet, selected_location = _document_fragment(rendered, selector["fence_id"])
                try:
                    selected_value = _strict_json(selected_snippet)
                    for key in selector["path"]:
                        if not isinstance(selected_value, dict) or key not in selected_value:
                            raise _Unresolved("missing_document_object_key")
                        selected_value = selected_value[key]
                    value_known = True
                except (WorkbenchError, _Unresolved):
                    pass
            else:
                selected_value, selected_snippet, selected_location = _code_fragment(rendered, selector["symbol"])
                value_known = True
        except _Unresolved as error:
            raise WorkbenchError("invalid_obligations", "Fragment cannot be reconstructed from its selector") from error
        _require(snippet == selected_snippet and location == selected_location, "Fragment differs from its declared selector")
        if value_known:
            _require(digest(endpoint["value"]) == digest(selected_value), "Cached value differs from selected source fragment")
        else:
            _require(endpoint["value"] is None and not endpoint["resolved"], "Unextractable field has a recorded resolved value")
    if endpoint["resolved"]:
        _require(snippet is not None and not endpoint["reasons"], "Resolved endpoint lacks a fragment or has errors")
        try:
            normalized = _normalize(endpoint["value"], value_type)
        except _Unresolved as error:
            raise WorkbenchError("invalid_obligations", "Recorded resolved value has the wrong type") from error
        _require(digest(endpoint["normalized_value"]) == digest(normalized), "Recorded normalized value differs from its typed value")
    else:
        _require(bool(endpoint["reasons"]) and endpoint["normalized_value"] is None, "Unresolved endpoint lacks reasons or has a normalized value")


def _validate_report(value: Any) -> None:
    """Check addresses, provenance and aggregation, preserving historic verdicts."""
    try:
        _shape(value)
        _require(isinstance(value, dict) and set(value) == REPORT_KEYS, "Unexpected obligation report fields")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Unsupported obligation report schema")
        _require(value["obligation_rules_version"] == "obligations-1", "Unsupported obligation rules version; supported frozen verifier: obligations-1")
        _require(isinstance(value["created_at"], str), "Missing creation time")
        timestamp = datetime.fromisoformat(value["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0, "Creation time must have UTC offset")
        _require(len(canonical(value).encode("utf-8")) <= MAX_REPORT_BYTES, "Obligation report exceeds 25 MB")
        payload = {key: child for key, child in value.items() if key not in {"report_id", "report_digest"}}
        address = digest(payload)
        _require(value["report_id"] == "obl_" + address and value["report_digest"] == address, "Obligation report address mismatch")
        review_set, spec = value["review_set"], value["spec"]
        review_sets._validate(review_set)
        _spec(spec)
        candidate, writes = _candidate(review_set)
        observation = value["observation"]
        _require(isinstance(observation, dict) and set(observation) == OBSERVATION_KEYS, "Unexpected observation fields")
        for field, expected in [
            ("source_review_set_id", review_set["review_set_id"]),
            ("source_review_set_digest", review_set["review_set_digest"]),
            ("proposal_id", review_set["proposal_id"]),
            ("proposal_digest", review_set["runtime_context"]["proposal"]["digest"]),
            ("upstream_state", review_set["observation"]["state"]),
            ("upstream_blockers", review_set["observation"]["blockers"]),
        ]:
            _require(digest(observation[field]) == digest(expected), "Captured source mismatch: " + field)
        _require(observation["requires_human_review"] is True and observation["auto_accept"] is False and observation["atomic_capture"] is False and observation["live_state_checked"] is False, "Invalid authority or capture flags")
        _require(isinstance(observation["limits"], list) and bool(observation["limits"]) and all(isinstance(item, str) for item in observation["limits"]), "Missing recorded limitations")
        results = observation["results"]
        _require(isinstance(results, list) and len(results) == len(spec["obligations"]), "Results do not cover declared obligations")
        for item, result in zip(spec["obligations"], results):
            _require(isinstance(result, dict) and set(result) == RESULT_KEYS, "Unexpected comparison result fields")
            for field in ["id", "scope", "relation", "value_type"]:
                _require(digest(result[field]) == digest(item[field]), "Result differs from declared obligation")
            _require(result["status"] in {"match", "mismatch", "unresolved"}, "Unknown recorded comparison status")
            _reasons(result["reasons"])
            for kind in ["document", "code"]:
                _validate_endpoint(result[kind], item[kind], kind, item["value_type"], candidate, writes)
            resolved = result["document"]["resolved"] and result["code"]["resolved"]
            _require((result["status"] != "unresolved") == resolved, "Comparison status conflicts with endpoint resolution")
            _require(bool(result["reasons"]) == (result["status"] != "match"), "Comparison reasons conflict with recorded status")
            if resolved:
                left = result["document"]["normalized_value"]
                right = result["code"]["normalized_value"]
                matches = left == right if item["relation"] == "equals" else set(right) <= set(left)
                _require(result["status"] == ("match" if matches else "mismatch"), "Recorded status contradicts its typed values under frozen obligations-1")
        _require(observation["declared_relation_state"] == _declared_state(results), "Recorded relation aggregation mismatch")
        blockers = _blockers(observation["upstream_blockers"], results)
        _require(digest(observation["blockers"]) == digest(blockers), "Recorded blockers differ from captured results")
        expected_state = "blocked" if blockers or observation["upstream_state"] != "current_for_human_review" else "current_for_human_review"
        _require(observation["state"] == expected_state, "Recorded overall state mismatch")
    except WorkbenchError as error:
        if error.code == "invalid_obligations":
            raise
        raise WorkbenchError("invalid_obligations", str(error)) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_obligations", "Malformed obligation report: " + str(error)) from error


def load_spec(path: str | Path) -> dict[str, Any]:
    value = _read_json(path)
    _spec(value)
    return value


def load_report(path: str | Path) -> dict[str, Any]:
    value = _read_json(path)
    _validate_report(value)
    return value


def save_report(report: dict[str, Any], path: str | Path) -> None:
    """Create a new report file exclusively; never replace a saved observation."""
    captured = _copy(report)
    _validate_report(captured)
    encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_REPORT_BYTES, "Formatted obligation report exceeds 25 MB")
    with Path(path).open("xb") as stream:
        stream.write(encoded)
