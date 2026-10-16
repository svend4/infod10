"""Advisory review of every explicitly selected write in one proposal.

One runtime context and one journal snapshot are stored once per review set.
Their capture is sequential. Complete write coverage is a bookkeeping fact,
not proof that the supplied statements explain the whole change correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from . import dossier
from .editorial import EditorialJournal, verify_journal_export
from .store import Store, WorkbenchError, canonical, digest


REVIEW_SET_RULES_VERSION = "review-set-1"
MAX_REVIEW_SET_BYTES = 25_000_000
MAX_BINDINGS = 100
REVIEW_SET_KEYS = {
    "schema_version", "review_set_rules_version", "dossier_rules_version", "created_at",
    "proposal_id", "bindings", "runtime_context", "journal_snapshot", "observation",
    "review_set_id", "review_set_digest",
}
LIMITS = [
    "Runtime and journal snapshots are sequential observations, not one atomic transaction.",
    "Write coverage enumerates proposal write IDs; it does not prove semantic coverage.",
    "Exact template matching binds bytes; statement fields and their mapping to prose remain caller-declared.",
    "Only existing document and code templates can obtain a matching binding in this version.",
    "Actor identities and kinds are self-declared; agreement and reused packets do not prove independence or truth.",
    "No review set or recheck grants Store.accept or publication rights.",
]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_review_set", message)


def _copy(value: Any) -> Any:
    try:
        encoded = canonical(value).encode("utf-8")
        _require(len(encoded) <= MAX_REVIEW_SET_BYTES, "JSON data exceeds 25 MB")
        return json.loads(encoded)
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_review_set", "Expected bounded, finite JSON data") from error


def _bindings(value: Any) -> list[dict[str, Any]]:
    """Validate and normalize a descriptor without consulting either database."""
    _require(isinstance(value, list) and len(value) <= MAX_BINDINGS, "Expected at most 100 bindings")
    identifiers = set()
    for item in value:
        _require(isinstance(item, dict) and set(item) == {"artifact_id", "packet_id", "pair"}, "Expected artifact_id, packet_id and pair")
        _require(all(isinstance(item[key], str) and item[key] for key in ["artifact_id", "packet_id"]), "Nonempty binding identifiers required")
        _require(item["artifact_id"] not in identifiers, "Duplicate artifact binding")
        identifiers.add(item["artifact_id"])
        dossier._pair(item["pair"])
    return sorted(value, key=lambda item: item["artifact_id"])


def _runtime_basis(context: dict[str, Any]) -> dict[str, Any]:
    """Exclude unrelated revision changes and the global source inventory."""
    return {
        "runtime_rules_version": context["runtime_rules_version"],
        "proposal_digest": context["proposal"]["digest"],
        "actual_readset": context["actual_readset"],
        "actual_queries": context["actual_queries"],
        "components": context.get("components", []),
    }


def _aggregate(context: dict[str, Any], bindings: list[dict[str, Any]],
               entries: list[dict[str, Any]]) -> dict[str, Any]:
    """Combine captured observations without reinterpreting historical semantics."""
    write_ids = sorted(body["id"] for body in context["proposal"]["payload"]["writes"])
    selected = [item["artifact_id"] for item in bindings]
    uncovered = sorted(set(write_ids) - set(selected))
    coverage = {
        "selected_write_ids": selected, "uncovered_write_ids": uncovered,
        "complete": not uncovered,
        "scope": "write_ids_only_not_semantic_coverage",
    }
    common = [dict(item) for item in context["runtime_errors"] + context["formal_errors"]]
    if context["proposal"]["status"] != "proposed":
        common.append({"code": "proposal_already_accepted"})
    blockers = list(common)
    shared = {canonical(item) for item in common}
    entry_summaries = []
    for entry in entries:
        local = []
        for item in entry["blockers"]:
            # A one-file observation records its own partial coverage. The
            # aggregate below is authoritative for coverage of this proposal.
            if item["code"] == "uncovered_writes" or canonical(item) in shared:
                continue
            local.append(dict(item))
            blockers.append({**item, "artifact_id": entry["artifact_id"]})
        effective = [dict(item) for item in common] + local
        entry_summaries.append({
            "artifact_id": entry["artifact_id"], "packet_id": entry["packet_id"],
            "state": "blocked" if effective else "current_for_human_review",
            "blockers": effective,
        })
    if uncovered:
        blockers.append({"code": "uncovered_writes", "artifact_ids": uncovered})
    packet_artifacts: dict[str, list[str]] = {}
    for item in bindings:
        packet_artifacts.setdefault(item["packet_id"], []).append(item["artifact_id"])
    reused = [
        {"packet_id": packet_id, "artifact_ids": artifact_ids, "independence_verified": False}
        for packet_id, artifact_ids in sorted(packet_artifacts.items()) if len(artifact_ids) > 1
    ]
    return {
        "proposal_id": context["proposal"]["id"],
        "proposal_digest": context["proposal"]["digest"],
        "observed_revision": context["observed_revision"],
        "proposal_status": context["proposal"]["status"],
        "runtime_basis_digest": digest(_runtime_basis(context)),
        "formal_head": dossier._formal_head(context),
        "component_head": digest(context.get("components", [])),
        "entries": entries, "entry_summaries": entry_summaries,
        "coverage": coverage, "packet_reuse": reused,
        "entry_coverage_scope": "each_entry_is_partial_global_coverage_is_authoritative",
        "blockers": blockers,
        "state": "blocked" if blockers else "current_for_human_review",
        "requires_human_review": True, "auto_accept": False, "atomic_capture": False,
        "limits": list(LIMITS),
    }


def _observe(context: dict[str, Any], snapshot: dict[str, Any],
             bindings: list[dict[str, Any]]) -> dict[str, Any]:
    dossier._context(context)
    verify_journal_export(snapshot)
    write_ids = {body["id"] for body in context["proposal"]["payload"]["writes"]}
    _require(all(item["artifact_id"] in write_ids for item in bindings), "Binding selects an artifact outside proposal writes")
    entries = [
        dossier._observe(context, snapshot, item["packet_id"], item["artifact_id"], item["pair"])
        for item in bindings
    ]
    return _aggregate(context, bindings, entries)


def _translate(error: WorkbenchError) -> WorkbenchError:
    return error if error.code == "invalid_review_set" else WorkbenchError("invalid_review_set", str(error))


def create_review_set(store: Store, journal: EditorialJournal, proposal_id: str,
                      bindings: list[dict[str, Any]]) -> dict[str, Any]:
    """Capture one advisory set, including explicit gaps and other blockers."""
    try:
        _require(isinstance(proposal_id, str) and bool(proposal_id), "Nonempty proposal identifier required")
        captured_bindings = _bindings(_copy(bindings))
        # Exactly one transaction in each store; per-entry work uses these
        # captured values only. No cross-database atomicity is implied.
        context = store.proposal_context(proposal_id)
        snapshot = journal.export_snapshot()
        observation = _observe(context, snapshot, captured_bindings)
        payload = {
            "schema_version": 1, "review_set_rules_version": REVIEW_SET_RULES_VERSION,
            "dossier_rules_version": dossier.BRIDGE_RULES_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "proposal_id": proposal_id, "bindings": captured_bindings,
            "runtime_context": context, "journal_snapshot": snapshot,
            "observation": observation,
        }
        address = digest(payload)
        result = {**payload, "review_set_id": "rset_" + address, "review_set_digest": address}
        _validate(result)
        return result
    except WorkbenchError as error:
        raise _translate(error) from error


def _validate(value: Any) -> None:
    """Check captured records, not the present implementation's semantic verdict."""
    try:
        _require(isinstance(value, dict) and set(value) == REVIEW_SET_KEYS, "Unexpected review-set fields")
        _require(type(value["schema_version"]) is int and value["schema_version"] == 1, "Unsupported review-set schema")
        _require(all(isinstance(value[key], str) and value[key] for key in ["proposal_id", "review_set_rules_version", "dossier_rules_version", "created_at"]), "Missing review-set metadata")
        timestamp = datetime.fromisoformat(value["created_at"])
        _require(timestamp.tzinfo is not None and timestamp.utcoffset().total_seconds() == 0, "Creation time must have a UTC offset")
        _require(len(canonical(value).encode("utf-8")) <= MAX_REVIEW_SET_BYTES, "Review set exceeds 25 MB")
        payload = {key: item for key, item in value.items() if key not in {"review_set_id", "review_set_digest"}}
        address = digest(payload)
        _require(value["review_set_digest"] == address and value["review_set_id"] == "rset_" + address, "Review-set content address mismatch")
        bindings = _bindings(value["bindings"])
        _require(bindings == value["bindings"], "Bindings must use canonical artifact order")
        context, snapshot = value["runtime_context"], value["journal_snapshot"]
        dossier._context(context)
        verify_journal_export(snapshot)
        _require(context["proposal"]["id"] == value["proposal_id"], "Proposal identity mismatch")
        write_ids = {body["id"] for body in context["proposal"]["payload"]["writes"]}
        _require(all(item["artifact_id"] in write_ids for item in bindings), "Binding selects an artifact outside proposal writes")
        observation = value["observation"]
        _require(isinstance(observation, dict) and isinstance(observation.get("entries"), list), "Missing entry observations")
        entries = observation["entries"]
        _require(len(entries) == len(bindings), "Entry observations do not cover bindings")
        for binding, entry in zip(bindings, entries):
            # Reuse the dossier's historical integrity checks. This constructs
            # no additional persistent copies of the shared captured records
            # and deliberately does not call compare_statements/assess_export.
            synthetic = {
                "schema_version": 1, "bridge_rules_version": value["dossier_rules_version"],
                "created_at": value["created_at"], "proposal_id": value["proposal_id"],
                "packet_id": binding["packet_id"], "artifact_id": binding["artifact_id"],
                "pair": binding["pair"], "runtime_context": context,
                "journal_snapshot": snapshot, "observation": entry,
            }
            entry_digest = digest(synthetic)
            dossier._validate({**synthetic, "dossier_id": "dos_" + entry_digest, "dossier_digest": entry_digest})
        expected = _aggregate(context, bindings, entries)
        _require(digest(observation) == digest(expected), "Aggregate observation differs from captured entries")
    except WorkbenchError as error:
        raise _translate(error) from error
    except (KeyError, TypeError, ValueError, AttributeError, OverflowError, RecursionError, UnicodeError) as error:
        raise WorkbenchError("invalid_review_set", "Malformed review set: " + str(error)) from error


