"""Compare selected captured syntax in a source, literal adapter and document.

No captured Python is executed or imported. The source grammar is a narrow
projection of declarations, including a syntactic frozenset call; it does not
resolve runtime name binding or establish behavior. Reports are self-contained
historical observations, without Store writes or acceptance authority.
"""

from __future__ import annotations

import ast
from datetime import datetime, timezone
import hashlib
import json
import keyword
import os
from pathlib import Path
import re
import stat
from typing import Any

from . import obligations
from .store import WorkbenchError, canonical, digest


RULES_VERSION = "source-adapter-1"
MAX_TEXT_BYTES = 1_000_000
MAX_PROFILE_BYTES = 64_000
MAX_REPORT_BYTES = 5_000_000
MAX_AST_NODES = 20_000
MAX_DEPTH = 64
MAX_MAPPINGS = 16
REPORT_FIELDS = {"schema_version", "rules_version", "created_at", "captured", "observation", "limits", "report_id", "report_digest"}
CAPTURED_FIELDS = {"texts", "profile"}
OBSERVATION_FIELDS = {"state", "counts", "results", "blockers", "requires_human_review", "auto_accept", "live_state_checked", "runtime_binding_verified"}
RESULT_FIELDS = {"id", "scope", "annotation", "value_type", "source", "adapter", "document", "comparison"}
ENDPOINT_FIELDS = {"resolved", "value", "normalized_value", "sha256", "snippet", "location", "selector", "reasons"}
COMPARISON_FIELDS = {"source_adapter", "source_document", "adapter_document", "state"}
SUMMARY_FIELDS = {"report_id", "report_digest", "rules_version", "state", "counts", "results", "blockers", "requires_human_review", "auto_accept", "live_state_checked", "runtime_binding_verified"}
PROFILE_FIELDS = {"schema_version", "mappings"}
MAPPING_FIELDS = {"id", "scope", "annotation", "value_type", "source_selector", "adapter_symbol", "document_selector"}
FLAGS = {"requires_human_review": True, "auto_accept": False,
         "live_state_checked": False, "runtime_binding_verified": False}
LIMITS = [
    "Only the explicitly selected mappings are compared; completeness of the document, source or promise inventory is not established.",
    "Source values are projections of supported captured AST syntax, not evaluated Python expressions or verified runtime defaults.",
    "A syntactic frozenset call is never invoked; visible rebinding is rejected conservatively and runtime name binding remains unverified.",
    "The adapter uses the earlier restricted literal-module grammar; unsupported code is unresolved rather than executed or inferred.",
    "Scope, annotation, units and mappings from prose to selected fields are caller-declared metadata, not inferred semantic truth.",
    "A methods-set match does not establish a universal prohibition on POST retries, and an integer match does not establish retry-versus-attempt semantics.",
    "Only the three captured texts and profile are inspected; no live files, runtime database, external source or current upstream version is rechecked when showing a report.",
    "Matching syntax does not establish program behavior, prose equivalence, independent agent evidence, human review or permission to accept or publish.",
    "Content hashes establish internal consistency relative to captured bytes, not authorship, external attestation, authenticity or completeness.",
    "Existing semantic extractors, content gates, recovery protocols and acceptance authorities retain their prior behavior; this report adds no Store write or automatic acceptance.",
]
REASON_CODES = {
    "python_syntax_unsupported", "python_ast_limit", "missing_source_class", "ambiguous_source_class",
    "source_class_binding_unsupported", "source_class_shape_unsupported", "source_member_binding_unsupported",
    "missing_source_symbol", "ambiguous_source_symbol", "unsupported_source_frozenset",
    "source_frozenset_binding_unsupported", "missing_source_method", "ambiguous_source_method",
    "source_method_shape_unsupported", "missing_source_parameter", "ambiguous_source_parameter",
    "source_parameter_default_missing", "unsupported_python_literal", "python_integer_too_large",
    "duplicate_python_string_member", "unsupported_python_module_statement", "duplicate_python_assignment",
    "missing_python_symbol", "unclosed_document_fence", "missing_document_fence", "duplicate_document_fence",
    "invalid_document_json", "missing_document_object_key", "value_type_mismatch", "duplicate_string_member",
}
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,79}\Z")
_ERRORS = (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError)


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError("source_adapter_invalid", "Source-adapter data has an unsupported shape or binding")


def _invalid() -> WorkbenchError:
    return WorkbenchError("source_adapter_invalid", "Source-adapter data could not be validated")


