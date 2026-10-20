"""Opt-in continuity of exact local review through unrelated catalog edits.

The caller owns the existing Store transaction. Old votes are never rewritten.
Every retained opinion still needs its original current grant and current head,
which governance checks independently. SQLite, its owner and the clock remain
trusted; these hashes provide internal consistency, not external attestation.
"""

from __future__ import annotations

import math
import re
import sqlite3
import time
from typing import Any

from .store import HASH, Store, WorkbenchError, canonical, digest


RULES_VERSION = "review-continuity-1"
MAX_TRANSITIONS = 64
MAX_STATUS_TRANSITIONS = 100
MAX_TRANSITION_BYTES = 16_000
MAX_POLICY_BYTES = 1_000_000
MAX_PACKET_BYTES = 4_000_000
EXCLUDED_BASIS_FIELDS = {
    "packet_id", "packet_digest", "content_policy_generation", "content_policy_digest",
    "governance_policy_generation", "governance_policy_digest",
}
STAMP_FIELDS = {"rules_version", "activation_generation", "basis_id", "basis_digest"}
TRANSITION_FIELDS = {
    "schema_version", "rules_version", "activation_generation", "created_at",
    "from_content_policy_generation", "from_content_policy_digest",
    "to_content_policy_generation", "to_content_policy_digest",
    "from_governance_policy_generation", "from_governance_policy_digest",
    "to_governance_policy_generation", "to_governance_policy_digest",
    "administrator_grant_id",
}
LIMITS = [
    "Continuity covers the same immutable proposal and the entire selected review basis, not another proposal or a claim of prose truth.",
    "Every intervening governance increment must be a recorded content-policy transition preserving the selected catalog context on both sides.",
    "A selected-policy change followed by a return to its old value does not restore the old opinion.",
    "A direct governance policy update, even with the same body, is a barrier rather than a continuity transition.",
    "At most 64 intervening transitions are examined; a recent verified direct-policy barrier invalidates older opinions, while otherwise unresolved history requires reconsideration or grant revocation.",
    "Original grant validity, current heads, scope, contributor separation, formal review and runtime freshness remain separate requirements.",
    "The SQLite owner and local clock remain trusted; hashes do not authenticate people, establish independent expertise or externally attest history.",
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS review_continuity_settings (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), rules_version TEXT NOT NULL,
    activation_generation INTEGER NOT NULL, content_policy_generation INTEGER NOT NULL,
    content_policy_digest TEXT NOT NULL, enabled_at REAL NOT NULL,
    activation_revision INTEGER NOT NULL, administrator_grant_id TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_continuity_transitions (
    transition_id TEXT PRIMARY KEY, digest TEXT NOT NULL UNIQUE,
    from_governance_generation INTEGER NOT NULL UNIQUE,
    to_governance_generation INTEGER NOT NULL UNIQUE,
    body TEXT NOT NULL,
    CHECK(to_governance_generation=from_governance_generation+1)
);
"""


def _require(condition: bool, code: str, detail: str) -> None:
    if not condition:
        raise WorkbenchError(code, detail)


def _integer(value: Any) -> bool:
    return type(value) is int and 1 <= value < 2**63


def _hash(value: Any) -> bool:
    return isinstance(value, str) and bool(HASH.fullmatch(value))


def _same(left: Any, right: Any) -> bool:
    return canonical(left) == canonical(right)


def enabled(connection: sqlite3.Connection) -> bool:
    return connection.execute("SELECT 1 FROM review_continuity_settings WHERE singleton=1").fetchone() is not None


def _settings(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM review_continuity_settings WHERE singleton=1").fetchone()
    if row is None:
        raise WorkbenchError("continuity_disabled", "Review continuity is not enabled")
    result = {key: row[key] for key in row.keys() if key != "singleton"}
    _require(result["rules_version"] == RULES_VERSION
             and _integer(result["activation_generation"])
             and _integer(result["content_policy_generation"])
             and _hash(result["content_policy_digest"])
             and type(result["activation_revision"]) is int and result["activation_revision"] >= 1
             and type(result["enabled_at"]) in {int, float} and math.isfinite(result["enabled_at"])
             and isinstance(result["administrator_grant_id"], str)
             and bool(re.fullmatch(r"gvg_[0-9a-f]{32}", result["administrator_grant_id"])),
             "continuity_history_invalid", "Unsupported or malformed continuity activation")
    return result


def _policy_record(connection: sqlite3.Connection, kind: str, generation: Any) -> dict[str, Any]:
    """Read bounded canonical historical policy, never a current artifact."""
    from . import content_routing, governance, obligations

    _require(_integer(generation), "continuity_history_invalid", "Invalid historical policy generation")
    table = "content_policies" if kind == "content" else "governance_policies"
    size = connection.execute("SELECT length(CAST(body AS BLOB)),typeof(body) FROM " + table
                              + " WHERE generation=?", (generation,)).fetchone()
    _require(size is not None and size[1] == "text" and size[0] <= MAX_POLICY_BYTES,
             "continuity_history_invalid", "Historical policy is missing or exceeds its bound")
    row = connection.execute("SELECT * FROM " + table + " WHERE generation=?", (generation,)).fetchone()
    descriptor = obligations._strict_json(row["body"])
    normalized = (content_routing.validate_policy(descriptor) if kind == "content"
                  else governance._policy(descriptor))
    _require(canonical(normalized) == row["body"] and digest(normalized) == row["digest"],
             "continuity_history_invalid", "Historical policy address differs from its body")
    result = {"generation": generation, "policy": normalized, "policy_digest": row["digest"],
              "administrator_grant_id": row["administrator_grant_id"]}
    if kind == "content":
        result["governance_policy_generation"] = row["governance_policy_generation"]
    return result


def _record_matches(given: dict[str, Any], recorded: dict[str, Any]) -> bool:
    return all(key in given and _same(given[key], recorded[key])
               for key in ("generation", "policy", "policy_digest"))


def enable(connection: sqlite3.Connection, *, credential: Any,
           expected_policy_generation: Any) -> dict[str, Any]:
    from . import authorship, content_gate, governance

    if enabled(connection):
        raise WorkbenchError("continuity_already_enabled", "Review continuity activation is one-way")
    if not governance.enabled(connection) or not authorship.enabled(connection) or not content_gate.enabled(connection):
        raise WorkbenchError("continuity_prerequisites_required", "Enable governance, authorship and content first")
    administrator = governance.authorize(connection, credential, "administrator")
    content = content_gate.current_policy(connection)
    _require(content["policy"]["schema_version"] == 2, "continuity_schema_required",
             "Review continuity requires a section catalog (content policy schema 2)")
    current = governance.current_policy(connection)
    checked = _policy_record(connection, "governance", current["generation"])
    _require(_record_matches(current, checked), "continuity_history_invalid", "Governance policy changed internally")
    changed = governance.set_policy(connection, current["policy"], credential=credential,
                                    expected_generation=expected_policy_generation)
    now = time.time()
    revision = Store._revision(connection)
    connection.execute("INSERT INTO review_continuity_settings VALUES (1,?,?,?,?,?,?,?)",
                       (RULES_VERSION, changed["generation"], content["generation"], content["policy_digest"],
                        now, revision, administrator["grant_id"]))
    result = {"enabled": True, "rules_version": RULES_VERSION,
              "activation_generation": changed["generation"], "policy_generation": changed["generation"],
              "activation": _settings(connection), "maximum_transitions": MAX_TRANSITIONS,
              "limits": list(LIMITS)}
    governance._event(connection, "review_continuity_enabled", result)
    return result


def basis(packet: Any) -> dict[str, Any]:
    """Address the entire packet projection, excluding only six global pins."""
    from . import content_routing, obligations

    obligations._shape(packet)
    _require(isinstance(packet, dict) and set(packet) == content_routing.PACKET_FIELDS
             and type(packet.get("schema_version")) is int and packet["schema_version"] == 2
             and packet.get("content_rules_version") == content_routing.RULES_VERSION
             and packet.get("obligation_rules_version") == "obligations-1"
             and packet.get("coverage_rules_version") == "coverage-1"
             and _same(packet.get("limits"), content_routing.LIMITS),
             "continuity_basis_invalid", "Continuity requires an exact schema 2 content packet")
    encoded = canonical(packet)
    _require(len(encoded.encode("utf-8")) <= MAX_PACKET_BYTES,
             "continuity_basis_invalid", "Content packet exceeds 4 MB")
    body = {key: value for key, value in packet.items() if key not in {"packet_id", "packet_digest"}}
    address = digest(body)
    _require(packet["packet_id"] == "cgp_" + address and packet["packet_digest"] == address,
             "continuity_basis_invalid", "Packet address differs from its body")
    for field in ("content_policy_generation", "governance_policy_generation"):
        _require(_integer(packet[field]), "continuity_basis_invalid", "Invalid packet generation")
    for field in ("content_policy_digest", "governance_policy_digest", "proposal_digest"):
        _require(_hash(packet[field]), "continuity_basis_invalid", "Invalid packet reference digest")
    projected = {key: value for key, value in packet.items() if key not in EXCLUDED_BASIS_FIELDS}
    projected_address = digest(projected)
    return {"basis_id": "rcb_" + projected_address, "basis_digest": projected_address, "body": projected}


def _stamp(settings: dict[str, Any], addressed: dict[str, Any]) -> dict[str, Any]:
    return {"rules_version": RULES_VERSION, "activation_generation": settings["activation_generation"],
            "basis_id": addressed["basis_id"], "basis_digest": addressed["basis_digest"]}


def stamp(connection: sqlite3.Connection, packet: dict[str, Any]) -> dict[str, Any]:
    from . import content_gate, governance

    settings = _settings(connection)
    addressed = basis(packet)
    content, policy = content_gate.current_policy(connection), governance.current_policy(connection)
    _require(packet["governance_policy_generation"] == policy["generation"]
             and packet["governance_policy_digest"] == policy["policy_digest"]
             and packet["content_policy_generation"] == content["generation"]
             and packet["content_policy_digest"] == content["policy_digest"]
             and packet["governance_policy_generation"] >= settings["activation_generation"],
             "continuity_basis_invalid", "Only the current policy packet can stamp a new decision")
    return _stamp(settings, addressed)


def record_transition(connection: sqlite3.Connection, old_content: dict[str, Any],
                      old_governance: dict[str, Any], new_content: dict[str, Any],
                      new_governance: dict[str, Any]) -> dict[str, Any]:
    """Append the edge after both policy updates in the same Store transaction."""
    from . import governance

    settings = _settings(connection)
    old_c = _policy_record(connection, "content", old_content["generation"])
    new_c = _policy_record(connection, "content", new_content["generation"])
    old_g = _policy_record(connection, "governance", old_governance["generation"])
    new_g = _policy_record(connection, "governance", new_governance["generation"])
    _require(all(_record_matches(given, recorded) for given, recorded in (
        (old_content, old_c), (new_content, new_c), (old_governance, old_g), (new_governance, new_g)))
        and old_g["generation"] >= settings["activation_generation"]
        and new_g["generation"] == old_g["generation"] + 1
        and new_c["generation"] == old_c["generation"] + 1
        and new_c["governance_policy_generation"] == new_g["generation"]
        and _same(old_g["policy"], new_g["policy"])
        and old_g["policy_digest"] == new_g["policy_digest"]
        and new_c["administrator_grant_id"] == new_g["administrator_grant_id"],
        "continuity_history_invalid", "A continuity edge must describe one exact content-only policy update")
    body = {"schema_version": 1, "rules_version": RULES_VERSION,
            "activation_generation": settings["activation_generation"], "created_at": time.time(),
            "from_content_policy_generation": old_c["generation"],
            "from_content_policy_digest": old_c["policy_digest"],
            "to_content_policy_generation": new_c["generation"],
            "to_content_policy_digest": new_c["policy_digest"],
            "from_governance_policy_generation": old_g["generation"],
            "from_governance_policy_digest": old_g["policy_digest"],
            "to_governance_policy_generation": new_g["generation"],
            "to_governance_policy_digest": new_g["policy_digest"],
            "administrator_grant_id": new_g["administrator_grant_id"]}
    address = digest(body)
    record = {**body, "transition_id": "rct_" + address, "transition_digest": address}
    connection.execute("INSERT INTO review_continuity_transitions VALUES (?,?,?,?,?)",
                       (record["transition_id"], address, old_g["generation"], new_g["generation"], canonical(body)))
    governance._event(connection, "review_continuity_transition", record)
    return record


def _transition(connection: sqlite3.Connection, generation: int,
                settings: dict[str, Any]) -> dict[str, Any] | None:
    from . import obligations

    size = connection.execute("SELECT length(CAST(body AS BLOB)),typeof(body) FROM review_continuity_transitions"
                              " WHERE from_governance_generation=?", (generation,)).fetchone()
    if size is None:
        if connection.execute("SELECT 1 FROM content_policies WHERE governance_policy_generation=? LIMIT 1",
                              (generation + 1,)).fetchone() is not None:
            raise WorkbenchError("continuity_history_invalid",
                                 "A recorded content policy update is missing its continuity edge")
        return None
    _require(size[1] == "text" and size[0] <= MAX_TRANSITION_BYTES,
             "continuity_history_invalid", "Continuity edge exceeds its bound")
    row = connection.execute("SELECT * FROM review_continuity_transitions WHERE from_governance_generation=?",
                             (generation,)).fetchone()
    body = obligations._strict_json(row["body"])
    _require(isinstance(body, dict) and set(body) == TRANSITION_FIELDS,
             "continuity_history_invalid", "Unexpected continuity edge fields")
    address = digest(body)
    _require(canonical(body) == row["body"] and row["digest"] == address
             and row["transition_id"] == "rct_" + address
             and type(body["schema_version"]) is int and body["schema_version"] == 1
             and body["rules_version"] == RULES_VERSION
             and type(body["activation_generation"]) is int
             and body["activation_generation"] == settings["activation_generation"]
             and type(body["created_at"]) in {int, float} and math.isfinite(body["created_at"])
             and isinstance(body["administrator_grant_id"], str)
             and bool(re.fullmatch(r"gvg_[0-9a-f]{32}", body["administrator_grant_id"])),
             "continuity_history_invalid", "Continuity edge address or activation differs")
    for field in ("from_content_policy_generation", "to_content_policy_generation",
                  "from_governance_policy_generation", "to_governance_policy_generation"):
        _require(_integer(body[field]), "continuity_history_invalid", "Invalid continuity edge generation")
    for field in ("from_content_policy_digest", "to_content_policy_digest",
                  "from_governance_policy_digest", "to_governance_policy_digest"):
        _require(_hash(body[field]), "continuity_history_invalid", "Invalid continuity edge digest")
    _require(body["from_governance_policy_generation"] == generation
             and row["from_governance_generation"] == generation
             and row["to_governance_generation"] == generation + 1
             and body["to_governance_policy_generation"] == generation + 1
             and body["to_content_policy_generation"] == body["from_content_policy_generation"] + 1,
             "continuity_history_invalid", "Continuity edge skips a policy generation")
    return {**body, "transition_id": row["transition_id"], "transition_digest": address}


def _scope(descriptor: dict[str, Any], write_ids: list[str]) -> dict[str, Any]:
    from . import content_routing

    routing, required = content_routing._routing(descriptor, write_ids)
    selected = [section for section in descriptor["sections"] if section["id"] in routing["selected_section_ids"]]
    return {"routing": routing, "required_write_ids": required, "sections": selected}


def _pin(packet: dict[str, Any]) -> dict[str, Any]:
    return {key: packet[key] for key in (
        "packet_id", "packet_digest", "content_policy_generation", "content_policy_digest")}


def _invalid(code: str) -> dict[str, Any]:
    return {"valid": False, "reasons": [code], "proof": None}


def match_decision(connection: sqlite3.Connection, body: dict[str, Any],
                   currentpacket: dict[str, Any]) -> dict[str, Any]:
    """Prove local continuity; governance separately checks authority and heads."""
    from . import content_gate

    try:
        settings = _settings(connection)
        current_basis = basis(currentpacket)
        if type(body.get("schema_version")) is int and body["schema_version"] in {1, 2}:
            if (_integer(body.get("policy_generation"))
                    and body["policy_generation"] < settings["activation_generation"]):
                return _invalid("continuity_decision_unproven")
            return _invalid("continuity_history_invalid")
        if type(body.get("schema_version")) is not int or body["schema_version"] != 3:
            return _invalid("continuity_history_invalid")
        recorded_stamp = body.get("review_continuity")
        if not isinstance(recorded_stamp, dict) or set(recorded_stamp) != STAMP_FIELDS:
            return _invalid("continuity_history_invalid")
        old_pin = body.get("content_packet")
        if not isinstance(old_pin, dict) or not isinstance(old_pin.get("packet_id"), str):
            return _invalid("continuity_history_invalid")
        old_packet = content_gate.load_packet(connection, old_pin["packet_id"])
        old_basis = basis(old_packet)
        if (not _same(recorded_stamp, _stamp(settings, old_basis))
                or not _same(old_pin, _pin(old_packet))
                or body.get("content_packet_digest") != old_packet["packet_digest"]
                or body.get("proposal_id") != old_packet["proposal_id"]
                or body.get("proposal_digest") != old_packet["proposal_digest"]
                or type(body.get("policy_generation")) is not int
                or body["policy_generation"] != old_packet["governance_policy_generation"]
                or body.get("policy_digest") != old_packet["governance_policy_digest"]
                or body["policy_generation"] < settings["activation_generation"]):
            return _invalid("continuity_history_invalid")
        if not _same(old_basis, current_basis):
            return _invalid("continuity_basis_changed")
        original_g = old_packet["governance_policy_generation"]
        target_g = currentpacket["governance_policy_generation"]
        if target_g < original_g:
            return _invalid("continuity_history_invalid")
        content_cache: dict[int, dict[str, Any]] = {}
        governance_cache: dict[int, dict[str, Any]] = {}

        def content_record(generation: int) -> dict[str, Any]:
            if generation not in content_cache:
                content_cache[generation] = _policy_record(connection, "content", generation)
            return content_cache[generation]

        def governance_record(generation: int) -> dict[str, Any]:
            if generation not in governance_cache:
                governance_cache[generation] = _policy_record(connection, "governance", generation)
            return governance_cache[generation]

        def checked_edge(edge: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            old_c = content_record(edge["from_content_policy_generation"])
            new_c = content_record(edge["to_content_policy_generation"])
            old_g = governance_record(edge["from_governance_policy_generation"])
            new_g = governance_record(edge["to_governance_policy_generation"])
            if (old_c["policy_digest"] != edge["from_content_policy_digest"]
                    or new_c["policy_digest"] != edge["to_content_policy_digest"]
                    or old_g["policy_digest"] != edge["from_governance_policy_digest"]
                    or new_g["policy_digest"] != edge["to_governance_policy_digest"]
                    or not _same(old_g["policy"], new_g["policy"])
                    or new_c["governance_policy_generation"] != new_g["generation"]
                    or new_c["administrator_grant_id"] != edge["administrator_grant_id"]
                    or new_g["administrator_grant_id"] != edge["administrator_grant_id"]):
                raise WorkbenchError("continuity_history_invalid", "Continuity edge historical references differ")
            return old_c, new_c

        source_content = content_record(old_packet["content_policy_generation"])
        target_content = content_record(currentpacket["content_policy_generation"])
        source_governance = governance_record(original_g)
        target_governance = governance_record(target_g)
        if (source_content["policy_digest"] != old_packet["content_policy_digest"]
                or target_content["policy_digest"] != currentpacket["content_policy_digest"]
                or source_governance["policy_digest"] != old_packet["governance_policy_digest"]
                or target_governance["policy_digest"] != currentpacket["governance_policy_digest"]):
            return _invalid("continuity_history_invalid")
        original_scope = _scope(source_content["policy"], old_packet["write_ids"])
        if not _same(original_scope, _scope(target_content["policy"], currentpacket["write_ids"])):
            return _invalid("continuity_scope_changed")
        if target_g - original_g > MAX_TRANSITIONS:
            # Examine only the latest bounded suffix. A verified absence is a
            # direct governance-policy barrier under this API; a malformed
            # present edge is uncertainty and must never masquerade as reset.
            cursor_c = target_content["generation"]
            cursor_digest = target_content["policy_digest"]
            for generation in range(target_g - 1, target_g - MAX_TRANSITIONS - 1, -1):
                edge = _transition(connection, generation, settings)
                if edge is None:
                    governance_record(generation)
                    governance_record(generation + 1)
                    return _invalid("continuity_path_gap")
                old_c, new_c = checked_edge(edge)
                if (edge["to_content_policy_generation"] != cursor_c
                        or edge["to_content_policy_digest"] != cursor_digest):
                    return _invalid("continuity_history_invalid")
                cursor_c, cursor_digest = old_c["generation"], old_c["policy_digest"]
            return _invalid("continuity_history_limit")
        cursor_c = source_content["generation"]
        cursor_digest = source_content["policy_digest"]
        transitions = []
        for generation in range(original_g, target_g):
            edge = _transition(connection, generation, settings)
            if edge is None:
                return _invalid("continuity_path_gap")
            if (edge["from_content_policy_generation"] != cursor_c
                    or edge["from_content_policy_digest"] != cursor_digest):
                return _invalid("continuity_history_invalid")
            old_c, new_c = checked_edge(edge)
            if (not _same(original_scope, _scope(old_c["policy"], currentpacket["write_ids"]))
                    or not _same(original_scope, _scope(new_c["policy"], currentpacket["write_ids"]))):
                return _invalid("continuity_scope_changed")
            transitions.append({"transition_id": edge["transition_id"], "transition_digest": edge["transition_digest"]})
            cursor_c, cursor_digest = new_c["generation"], new_c["policy_digest"]
        if (cursor_c != target_content["generation"] or cursor_digest != target_content["policy_digest"]
                or not _same(source_governance["policy"], target_governance["policy"])):
            return _invalid("continuity_history_invalid")
        proof = {"rules_version": RULES_VERSION, "activation_generation": settings["activation_generation"],
                 "decision_id": body["decision_id"], "mode": "preserved_catalog" if transitions else "exact_packet",
                 "basis_id": current_basis["basis_id"], "basis_digest": current_basis["basis_digest"],
                 "scope_digest": digest(original_scope), "old_packet": _pin(old_packet),
                 "current_packet": _pin(currentpacket), "from_governance_generation": original_g,
                 "to_governance_generation": target_g, "transitions": transitions}
        proof["chain_digest"] = digest(proof)
        return {"valid": True, "reasons": [], "proof": proof}
    except (WorkbenchError, KeyError, TypeError, ValueError, AttributeError, OverflowError,
            RecursionError, UnicodeError, sqlite3.Error):
        return _invalid("continuity_history_invalid")


def status(connection: sqlite3.Connection, proposal_id: str | None = None) -> dict[str, Any]:
    from . import content_gate

    result: dict[str, Any] = {"enabled": enabled(connection), "rules_version": RULES_VERSION,
                              "maximum_transitions": MAX_TRANSITIONS, "limits": list(LIMITS),
                              "trusted_database_owner_can_bypass": True, "human_identity_authenticated": False}
    if not result["enabled"]:
        return result
    settings = _settings(connection)
    count = connection.execute("SELECT COUNT(*) FROM review_continuity_transitions").fetchone()[0]
    rows = connection.execute("SELECT from_governance_generation FROM review_continuity_transitions"
                              " ORDER BY from_governance_generation DESC LIMIT ?", (MAX_STATUS_TRANSITIONS,))
    result.update({"activation": settings, "transition_count": count,
                   "transitions": [_transition(connection, row[0], settings) for row in rows],
                   "transitions_truncated": count > MAX_STATUS_TRANSITIONS})
    if proposal_id is not None:
        proposal = Store._find_proposal(connection, proposal_id)
        packet = content_gate.build_packet(connection, proposal)
        result.update({"proposal_id": proposal_id, "current_packet": _pin(packet),
                       "current_basis": stamp(connection, packet), "advisory_only": True})
    return result