def recheck_review_set(saved: dict[str, Any], store: Store, journal: EditorialJournal,
                       current_bindings: list[dict[str, Any]]) -> dict[str, Any]:
    """Recapture relevant bases while preserving the original review snapshot."""
    try:
        original = _copy(saved)
        _validate(original)
        bindings = _bindings(_copy(current_bindings))
        old_ids = [(item["artifact_id"], item["packet_id"]) for item in original["bindings"]]
        current_ids = [(item["artifact_id"], item["packet_id"]) for item in bindings]
        _require(old_ids == current_ids, "Recheck must keep the same artifact and packet bindings")
        context = store.proposal_context(original["proposal_id"])
        snapshot = journal.export_snapshot()
        current = _observe(context, snapshot, bindings)
        previous = original["observation"]
        changes = []
        for field, code in [
            ("proposal_digest", "proposal_changed"),
            ("runtime_basis_digest", "runtime_basis_changed"),
            ("formal_head", "formal_review_head_changed"),
            ("component_head", "components_changed"),
            ("proposal_status", "proposal_status_changed"),
        ]:
            if digest(previous[field]) != digest(current[field]):
                changes.append({"code": code})
        if original["review_set_rules_version"] != REVIEW_SET_RULES_VERSION:
            changes.append({"code": "review_set_rules_changed"})
        if original["dossier_rules_version"] != dossier.BRIDGE_RULES_VERSION:
            changes.append({"code": "dossier_rules_changed"})
        per_artifact = []
        for old_binding, new_binding, old_entry, new_entry in zip(original["bindings"], bindings, previous["entries"], current["entries"]):
            entry_changes = []
            for field, code in [
                ("runtime_basis_digest", "runtime_basis_changed"),
                ("packet_digest", "packet_changed"),
                ("decision_heads", "decision_heads_changed"),
                ("binding", "template_binding_changed"),
                ("editorial", "editorial_assessment_changed"),
                ("blockers", "blockers_changed"),
            ]:
                if digest(old_entry[field]) != digest(new_entry[field]):
                    entry_changes.append({"code": code})
            if digest(old_binding["pair"]) != digest(new_binding["pair"]):
                entry_changes.append({"code": "current_input_changed"})
            if entry_changes:
                changes.append({"code": "artifact_review_changed", "artifact_id": new_binding["artifact_id"]})
            per_artifact.append({"artifact_id": new_binding["artifact_id"], "changes": entry_changes})
        if digest(previous["blockers"]) != digest(current["blockers"]):
            changes.append({"code": "blockers_changed"})
        state = "blocked" if current["blockers"] else "needs_refresh" if changes else "current_for_human_review"
        return {
            "schema_version": 1, "review_set_rules_version": REVIEW_SET_RULES_VERSION,
            "review_set_id": original["review_set_id"], "review_set_digest": original["review_set_digest"],
            "freshness": "stale" if changes else "current", "state": state,
            "changes": changes, "per_artifact_changes": per_artifact,
            "blockers": current["blockers"], "current_observation": current,
            "original_observed_revision": original["runtime_context"]["observed_revision"],
            "current_observed_revision": context["observed_revision"],
            "journal_snapshot_changed": original["journal_snapshot"]["export_digest"] != snapshot["export_digest"],
            "requires_human_review": True, "auto_accept": False, "atomic_capture": False,
            "limits": list(LIMITS),
        }
    except WorkbenchError as error:
        raise _translate(error) from error


