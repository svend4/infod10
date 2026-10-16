"""Load authored examples without using their annotations as analysis input.

Local source snapshots describe fictional scenarios. Their hashes establish byte
identity, not factual truth or logical support for a statement.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .semantic import compare_statements, validate_sources, validate_statement
from .store import WorkbenchError


MAX_CORPUS_BYTES = 5 * 1024 * 1024
NAME = re.compile(r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*\Z")
CATEGORY = re.compile(r"[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*\Z")
CASE_KEYS = {
    "schema_version", "case_id", "title", "category", "before", "after",
    "sources", "annotation", "limitations",
}
ANNOTATION_KEYS = {"relation", "action", "rationale", "status"}
ANNOTATION_STATUS = "project_authored_illustration"


def default_corpus() -> Path:
    return Path(__file__).resolve().parent.parent / "corpora" / "semantic-v1" / "cases.jsonl"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_corpus", message)


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise WorkbenchError("invalid_corpus", f"Non-finite JSON number: {value}")


def _validate_case(case: Any) -> None:
    _require(isinstance(case, dict) and set(case) == CASE_KEYS,
             "Case must contain exactly the documented schema fields")
    _require(type(case["schema_version"]) is int and case["schema_version"] == 1,
             "Unsupported case schema_version")
    _require(isinstance(case["case_id"], str) and NAME.fullmatch(case["case_id"]) is not None,
             "Invalid case_id; use lowercase words separated by hyphens")
    _require(isinstance(case["category"], str) and CATEGORY.fullmatch(case["category"]) is not None,
             "Invalid category; use lowercase words separated by hyphens or underscores")
    _require(_text(case["title"]), "Case title must be nonempty text")
    notes = case["annotation"]
    _require(isinstance(notes, dict) and set(notes) == ANNOTATION_KEYS,
             "Annotation must contain relation, action, rationale and status")
    _require(all(_text(value) for value in notes.values()),
             "Annotation values must be nonempty text")
    _require(notes["status"] == ANNOTATION_STATUS,
             "Annotation must identify its project-authored illustrative status")
    _require(isinstance(case["limitations"], list)
             and all(_text(item) for item in case["limitations"]),
             "Case limitations must be a list of nonempty strings")
    validate_statement(case["before"])
    validate_statement(case["after"])
    validate_sources(case["sources"])


def load_corpus(path: Path | None = None) -> dict[str, Any]:
    """Validate JSONL structure and hash its exact bytes, including whitespace."""
    corpus_path = Path(path) if path is not None else default_corpus()
    try:
        with corpus_path.open("rb") as handle:
            raw = handle.read(MAX_CORPUS_BYTES + 1)
        _require(len(raw) <= MAX_CORPUS_BYTES, "Corpus exceeds the 5 MiB limit")
        content = raw.decode("utf-8")
    except (OSError, UnicodeError) as error:
        raise WorkbenchError("invalid_corpus", f"Cannot read corpus: {error}") from error
    cases: dict[str, Any] = {}
    categories: dict[str, int] = {}
    nonempty_lines = 0
    for line_number, line in enumerate(content.splitlines(), 1):
        if not line.strip():
            continue
        nonempty_lines += 1
        try:
            case = json.loads(line, object_pairs_hook=_object, parse_constant=_constant)
            _validate_case(case)
            identifier = case["case_id"]
            _require(identifier not in cases, f"Duplicate case_id: {identifier}")
        except (ValueError, RecursionError, WorkbenchError) as error:
            raise WorkbenchError("invalid_corpus", f"Line {line_number}: {error}") from error
        cases[identifier] = case
        category = case["category"]
        categories[category] = categories.get(category, 0) + 1
    _require(nonempty_lines > 0, "Corpus contains no cases")
    _require(nonempty_lines == len(cases), "Case count does not match nonempty JSONL lines")
    return {
        "corpus_id": corpus_path.resolve().parent.name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "case_count": nonempty_lines,
        "categories": dict(sorted(categories.items())),
        "cases": cases,
    }


def list_cases(path: Path | None = None, category: str | None = None) -> list[dict[str, Any]]:
    """Return display metadata; an annotation is a manual note, not a verdict."""
    corpus = load_corpus(path)
    if category is not None and category not in corpus["categories"]:
        raise WorkbenchError("unknown_category", f"Unknown corpus category: {category}")
    return [
        {key: case[key] for key in ("case_id", "category", "title", "annotation", "limitations")}
        for case in corpus["cases"].values()
        if category is None or case["category"] == category
    ]


def _lookup(corpus: dict[str, Any], case_id: str) -> dict[str, Any]:
    if case_id not in corpus["cases"]:
        raise WorkbenchError("unknown_case", f"Unknown corpus case: {case_id}")
    return corpus["cases"][case_id]


def get_case(case_id: str, path: Path | None = None) -> dict[str, Any]:
    return _lookup(load_corpus(path), case_id)


def analyze_case(case_id: str, path: Path | None = None) -> dict[str, Any]:
    corpus = load_corpus(path)
    case = _lookup(corpus, case_id)
    analysis = compare_statements(case["before"], case["after"], case["sources"])
    return {
        "corpus": {"id": corpus["corpus_id"], "digest": corpus["sha256"]},
        "case_id": case["case_id"],
        "category": case["category"],
        "title": case["title"],
        "analysis": analysis,
        "annotation": case["annotation"],
        "annotation_usage": "display_only; not used as analyzer input",
    }
