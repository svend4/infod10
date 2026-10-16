"""Explain declared dependencies without reading files or executing agent code.

The graph is a conservative account of explicit bindings, evidence references
and accepted proposal read sets. It does not discover dependencies in prose or
Python, establish that a quotation supports a claim, or implement incremental
publication. A release remains a complete pinned snapshot.
"""

from __future__ import annotations

from collections import deque
from typing import Any

from .store import POLICY_KEYS, QUERY_SCOPES, WorkbenchError


RULE_NODE = "rule:timeout_times_attempts"
RELEASE_NODE = "release:snapshot"
REVIEW_EDGE_KINDS = frozenset({"evidence", "readset", "query_read"})


def _artifacts(snapshot: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get("artifacts"), dict):
        raise WorkbenchError("invalid_snapshot", "Snapshot needs an artifacts object")
    artifacts = snapshot["artifacts"]
    if any(not isinstance(key, str) or not isinstance(body, dict)
           for key, body in artifacts.items()):
        raise WorkbenchError("invalid_snapshot", "Artifact IDs and bodies must be strings and objects")
    return artifacts


def dependency_graph(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Return a stable source-to-consumer graph for one in-memory snapshot.

    An edge ``source -> target`` means a change to source can affect target.
    Empty registered scope queries are nodes too: an insertion can invalidate
    a query even when no previous member artifact existed. Missing read-set
    entries remain explicit absent artifact nodes.
    """
    artifacts = _artifacts(snapshot)
    nodes: dict[str, dict[str, Any]] = {}
    edges: set[tuple[str, str, str]] = set()

    def node(identifier: str, kind: str, **details: Any) -> str:
        if identifier not in nodes:
            nodes[identifier] = {"id": identifier, "kind": kind, **details}
        return identifier

    def artifact_node(identifier: str) -> str:
        body = artifacts.get(identifier)
        return node("artifact:" + identifier, "artifact", artifact_id=identifier,
                    artifact_kind=body.get("kind") if body else None,
                    present=identifier in artifacts)

    def edge(source: str, target: str, kind: str) -> None:
        edges.add((source, target, kind))

    declared_coverage = snapshot.get("dependency_coverage")
    legacy = declared_coverage == "bindings_only"
    if isinstance(declared_coverage, dict):
        legacy = legacy or bool(declared_coverage.get("legacy", False))
    dependencies_available = (isinstance(snapshot.get("dependencies"), dict)
                              and not legacy
                              and snapshot.get("dependency_provenance_available", True) is not False)
    knowledge = node("derived:knowledge.json", "derived", path="knowledge.json")
    sources = node("derived:sources.json", "derived", path="sources.json")
    search = node("derived:search-index.json", "derived", path="search-index.json")
    dependency_records = (node("derived:dependencies.json", "derived", path="dependencies.json")
                          if dependencies_available else None)
    rule = node(RULE_NODE, "rule", rule="timeout_times_attempts")
    release = node(RELEASE_NODE, "release", revision=snapshot.get("revision"))
    for scope in QUERY_SCOPES:
        node("query:" + scope, "query", scope=scope)
    for identifier in sorted(artifacts):
        artifact_node(identifier)
    snapshot_sources = snapshot.get("sources", {})
    if isinstance(snapshot_sources, dict):
        for source_id in sorted(snapshot_sources):
            if isinstance(source_id, str):
                node("source:" + source_id, "source", source_id=source_id)

    for identifier, body in sorted(artifacts.items()):
        current = artifact_node(identifier)
        edge(current, knowledge, "knowledge_member")
        if dependency_records is not None:
            edge(current, dependency_records, "dependency_record")
        edge(current, release, "release_member")
        scope = body.get("scope")
        if scope in QUERY_SCOPES:
            edge(current, "query:" + scope, "scope_member")
        evidence = body.get("evidence")
        if body.get("kind") == "claim" and isinstance(evidence, dict):
            source_id = evidence.get("source_id")
            if isinstance(source_id, str):
                source = node("source:" + source_id, "source", source_id=source_id)
                edge(source, current, "evidence")
                edge(source, sources, "source_member")
                # sources.json contains only sources selected by current claims.
                edge(current, sources, "source_selection")
        if body.get("kind") in {"document", "code"}:
            bindings = body.get("bindings", [])
            if not isinstance(bindings, list) or any(not isinstance(key, str) for key in bindings):
                raise WorkbenchError("invalid_snapshot", "File bindings must be a list of artifact IDs")
            for binding in sorted(set(bindings)):
                edge(artifact_node(binding), current, "binding")
            if body["kind"] == "document":
                edge(current, search, "search_member")

    for identifier in POLICY_KEYS:
        if identifier in artifacts:
            edge(artifact_node(identifier), rule, "rule_input")
    for derived in (knowledge, sources, search):
        edge(derived, release, "release_member")
    if dependency_records is not None:
        edge(dependency_records, release, "release_member")
    edge(rule, release, "release_gate")

    dependencies = snapshot.get("dependencies")
    if dependencies is not None and not isinstance(dependencies, dict):
        raise WorkbenchError("invalid_snapshot", "Proposal dependencies must be an object")
    dependencies = dependencies or {}
    covered: list[str] = []
    proposal_covered: list[str] = []
    for identifier in sorted(artifacts):
        metadata = dependencies.get(identifier)
        if metadata is None:
            continue
        if not isinstance(metadata, dict):
            raise WorkbenchError("invalid_snapshot", "Artifact dependency metadata must be an object")
        reads = metadata.get("readset", {})
        queries = metadata.get("queries", {})
        writes = metadata.get("write_ids", [identifier])
        if (not isinstance(reads, dict) or any(not isinstance(key, str) for key in reads)
                or not isinstance(queries, dict) or any(scope not in QUERY_SCOPES for scope in queries)
                or not isinstance(writes, list) or any(not isinstance(key, str) for key in writes)):
            raise WorkbenchError("invalid_snapshot", "Malformed artifact dependency metadata")
        covered.append(identifier)
        if metadata.get("proposal_id") is not None:
            proposal_covered.append(identifier)
        bindings = set(artifacts[identifier].get("bindings", []))
        # The self read is a compare-and-swap precondition. Other written
        # artifacts can still be relevant cross-file reads, so retain them.
        # A duplicate binding is already a stronger, explicit rendering edge.
        excluded = {identifier} | bindings
        for read_id in sorted(set(reads) - excluded):
            edge(artifact_node(read_id), artifact_node(identifier), "readset")
        for scope in sorted(queries):
            edge("query:" + scope, artifact_node(identifier), "query_read")

    available = dependencies_available
    missing = sorted(set(artifacts) - set(covered))
    warnings: list[dict[str, Any]] = []
    if not available:
        warnings.append({"code": "proposal_dependencies_unavailable",
                         "detail": "Legacy or incomplete snapshot: accepted proposal read/query edges are unavailable."})
    if missing:
        warnings.append({"code": "artifact_dependency_metadata_missing", "artifact_ids": missing,
                         "detail": "Missing metadata does not establish independence."})
    return {
        "revision": snapshot.get("revision"),
        "nodes": [nodes[key] for key in sorted(nodes)],
        "edges": [{"source": source, "target": target, "kind": kind}
                  for source, target, kind in sorted(edges)],
        "coverage": {
            "declared_only": True,
            "proposal_dependencies_available": available,
            "artifacts_with_dependency_metadata": covered,
            "artifacts_with_proposal_dependencies": proposal_covered,
            "artifacts_without_dependency_metadata": missing,
            "warnings": warnings,
            "declared_graph_limit": "Only explicit bindings, evidence IDs, scope membership, accepted read/query sets and the registered budget rule are represented. Prose and program semantics are not inferred.",
            "publication_limit": "Candidate selection does not skip acceptance invariants or replace complete pinned release publication; revision metadata can change even for otherwise unaffected content.",
        },
    }


def analyze_impact(snapshot: dict[str, Any], changed: list[str]) -> dict[str, Any]:
    """Explain reachable candidates, including one shortest witness per node.

    ``changed`` accepts an existing raw artifact ID or a known qualified graph
    node ID, such as ``source:fixture-spec`` or ``query:platforms:api-v1``.
    A binding-only path selects rendering. A path containing evidence, query
    reading or another explicit read selects review. A file can need both.
    Neither set is a claim that its resulting bytes must change.
    """
    graph = dependency_graph(snapshot)
    artifacts = snapshot["artifacts"]
    if not isinstance(changed, list) or any(not isinstance(item, str) for item in changed):
        raise WorkbenchError("unknown_dependency", "Changed dependencies must be a list of known IDs")
    known = {item["id"] for item in graph["nodes"]}
    seeds: set[str] = set()
    for item in changed:
        identifier = "artifact:" + item if item in artifacts else item
        if identifier not in known:
            raise WorkbenchError("unknown_dependency", "Unknown dependency: " + item)
        seeds.add(identifier)
    ordered_seeds = sorted(seeds)
    adjacent: dict[str, list[tuple[str, str]]] = {key: [] for key in known}
    for item in graph["edges"]:
        adjacent[item["source"]].append((item["target"], item["kind"]))
    for neighbors in adjacent.values():
        neighbors.sort()

    paths = {key: [key] for key in ordered_seeds}
    path_kinds: dict[str, list[str]] = {key: [] for key in ordered_seeds}
    pending = deque(ordered_seeds)
    while pending:
        current = pending.popleft()
        for target, kind in adjacent[current]:
            if target not in paths:
                paths[target] = paths[current] + [target]
                path_kinds[target] = path_kinds[current] + [kind]
                pending.append(target)

    render_paths = {key: [key] for key in ordered_seeds}
    pending = deque(ordered_seeds)
    while pending:
        current = pending.popleft()
        for target, kind in adjacent[current]:
            if kind == "binding" and target not in render_paths:
                render_paths[target] = render_paths[current] + [target]
                pending.append(target)

    # Keeping both states prevents a short binding path from concealing a
    # different path that needs review. At most two states per node are visited.
    review_state_paths = {(key, False): [key] for key in ordered_seeds}
    review_state_kinds: dict[tuple[str, bool], list[str]] = {
        (key, False): [] for key in ordered_seeds}
    states = deque((key, False) for key in ordered_seeds)
    while states:
        current, needs_review = states.popleft()
        state = (current, needs_review)
        for target, kind in adjacent[current]:
            next_state = (target, needs_review or kind in REVIEW_EDGE_KINDS)
            if next_state not in review_state_paths:
                review_state_paths[next_state] = review_state_paths[state] + [target]
                review_state_kinds[next_state] = review_state_kinds[state] + [kind]
                states.append(next_state)

    file_ids = sorted(key for key, body in artifacts.items()
                      if body.get("kind") in {"document", "code"})
    affected_files = [key for key in file_ids if "artifact:" + key in paths]
    render_files = [key for key in file_ids if "artifact:" + key in render_paths]
    review_files = [key for key in file_ids if ("artifact:" + key, True) in review_state_paths]
    affected_artifacts = sorted(key for key in artifacts
                                if "artifact:" + key in paths and "artifact:" + key not in seeds)
    return {
        "revision": graph["revision"],
        "changed": ordered_seeds,
        "affected_artifacts": affected_artifacts,
        "affected_files": affected_files,
        "render_files": render_files,
        "review_files": review_files,
        "rules": sorted(key.removeprefix("rule:") for key in paths if key.startswith("rule:")),
        "derived": sorted(key.removeprefix("derived:") for key in paths if key.startswith("derived:")),
        "paths": {key: paths[key] for key in sorted(paths)},
        "path_kinds": {key: path_kinds[key] for key in sorted(path_kinds)},
        "render_paths": {key: render_paths["artifact:" + key] for key in render_files},
        "review_paths": {key: review_state_paths[("artifact:" + key, True)] for key in review_files},
        "review_path_kinds": {key: review_state_kinds[("artifact:" + key, True)] for key in review_files},
        "counts": {
            "total_files": len(file_ids),
            "affected_files": len(affected_files),
            "render_files": len(render_files),
            "review_files": len(review_files),
            "unaffected_files": len(file_ids) - len(affected_files),
            "candidate_file_checks": len(affected_files),
            "full_file_checks": len(file_ids),
            "reachable_nodes": len(paths),
            "graph_nodes": len(known),
            "graph_edges": len(graph["edges"]),
        },
        "coverage": graph["coverage"],
    }