def _read_json(path: str | Path) -> Any:
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            _require(key not in result, "Duplicate JSON object key")
            result[key] = value
        return result

    def constant(value: str) -> None:
        raise WorkbenchError("invalid_review_set", "Non-finite JSON constant")

    with Path(path).open("rb") as stream:
        data = stream.read(MAX_REVIEW_SET_BYTES + 1)
    _require(len(data) <= MAX_REVIEW_SET_BYTES, "JSON file exceeds 25 MB")
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise WorkbenchError("invalid_review_set", "Invalid review-set JSON") from error


def load_bindings(path: str | Path) -> list[dict[str, Any]]:
    """Read a bounded JSON array of artifact, packet and current-pair bindings."""
    try:
        return _bindings(_read_json(path))
    except WorkbenchError as error:
        raise _translate(error) from error


def load_review_set(path: str | Path) -> dict[str, Any]:
    value = _read_json(path)
    _validate(value)
    return value


def save_review_set(value: dict[str, Any], path: str | Path) -> None:
    """Write exclusively: a saved review snapshot is never overwritten."""
    captured = _copy(value)
    _validate(captured)
    encoded = (json.dumps(captured, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("utf-8")
    _require(len(encoded) <= MAX_REVIEW_SET_BYTES, "Formatted review set exceeds 25 MB")
    with Path(path).open("xb") as stream:
        stream.write(encoded)
