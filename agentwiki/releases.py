"""Read and compare self-contained releases without opening a runtime database.

Checksums establish internal consistency, not authorship or factual truth. Code
in a release is parsed by the existing policy checks and is never executed.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import stat
from typing import Any

from .store import (
    CLAIM_ID, HASH, PLACEHOLDER, QUERY_SCOPES, RULES_VERSION,
    Store, WorkbenchError, digest, safe_file_id,
)


_DATA_FILES = {"knowledge.json", "sources.json", "search-index.json"}
_MANIFEST_KEYS = {
    "schema_version", "revision", "state_digest", "rules_version", "files", "release_id",
}


def _require(condition: bool, message: str, code: str = "invalid_release") -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        _require(key not in result, f"Duplicate JSON object key: {key}")
        result[key] = value
    return result


def _bad_constant(value: str) -> None:
    raise WorkbenchError("invalid_release", f"Non-finite JSON number: {value}")


def _json(data: bytes, name: str) -> Any:
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=_object_pairs,
                          parse_constant=_bad_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise WorkbenchError("invalid_release", f"Invalid JSON in {name}: {error}") from error


def _safe_name(name: Any) -> bool:
    return isinstance(name, str) and (
        name in _DATA_FILES | {"dependencies.json"}
        or safe_file_id(name, "document") or safe_file_id(name, "code")
    )


def _directory(path: str | Path) -> Path:
    directory = Path(path).absolute()
    _require(".." not in directory.parts, "Release path cannot contain '..'", "unsafe_path")
    for component in (directory, *directory.parents):
        _require(not component.is_symlink(), "Release path traverses a symlink", "unsafe_path")
    _require(directory.is_dir(), "Release must be an existing directory")
    return directory


def _regular_files(directory: Path) -> dict[str, Path]:
    """Inspect every entry, rejecting links and special files before reading."""
    files: dict[str, Path] = {}
    pending = [directory]
    while pending:
        current = pending.pop()
        for path in current.iterdir():
            mode = path.lstat().st_mode
            _require(not stat.S_ISLNK(mode), "Release contains a symlink", "unsafe_path")
            if stat.S_ISDIR(mode):
                pending.append(path)
            else:
                _require(stat.S_ISREG(mode), "Release contains a nonregular file", "unsafe_path")
                files[path.relative_to(directory).as_posix()] = path
    return files


def _manifest(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == _MANIFEST_KEYS,
             "Manifest has missing or unexpected fields")
    _require(type(value["schema_version"]) is int and value["schema_version"] in {1, 2},
             "Supported release schemas are 1 and 2")
    _require(type(value["revision"]) is int and value["revision"] >= 1,
             "Manifest revision must be a positive integer")
    _require(value["rules_version"] == RULES_VERSION, "Unsupported release policy version")
    _require(isinstance(value["state_digest"], str) and bool(HASH.fullmatch(value["state_digest"])),
             "Invalid state digest")
    _require(isinstance(value["files"], dict), "Manifest files must be an object")
    for name, entry in value["files"].items():
        _require(_safe_name(name), f"Unsafe or unsupported file path: {name}", "unsafe_path")
        _require(isinstance(entry, dict) and set(entry) == {"sha256", "bytes"},
                 f"Invalid manifest entry: {name}")
        _require(type(entry["bytes"]) is int and entry["bytes"] >= 0,
                 f"Invalid byte count: {name}")
        _require(isinstance(entry["sha256"], str) and bool(HASH.fullmatch(entry["sha256"])),
                 f"Invalid file digest: {name}")
    required = _DATA_FILES | ({"dependencies.json"} if value["schema_version"] == 2 else set())
    _require(required <= set(value["files"]), "Release is missing required data files")
    _require(value["schema_version"] == 2 or "dependencies.json" not in value["files"],
             "Dependency metadata requires release schema 2")
    identity = {key: item for key, item in value.items() if key != "release_id"}
    expected_id = f"r{value['revision']}-{digest(identity)[:16]}"
    _require(value["release_id"] == expected_id,
             "Release ID does not match its manifest", "release_corrupt")
    return value


def _sources(value: Any) -> dict[str, Any]:
    _require(isinstance(value, dict), "Sources must be an object")
    for identifier, source in value.items():
        _require(bool(CLAIM_ID.fullmatch(identifier)), f"Invalid source ID: {identifier}")
        _require(isinstance(source, dict) and set(source) == {"id", "text", "digest"},
                 f"Invalid source record: {identifier}")
        _require(source["id"] == identifier and isinstance(source["text"], str) and bool(source["text"]),
                 f"Invalid source content: {identifier}")
        expected = hashlib.sha256(source["text"].encode("utf-8")).hexdigest()
        _require(source["digest"] == expected, f"Source digest mismatch: {identifier}", "release_corrupt")
    return value


def _artifacts(value: Any, sources: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, dict), "Knowledge must be an artifact object")
    for identifier, body in value.items():
        _require(isinstance(body, dict) and body.get("id") == identifier,
                 f"Artifact ID does not match its map key: {identifier}")
        kind = body.get("kind")
        _require(isinstance(kind, str) and kind in {"claim", "document", "code"},
                 f"Invalid artifact kind: {identifier}")
        if kind == "claim":
            _require(set(body) == {"id", "kind", "scope", "value", "unit", "evidence"}
                     and bool(CLAIM_ID.fullmatch(identifier)), f"Invalid claim: {identifier}")
            _require(isinstance(body["scope"], str) and body["scope"] in QUERY_SCOPES,
                     f"Unregistered claim scope: {identifier}")
            scalar = body["value"]
            _require(isinstance(scalar, (str, int, float, bool))
                     and not (isinstance(scalar, float) and not math.isfinite(scalar)),
                     f"Invalid claim value: {identifier}")
            _require(isinstance(body["unit"], str) and len(body["unit"]) <= 40,
                     f"Invalid claim unit: {identifier}")
            evidence = body["evidence"]
            _require(isinstance(evidence, dict) and set(evidence) == {"source_id", "quote"},
                     f"Invalid claim evidence: {identifier}")
            source_id, quote = evidence["source_id"], evidence["quote"]
            _require(isinstance(source_id, str) and isinstance(quote, str) and bool(quote.strip()),
                     f"Invalid source or quote: {identifier}")
            _require(source_id in sources and quote in sources[source_id]["text"],
                     f"Unmatched source quote: {identifier}", "release_corrupt")
        else:
            _require(set(body) == {"id", "kind", "template", "bindings"}
                     and safe_file_id(identifier, kind), f"Invalid file artifact: {identifier}")
            template, bindings = body["template"], body["bindings"]
            _require(isinstance(template, str) and 0 < len(template) <= 200000,
                     f"Invalid template: {identifier}")
            _require(isinstance(bindings, list)
                     and all(isinstance(key, str) and CLAIM_ID.fullmatch(key) for key in bindings),
                     f"Invalid bindings: {identifier}")
            _require(len(bindings) == len(set(bindings)), f"Duplicate binding: {identifier}")
            remaining = PLACEHOLDER.sub("", template)
            _require(set(PLACEHOLDER.findall(template)) == set(bindings)
                     and "{{" not in remaining and "}}" not in remaining,
                     f"Template binding mismatch: {identifier}")
    errors = Store._invariant_errors(value)
    _require(not errors, f"Release violates artifact policy: {errors}", "release_corrupt")
    used = {body["evidence"]["source_id"] for body in value.values() if body["kind"] == "claim"}
    _require(set(sources) == used, "Sources must match exactly the evidence referenced by knowledge")
    return value


def _dependencies(value: Any, revision: int, artifacts: dict[str, Any]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == {"schema_version", "revision", "artifacts"},
             "Invalid dependency metadata")
    _require(type(value["schema_version"]) is int and value["schema_version"] == 1,
             "Unsupported dependency schema")
    _require(type(value["revision"]) is int and value["revision"] == revision,
             "Dependency metadata and release use different revisions", "release_mismatch")
    records = value["artifacts"]
    _require(isinstance(records, dict) and set(records) == set(artifacts),
             "Dependency records must cover exactly the release artifacts")
    for identifier, record in records.items():
        _require(isinstance(record, dict)
                 and set(record) == {"proposal_id", "baseline_revision", "readset", "queries", "write_ids"},
                 f"Invalid dependency record: {identifier}")
        _require(type(record["baseline_revision"]) is int
                 and 1 <= record["baseline_revision"] <= revision,
                 f"Invalid dependency baseline revision: {identifier}")
        proposal_id = record["proposal_id"]
        _require(proposal_id is None or isinstance(proposal_id, str) and bool(proposal_id),
                 f"Invalid proposal ID: {identifier}")
        readset, queries, write_ids = record["readset"], record["queries"], record["write_ids"]
        _require(isinstance(readset, dict) and all(
            isinstance(key, str) and (item is None or isinstance(item, str) and HASH.fullmatch(item))
            for key, item in readset.items()), f"Invalid dependency readset: {identifier}")
        _require(isinstance(queries, dict) and all(
            key in QUERY_SCOPES and isinstance(item, str) and HASH.fullmatch(item)
            for key, item in queries.items()), f"Invalid dependency queries: {identifier}")
        _require(isinstance(write_ids, list) and all(isinstance(key, str) for key in write_ids)
                 and len(write_ids) == len(set(write_ids)) and identifier in write_ids,
                 f"Invalid dependency write set: {identifier}")
    return records


def _rendered(artifacts: dict[str, Any], files: dict[str, bytes], index: Any,
              revision: int) -> None:
    rendered: dict[str, str] = {}
    documents = []
    for identifier, body in artifacts.items():
        if body["kind"] not in {"document", "code"}:
            continue
        def substitute(match: re.Match[str]) -> str:
            scalar = artifacts[match.group(1)]["value"]
            return repr(scalar) if body["kind"] == "code" else str(scalar)
        content = PLACEHOLDER.sub(substitute, body["template"])
        rendered[identifier] = content
        _require(files.get(identifier) == content.encode("utf-8"),
                 f"Rendered artifact does not match knowledge: {identifier}", "release_corrupt")
        if body["kind"] == "document":
            documents.append({"path": identifier, "text": content,
                              "terms": sorted(set(re.findall(r"\w+", content.casefold())))})
    actual = {name for name in files if name.startswith(("docs/", "code/"))}
    _require(actual == set(rendered), "Rendered files must match exactly the file artifacts")
    _require(isinstance(index, dict) and set(index) == {"revision", "documents"},
             "Invalid search index")
    _require(type(index["revision"]) is int and index["revision"] == revision,
             "Index and manifest must use one revision", "release_mismatch")
    _require(isinstance(index["documents"], list), "Index documents must be an array")
    expected_by_path = {entry["path"]: entry for entry in documents}
    actual_by_path = {}
    for entry in index["documents"]:
        _require(isinstance(entry, dict) and set(entry) == {"path", "text", "terms"}
                 and isinstance(entry["path"], str), "Invalid search document")
        _require(entry["path"] not in actual_by_path, "Duplicate search document")
        _require(isinstance(entry["text"], str) and isinstance(entry["terms"], list)
                 and all(isinstance(term, str) for term in entry["terms"]), "Invalid search text or terms")
        actual_by_path[entry["path"]] = entry
    _require(actual_by_path == expected_by_path,
             "Search index must contain the exact rendered documents and terms", "release_corrupt")


def load_release(path: str | Path) -> dict[str, Any]:
    """Load a verified release. No SQLite connection or agent code is executed.

    A release can be copied to another directory without changing its identity.
    Legacy schema 1 remains readable, with explicit missing-provenance warnings.
    The returned ``files`` includes manifest.json as well as its listed files.
    """
    try:
        directory = _directory(path)
        paths = _regular_files(directory)
        _require("manifest.json" in paths, "Release is missing manifest.json")
        manifest_bytes = paths["manifest.json"].read_bytes()
        manifest = _manifest(_json(manifest_bytes, "manifest.json"))
        _require(set(paths) == set(manifest["files"]) | {"manifest.json"},
                 "Release has missing or extra files", "release_corrupt")
        files = {"manifest.json": manifest_bytes}
        for name, entry in manifest["files"].items():
            data = paths[name].read_bytes()
            _require(len(data) == entry["bytes"] and hashlib.sha256(data).hexdigest() == entry["sha256"],
                     f"File size or digest mismatch: {name}", "release_corrupt")
            files[name] = data
        sources = _sources(_json(files["sources.json"], "sources.json"))
        artifacts = _artifacts(_json(files["knowledge.json"], "knowledge.json"), sources)
        _require(digest(artifacts) == manifest["state_digest"],
                 "Knowledge does not match state digest", "release_corrupt")
        index = _json(files["search-index.json"], "search-index.json")
        _rendered(artifacts, files, index, manifest["revision"])
        legacy = manifest["schema_version"] == 1
        dependencies = {} if legacy else _dependencies(
            _json(files["dependencies.json"], "dependencies.json"), manifest["revision"], artifacts)
        if not legacy:
            publication_errors = Store._publication_errors(artifacts, dependencies)
            _require(not publication_errors,
                     f"Release contains dependencies requiring review: {publication_errors}",
                     "release_needs_review")
        warnings = (["Legacy schema 1 has no proposal readsets or query dependencies; "
                     "impact analysis covers structural bindings and policy only."] if legacy else [])
        snapshot = {
            "revision": manifest["revision"], "artifacts": artifacts,
            "digests": {key: digest(body) for key, body in artifacts.items()},
            "queries": {scope: Store._query_digest(artifacts, scope) for scope in QUERY_SCOPES},
            "dependencies": dependencies,
            "sources": sources,
            "dependency_provenance_available": not legacy,
            "dependency_coverage": "bindings_only" if legacy else "complete",
        }
        return {"manifest": manifest, "files": files, "snapshot": snapshot, "warnings": warnings}
    except WorkbenchError:
        raise
    except (OSError, TypeError, ValueError, KeyError, OverflowError, RecursionError) as error:
        raise WorkbenchError("invalid_release", f"Cannot load release: {error}") from error


def _changes(before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    common = set(before) & set(after)
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "modified": sorted(key for key in common if before[key] != after[key]),
        "unchanged": sorted(key for key in common if before[key] == after[key]),
    }


def compare_releases(before: str | Path, after: str | Path) -> dict[str, Any]:
    """Compare two verified releases, separating semantic and rendered changes."""
    previous, current = load_release(before), load_release(after)
    return _compare_loaded_releases(previous, current)


def _compare_loaded_releases(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Reuse captured verified inputs so a caller can analyze the same snapshots."""
    old, new = previous["snapshot"]["artifacts"], current["snapshot"]["artifacts"]
    artifact_changes = _changes(previous["snapshot"]["digests"], current["snapshot"]["digests"])
    details = []
    for identifier in sorted(set(artifact_changes["added"] + artifact_changes["removed"]
                                 + artifact_changes["modified"])):
        old_body, new_body = old.get(identifier), new.get(identifier)
        fields = sorted(key for key in set(old_body or {}) | set(new_body or {})
                        if digest((old_body or {}).get(key)) != digest((new_body or {}).get(key)))
        categories = []
        if old_body is None:
            categories.append("added")
        elif new_body is None:
            categories.append("removed")
        else:
            if "kind" in fields:
                categories.append("kind")
            if set(fields) & {"value", "unit", "scope"}:
                categories.append("claim_value_or_scope")
            if "evidence" in fields:
                categories.append("evidence")
            if "template" in fields:
                categories.append("template")
            if "bindings" in fields:
                categories.append("bindings")
        details.append({"id": identifier, "fields": fields, "categories": categories,
                        "before_digest": previous["snapshot"]["digests"].get(identifier),
                        "after_digest": current["snapshot"]["digests"].get(identifier)})
    artifact_changes["details"] = details
    file_changes = _changes(previous["files"], current["files"])
    changed_files = sorted(name for group in ("added", "removed", "modified")
                           for name in file_changes[group] if name.startswith(("docs/", "code/")))
    return {
        "before": {"release_id": previous["manifest"]["release_id"],
                   "revision": previous["manifest"]["revision"]},
        "after": {"release_id": current["manifest"]["release_id"],
                  "revision": current["manifest"]["revision"]},
        "artifact_changes": artifact_changes, "file_changes": file_changes,
        "source_changes": _changes(
            {key: source["digest"] for key, source in previous["snapshot"]["sources"].items()},
            {key: source["digest"] for key, source in current["snapshot"]["sources"].items()}),
        "dependency_changes": _changes(
            {key: digest(record) for key, record in previous["snapshot"]["dependencies"].items()},
            {key: digest(record) for key, record in current["snapshot"]["dependencies"].items()}),
        "changed_rendered_files": changed_files,
        "template_changes": [item["id"] for item in details if "template" in item["categories"]],
        "claim_value_changes": [item["id"] for item in details
                                if "claim_value_or_scope" in item["categories"]],
        "evidence_changes": [item["id"] for item in details if "evidence" in item["categories"]],
        "warnings": list(dict.fromkeys(previous["warnings"] + current["warnings"])),
    }