def _copy(value: Any, maximum: int = MAX_REPORT_BYTES) -> Any:
    try:
        obligations._shape(value)
        encoded = canonical(value).encode("utf-8")
        _require(len(encoded) <= maximum)
        return json.loads(encoded)
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def _text(value: Any, maximum: int = MAX_TEXT_BYTES) -> bool:
    return type(value) is str and len(value.encode("utf-8")) <= maximum


def _symbol(value: Any) -> bool:
    return type(value) is str and 1 <= len(value) <= 80 and value.isidentifier() and not keyword.iskeyword(value)


def validate_profile(value: Any) -> dict[str, Any]:
    """Validate and copy a closed profile without opening paths from its data."""
    try:
        profile = _copy(value, MAX_PROFILE_BYTES)
        _require(type(profile) is dict and set(profile) == PROFILE_FIELDS)
        _require(type(profile["schema_version"]) is int and profile["schema_version"] == 1)
        mappings = profile["mappings"]
        _require(type(mappings) is list and 1 <= len(mappings) <= MAX_MAPPINGS)
        identifiers, selections = set(), set()
        for item in mappings:
            _require(type(item) is dict and set(item) == MAPPING_FIELDS)
            name = item["id"]
            _require(type(name) is str and _IDENTIFIER.fullmatch(name) is not None and name not in identifiers)
            identifiers.add(name)
            for field in ("scope", "annotation"):
                _require(type(item[field]) is str and 1 <= len(item[field]) <= 2000 and bool(item[field].strip()))
            _require(item["value_type"] in {"integer", "string_set"} and _symbol(item["adapter_symbol"]))
            source, document = item["source_selector"], item["document_selector"]
            _require(type(source) is dict and type(document) is dict)
            _require(set(document) == {"fence_id", "field"})
            _require(type(document["fence_id"]) is str and _IDENTIFIER.fullmatch(document["fence_id"]) is not None)
            _require(type(document["field"]) is str and 1 <= len(document["field"]) <= 80)
            kind = source.get("kind")
            if kind == "class_frozenset_strings":
                _require(set(source) == {"kind", "class_name", "symbol"} and item["value_type"] == "string_set")
                _require(_symbol(source["class_name"]) and _symbol(source["symbol"]))
            else:
                _require(kind == "parameter_default" and item["value_type"] == "integer")
                _require(set(source) == {"kind", "class_name", "method_name", "parameter"})
                _require(all(_symbol(source[field]) for field in ("class_name", "method_name", "parameter")))
            selection = digest({"source": source, "adapter": item["adapter_symbol"], "document": document})
            _require(selection not in selections)
            selections.add(selection)
        return profile
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def _metadata(details: os.stat_result) -> tuple[int, ...]:
    return tuple(getattr(details, key) for key in ("st_dev", "st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"))


def read_text(path: str | Path, maximum: int = MAX_TEXT_BYTES) -> str:
    """Capture one regular non-symlink UTF-8 file in one bounded read pass."""
    try:
        _require(type(maximum) is int and 0 < maximum <= MAX_REPORT_BYTES)
        location = Path(path)
        before = location.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
            raise OSError("Unsupported source-adapter input file")
        descriptor = os.open(location, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
        with os.fdopen(descriptor, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if not stat.S_ISREG(opened.st_mode) or _metadata(opened) != _metadata(before):
                raise OSError("Source-adapter input changed before capture")
            encoded = stream.read(maximum + 1)
            after = os.fstat(stream.fileno())
            if (len(encoded) > maximum or len(encoded) != before.st_size
                    or _metadata(after) != _metadata(before) or _metadata(location.lstat()) != _metadata(before)):
                raise OSError("Source-adapter input changed during capture")
        return encoded.decode("utf-8")
    except (OSError, UnicodeError, ValueError, TypeError):
        raise WorkbenchError("source_adapter_io_failed", "Source-adapter file could not be captured") from None


def _json_text(text: str) -> Any:
    try:
        return obligations._strict_json(text)
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def load_profile(path: str | Path) -> dict[str, Any]:
    return validate_profile(_json_text(read_text(path, MAX_PROFILE_BYTES)))


class _Unresolved(Exception):
    def __init__(self, code: str):
        _require(code in REASON_CODES)
        self.code = code
        super().__init__(code)


def _parse(text: str) -> ast.Module | _Unresolved:
    try:
        tree = ast.parse(text, mode="exec")
    except (SyntaxError, ValueError, RecursionError, OverflowError):
        return _Unresolved("python_syntax_unsupported")
    pending, count = [(tree, 0)], 0
    while pending:
        node, depth = pending.pop()
        count += 1
        if count > MAX_AST_NODES or depth > MAX_DEPTH:
            return _Unresolved("python_ast_limit")
        pending.extend((child, depth + 1) for child in ast.iter_child_nodes(node))
    return tree


def _own_binding(node: ast.AST, name: str) -> bool:
    if isinstance(node, ast.Name):
        return node.id == name and isinstance(node.ctx, (ast.Store, ast.Del))
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return node.name == name
    if isinstance(node, ast.Import):
        return any((alias.asname or alias.name.split(".")[0]) == name for alias in node.names)
    if isinstance(node, ast.ImportFrom):
        return any(alias.name == "*" or (alias.asname or alias.name) == name for alias in node.names)
    if isinstance(node, ast.ExceptHandler):
        return node.name == name
    if isinstance(node, (ast.MatchAs, ast.MatchStar)):
        return node.name == name
    if isinstance(node, ast.MatchMapping):
        return node.rest == name
    if isinstance(node, ast.arg):
        return node.arg == name
    return False


def _scope_bindings(body: list[ast.stmt], name: str) -> list[ast.AST]:
    """Find visible lexical bindings without descending into nested bodies."""
    pending, found = list(body), []
    while pending:
        node = pending.pop()
        if _own_binding(node, name):
            found.append(node)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            pending.extend(node.args.defaults)
            pending.extend(child for child in node.args.kw_defaults if child is not None)
            arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            arguments.extend(item for item in (node.args.vararg, node.args.kwarg) if item is not None)
            pending.extend(item.annotation for item in arguments if item.annotation is not None)
            if not isinstance(node, ast.Lambda):
                pending.extend(node.decorator_list)
                if node.returns is not None:
                    pending.append(node.returns)
            continue
        if isinstance(node, ast.ClassDef):
            pending.extend(node.bases)
            pending.extend(item.value for item in node.keywords)
            pending.extend(node.decorator_list)
            continue
        pending.extend(ast.iter_child_nodes(node))
    return found


def _attribute_rebound(tree: ast.Module, class_name: str, symbol: str) -> bool:
    return any(isinstance(node, ast.Attribute) and node.attr == symbol
               and isinstance(node.value, ast.Name) and node.value.id == class_name
               and isinstance(node.ctx, (ast.Store, ast.Del)) for node in ast.walk(tree))


def _source_node(tree: ast.Module, selector: dict[str, Any]) -> ast.AST:
    classes = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == selector["class_name"]]
    if not classes:
        raise _Unresolved("missing_source_class")
    if len(classes) != 1:
        raise _Unresolved("ambiguous_source_class")
    selected = classes[0]
    bindings = _scope_bindings(tree.body, selector["class_name"])
    if len(bindings) != 1 or bindings[0] is not selected:
        raise _Unresolved("source_class_binding_unsupported")
    if selected.decorator_list or selected.bases or selected.keywords or getattr(selected, "type_params", []):
        raise _Unresolved("source_class_shape_unsupported")
    if selector["kind"] == "class_frozenset_strings":
        symbol = selector["symbol"]
        declarations = [node for node in selected.body if isinstance(node, ast.Assign)
                        and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                        and node.targets[0].id == symbol and node.type_comment is None]
        if not declarations:
            raise _Unresolved("missing_source_symbol")
        if len(declarations) != 1:
            raise _Unresolved("ambiguous_source_symbol")
        declaration = declarations[0]
        member_bindings = _scope_bindings(selected.body, symbol)
        if (len(member_bindings) != 1 or member_bindings[0] is not declaration.targets[0]
                or _attribute_rebound(tree, selected.name, symbol)):
            raise _Unresolved("source_member_binding_unsupported")
        if any(_own_binding(node, "frozenset") for node in ast.walk(tree)):
            raise _Unresolved("source_frozenset_binding_unsupported")
        return declaration
    name = selector["method_name"]
    methods = [node for node in selected.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name]
    if not methods:
        raise _Unresolved("missing_source_method")
    if len(methods) != 1:
        raise _Unresolved("ambiguous_source_method")
    method = methods[0]
    method_bindings = _scope_bindings(selected.body, name)
    if (len(method_bindings) != 1 or method_bindings[0] is not method
            or _attribute_rebound(tree, selected.name, name)):
        raise _Unresolved("source_member_binding_unsupported")
    if not isinstance(method, ast.FunctionDef) or method.decorator_list or getattr(method, "type_params", []):
        raise _Unresolved("source_method_shape_unsupported")
    arguments, parameter = method.args, selector["parameter"]
    positional = [*arguments.posonlyargs, *arguments.args]
    all_parameters = [*positional, *arguments.kwonlyargs]
    all_parameters.extend(item for item in (arguments.vararg, arguments.kwarg) if item is not None)
    count = sum(item.arg == parameter for item in all_parameters)
    if count == 0:
        raise _Unresolved("missing_source_parameter")
    if count != 1:
        raise _Unresolved("ambiguous_source_parameter")
    defaults = {item.arg: default for item, default in zip(positional[len(positional)-len(arguments.defaults):], arguments.defaults)}
    defaults.update({item.arg: default for item, default in zip(arguments.kwonlyargs, arguments.kw_defaults)})
    node = defaults.get(parameter)
    if node is None:
        raise _Unresolved("source_parameter_default_missing")
    return node


def _literal(node: ast.AST) -> Any:
    try:
        return obligations._literal(node)
    except obligations._Unresolved as error:
        raise _Unresolved(error.reason["code"]) from None


def _source_value(node: ast.AST, selector: dict[str, Any]) -> Any:
    if selector["kind"] == "parameter_default":
        return _literal(node)
    expression = node.value
    if (not isinstance(expression, ast.Call) or not isinstance(expression.func, ast.Name)
            or expression.func.id != "frozenset" or len(expression.args) != 1 or expression.keywords
            or not isinstance(expression.args[0], (ast.List, ast.Tuple, ast.Set))):
        raise _Unresolved("unsupported_source_frozenset")
    return _literal(expression.args[0])


def _adapter_node(tree: ast.Module, symbol: str) -> tuple[Any, ast.Assign]:
    declarations = {}
    for index, node in enumerate(tree.body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and type(node.value.value) is str:
            continue
        if (not isinstance(node, ast.Assign) or len(node.targets) != 1
                or not isinstance(node.targets[0], ast.Name) or node.type_comment is not None):
            raise _Unresolved("unsupported_python_module_statement")
        name = node.targets[0].id
        if name in declarations:
            raise _Unresolved("duplicate_python_assignment")
        declarations[name] = (_literal(node.value), node)
    if symbol not in declarations:
        raise _Unresolved("missing_python_symbol")
    return declarations[symbol]


def _location(text: str, start: int, end: int) -> dict[str, int]:
    breaks = [match.end() for match in re.finditer(r"\r\n|\r|\n", text)]
    return {"start_offset": start, "end_offset": end,
            "start_line": 1 + sum(boundary <= start for boundary in breaks),
            "end_line": 1 + sum(boundary <= max(start, end-1) for boundary in breaks)}


def _node_fragment(text: str, node: ast.AST) -> tuple[str, dict[str, int]]:
    selected = obligations._node_location(text, node)
    start, end = selected["start_offset"], selected["end_offset"]
    return text[start:end], _location(text, start, end)


def _endpoint(kind: str, text: str, selector: dict[str, Any], value_type: str,
              parsed: ast.Module | _Unresolved | None = None) -> dict[str, Any]:
    result = {"resolved": False, "value": None, "normalized_value": None,
              "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(), "snippet": None,
              "location": None, "selector": _copy(selector), "reasons": []}
    try:
        if kind in {"source", "adapter"}:
            if isinstance(parsed, _Unresolved):
                raise _Unresolved(parsed.code)
            _require(isinstance(parsed, ast.Module))
            if kind == "source":
                node = _source_node(parsed, selector)
                result["snippet"], result["location"] = _node_fragment(text, node)
                value = _source_value(node, selector)
            else:
                value, node = _adapter_node(parsed, selector["symbol"])
                result["snippet"], result["location"] = _node_fragment(text, node)
        else:
            try:
                snippet, selected = obligations._document_fragment(text, selector["fence_id"])
            except obligations._Unresolved as error:
                raise _Unresolved(error.reason["code"]) from None
            result["snippet"] = snippet
            result["location"] = _location(text, selected["start_offset"], selected["end_offset"])
            try:
                block = obligations._strict_json(snippet)
            except WorkbenchError:
                raise _Unresolved("invalid_document_json") from None
            if type(block) is not dict or selector["field"] not in block:
                raise _Unresolved("missing_document_object_key")
            value = block[selector["field"]]
        result["value"] = _copy(value)
        try:
            result["normalized_value"] = _copy(obligations._normalize(value, value_type))
        except obligations._Unresolved as error:
            raise _Unresolved(error.reason["code"]) from None
        result["resolved"] = True
    except _Unresolved as error:
        result["reasons"] = [{"code": error.code}]
    return result


def _pair(left: dict[str, Any], right: dict[str, Any]) -> str:
    if not left["resolved"] or not right["resolved"]:
        return "unresolved"
    return "match" if canonical(left["normalized_value"]) == canonical(right["normalized_value"]) else "mismatch"


def _observation(texts: dict[str, str], profile: dict[str, Any]) -> dict[str, Any]:
    source_tree, adapter_tree = _parse(texts["source"]), _parse(texts["adapter"])
    results, blockers = [], []
    for item in profile["mappings"]:
        endpoints = {
            "source": _endpoint("source", texts["source"], item["source_selector"], item["value_type"], source_tree),
            "adapter": _endpoint("adapter", texts["adapter"], {"symbol": item["adapter_symbol"]}, item["value_type"], adapter_tree),
            "document": _endpoint("document", texts["document"], item["document_selector"], item["value_type"]),
        }
        comparisons = {"source_adapter": _pair(endpoints["source"], endpoints["adapter"]),
                       "source_document": _pair(endpoints["source"], endpoints["document"]),
                       "adapter_document": _pair(endpoints["adapter"], endpoints["document"])}
        state = ("unresolved" if "unresolved" in comparisons.values() else
                 "mismatch" if "mismatch" in comparisons.values() else "match")
        comparisons["state"] = state
        results.append({**{key: item[key] for key in ("id", "scope", "annotation", "value_type")},
                        **endpoints, "comparison": comparisons})
        if state != "match":
            blockers.append({"mapping_id": item["id"], "code": "mapping_" + state})
    counts = {"mappings": len(results), **{state: sum(item["comparison"]["state"] == state for item in results)
                                         for state in ("match", "mismatch", "unresolved")}}
    return {"state": "blocked" if blockers else "current_for_human_review", "counts": counts,
            "results": results, "blockers": blockers, **FLAGS}


def _captured(source: Any, adapter: Any, document: Any, profile: Any) -> dict[str, Any]:
    texts = {"source": source, "adapter": adapter, "document": document}
    _require(all(_text(value) for value in texts.values()))
    return {"texts": texts, "profile": validate_profile(profile)}


def analyze_source_adapter(source: str, adapter: str, document: str, profile: dict[str, Any]) -> dict[str, Any]:
    """Capture and compare three text inputs without evaluating their Python."""
    try:
        captured = _captured(source, adapter, document, profile)
        payload = {"schema_version": 1, "rules_version": RULES_VERSION,
                   "created_at": datetime.now(timezone.utc).isoformat(), "captured": captured,
                   "observation": _observation(captured["texts"], captured["profile"]), "limits": list(LIMITS)}
        address = digest(payload)
        report = {**payload, "report_id": "sad_" + address, "report_digest": address}
        return validate_report(report)
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def validate_report(value: Any) -> dict[str, Any]:
    """Reconstruct the observation solely from the closed captured report."""
    try:
        report = _copy(value)
        _require(type(report) is dict and set(report) == REPORT_FIELDS)
        _require(type(report["schema_version"]) is int and report["schema_version"] == 1
                 and report["rules_version"] == RULES_VERSION and report["limits"] == LIMITS)
        _require(type(report["created_at"]) is str)
        timestamp = datetime.fromisoformat(report["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0)
        captured = report["captured"]
        _require(type(captured) is dict and set(captured) == CAPTURED_FIELDS)
        texts = captured["texts"]
        _require(type(texts) is dict and set(texts) == {"source", "adapter", "document"})
        expected_captured = _captured(texts["source"], texts["adapter"], texts["document"], captured["profile"])
        _require(canonical(captured) == canonical(expected_captured))
        expected = _observation(texts, expected_captured["profile"])
        _require(canonical(report["observation"]) == canonical(expected))
        payload = {key: child for key, child in report.items() if key not in {"report_id", "report_digest"}}
        address = digest(payload)
        _require(report["report_digest"] == address and report["report_id"] == "sad_" + address)
        return report
    except WorkbenchError:
        raise _invalid() from None
    except _ERRORS:
        raise _invalid() from None


def load_report(path: str | Path) -> dict[str, Any]:
    return validate_report(_json_text(read_text(path, MAX_REPORT_BYTES)))


def save_report(report: dict[str, Any], path: str | Path) -> None:
    """Create a validated report exclusively without replacing any file."""
    captured = validate_report(report)
    try:
        encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
        _require(len(encoded) <= MAX_REPORT_BYTES)
        with Path(path).open("xb") as stream:
            stream.write(encoded)
    except (OSError, ValueError, TypeError):
        raise WorkbenchError("source_adapter_io_failed", "Source-adapter report could not be saved") from None


def summary(report: dict[str, Any]) -> dict[str, Any]:
    captured = validate_report(report)
    observation = captured["observation"]
    return {**{key: captured[key] for key in ("report_id", "report_digest", "rules_version")},
            **{key: observation[key] for key in ("state", "counts", "results", "blockers")}, **FLAGS}
