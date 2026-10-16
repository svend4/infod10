"""Append-only local editorial history, separate from runtime acceptance.

Actor kinds are declarations by a trusted local caller, not authentication.
No journal outcome publishes content or bypasses human editorial judgment.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from typing import Any, Iterator

from .semantic import SEMANTIC_RULES_VERSION, compare_statements
from .store import WorkbenchError, canonical, digest


APPLICATION_ID = 0x41574A31
SCHEMA_VERSION = 1
EDITORIAL_POLICY = {
    "version": "editorial-1", "agent_agreement_min": 2,
    "requires_human_review": True, "auto_accept": False,
    "latest_decision_per_reviewer": True, "actor_identity": "self_declared_local",
}
ACTOR_ID = re.compile(r"[a-zA-Z][a-zA-Z0-9_.-]{0,99}\Z")
OPERATION_ID = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9_.:-]{0,119}\Z")
KINDS = {"agent", "human"}
VERDICTS = {"approve", "request_changes", "abstain"}
SCHEMA = [
    "CREATE TABLE actors (id TEXT PRIMARY KEY, kind TEXT NOT NULL)",
    "CREATE TABLE packets (id TEXT PRIMARY KEY, body TEXT NOT NULL)",
    "CREATE TABLE decisions (id TEXT PRIMARY KEY, packet_id TEXT NOT NULL REFERENCES packets(id), reviewer TEXT NOT NULL REFERENCES actors(id), operation_id TEXT NOT NULL UNIQUE, request_digest TEXT NOT NULL, body TEXT NOT NULL)",
    "CREATE INDEX reviewer_history ON decisions(packet_id,reviewer)",
    "CREATE TABLE events (seq INTEGER PRIMARY KEY, body TEXT NOT NULL)",
]


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise WorkbenchError(code, message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _analysis(pair: Any) -> dict[str, Any]:
    _require(isinstance(pair, dict) and set(pair) == {"before", "after", "sources"}, "invalid_editorial_input", "Pair needs exactly before, after and sources")
    analysis = compare_statements(pair["before"], pair["after"], pair["sources"])
    _require(len(canonical(pair).encode("utf-8")) <= 5000000, "invalid_editorial_input", "Packet pair limit is 5 MB")
    return analysis


class EditorialJournal:
    """Immutable packets and decisions in a dedicated, namespaced SQLite file."""

    def __init__(self, path: str | Path, create: bool = True):
        self.path = Path(path)
        if not create:
            _require(self.path.is_file(), "journal_missing", "Editorial database does not exist")
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect(existing=not create)
        try:
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if application_id == APPLICATION_ID:
                _require(version == SCHEMA_VERSION, "journal_schema", "Unsupported editorial database schema")
                return
            tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
            _require(create and application_id == 0 and version == 0 and not tables, "foreign_database", "Refusing to initialize an existing foreign database")
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("BEGIN IMMEDIATE")
            # Recheck after obtaining the write lock: another initializer may
            # have completed while this connection was waiting.
            application_id = connection.execute("PRAGMA application_id").fetchone()[0]
            version = connection.execute("PRAGMA user_version").fetchone()[0]
            if application_id == APPLICATION_ID:
                _require(version == SCHEMA_VERSION, "journal_schema", "Unsupported editorial database schema")
            else:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
                _require(application_id == 0 and version == 0 and not tables, "foreign_database", "Another application initialized this database")
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute(f"PRAGMA application_id={APPLICATION_ID}")
                connection.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise WorkbenchError("journal_database_error", str(error)) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _connect(self, existing: bool = True) -> sqlite3.Connection:
        connection = None
        try:
            if existing:
                uri = self.path.absolute().as_uri() + "?mode=rw"
                connection = sqlite3.connect(uri, uri=True, timeout=30, isolation_level=None)
            else:
                connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except sqlite3.Error as error:
            if connection is not None:
                connection.close()
            raise WorkbenchError("journal_database_error", str(error)) from error

    @contextmanager
    def _transaction(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            yield connection
            connection.commit()
        except sqlite3.Error as error:
            connection.rollback()
            raise WorkbenchError("journal_database_error", str(error)) from error
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _event(connection: sqlite3.Connection, kind: str, payload: dict[str, Any]) -> None:
        last = connection.execute("SELECT seq,body FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        sequence = last["seq"] + 1 if last else 1
        previous = json.loads(last["body"])["event_hash"] if last else None
        event = {"seq": sequence, "type": kind, "payload": payload, "previous_hash": previous, "recorded_at": _now()}
        event["event_hash"] = digest(event)
        connection.execute("INSERT INTO events VALUES (?,?)", (sequence, canonical(event)))

    @staticmethod
    def _actor(connection: sqlite3.Connection, identifier: str, kind: str) -> None:
        _require(isinstance(identifier, str) and bool(ACTOR_ID.fullmatch(identifier)), "invalid_actor", "Use a bounded actor ID containing letters, numbers, dots, underscores or hyphens")
        _require(isinstance(kind, str) and kind in KINDS, "invalid_actor", "Actor kind must be agent or human")
        old = connection.execute("SELECT kind FROM actors WHERE id=?", (identifier,)).fetchone()
        if old:
            _require(old[0] == kind, "actor_kind_conflict", "An existing actor ID cannot change its declared kind")
            return
        connection.execute("INSERT INTO actors VALUES (?,?)", (identifier, kind))
        EditorialJournal._event(connection, "actor_registered", {"id": identifier, "kind": kind})

    @staticmethod
    def _get(connection: sqlite3.Connection, packet_id: str) -> dict[str, Any]:
        row = connection.execute("SELECT body FROM packets WHERE id=?", (packet_id,)).fetchone()
        _require(row is not None, "unknown_packet", "Unknown editorial packet")
        return json.loads(row["body"])

    def create_packet(self, pair: dict[str, Any], proposer: str = "author", proposer_kind: str = "agent") -> dict[str, Any]:
        # Capture caller-owned mutable objects before computing and saving the
        # packet; metadata and content address always describe this same copy.
        pair = json.loads(canonical(pair))
        analysis = _analysis(pair)
        payload = {
            "schema_version": 1, "proposer": {"id": proposer, "kind": proposer_kind},
            "pair": pair, "analysis": analysis, "input_digest": analysis["input_digest"],
            "analysis_digest": digest(analysis), "rules_version": analysis["rules_version"],
            "policy": dict(EDITORIAL_POLICY), "policy_digest": digest(EDITORIAL_POLICY),
        }
        packet_digest = digest(payload)
        packet_id = "pkt_" + packet_digest
        packet = {**payload, "packet_id": packet_id, "packet_digest": packet_digest, "created_at": _now()}
        with self._transaction(write=True) as connection:
            self._actor(connection, proposer, proposer_kind)
            existing = connection.execute("SELECT body FROM packets WHERE id=?", (packet_id,)).fetchone()
            if existing:
                return json.loads(existing["body"])
            connection.execute("INSERT INTO packets VALUES (?,?)", (packet_id, canonical(packet)))
            self._event(connection, "packet_created", packet)
        return packet

    def get_packet(self, packet_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            return self._get(connection, packet_id)

    @staticmethod
    def _freshness(packet: dict[str, Any], current_pair: dict[str, Any] | None) -> tuple[str, dict[str, Any] | None]:
        if packet["rules_version"] != SEMANTIC_RULES_VERSION:
            return "stale_rules", None
        if packet["policy_digest"] != digest(EDITORIAL_POLICY):
            return "stale_policy", None
        stored_analysis = _analysis(packet["pair"])
        if digest(stored_analysis) != packet["analysis_digest"]:
            return "stale_analysis", None
        if current_pair is None:
            return "input_not_checked", None
        current_analysis = _analysis(current_pair)
        if current_analysis["input_digest"] != packet["input_digest"]:
            return "stale_input", current_analysis
        if digest(current_analysis) != packet["analysis_digest"]:
            return "stale_analysis", current_analysis
        return "current", current_analysis

    def record_decision(self, packet_id: str, reviewer: str, actor_kind: str, verdict: str, rationale: str, operation_id: str, expected_packet_digest: str, expected_previous_decision: str | None = None) -> dict[str, Any]:
        _require(isinstance(verdict, str) and verdict in VERDICTS, "invalid_decision", "Unknown editorial verdict")
        _require(isinstance(rationale, str) and bool(rationale.strip()) and len(rationale) <= 20000, "invalid_decision", "A bounded, nonempty rationale is required")
        _require(isinstance(operation_id, str) and bool(OPERATION_ID.fullmatch(operation_id)), "invalid_decision", "A bounded operation ID is required")
        request = {
            "schema_version": 1, "packet_id": packet_id, "packet_digest": expected_packet_digest,
            "reviewer": reviewer, "actor_kind": actor_kind, "verdict": verdict,
            "rationale": rationale, "operation_id": operation_id,
            "supersedes": expected_previous_decision,
        }
        request_digest = digest(request)
        with self._transaction(write=True) as connection:
            previous = connection.execute("SELECT body,request_digest FROM decisions WHERE operation_id=?", (operation_id,)).fetchone()
            if previous:
                _require(previous["request_digest"] == request_digest, "idempotency_conflict", "Operation ID already refers to different decision content")
                return {**json.loads(previous["body"]), "replayed": True}
            packet = self._get(connection, packet_id)
            _require(packet["packet_digest"] == expected_packet_digest, "packet_mismatch", "Expected packet digest differs from the stored packet")
            _require(reviewer != packet["proposer"]["id"], "self_review", "The proposer cannot record their own review under the same actor ID")
            freshness, _ = self._freshness(packet, packet["pair"])
            _require(freshness == "current", freshness, "Create a fresh packet for current rules and policy")
            latest = connection.execute("SELECT id FROM decisions WHERE packet_id=? AND reviewer=? ORDER BY rowid DESC LIMIT 1", (packet_id, reviewer)).fetchone()
            latest_id = latest[0] if latest else None
            _require(latest_id == expected_previous_decision, "decision_conflict", "Latest decision changed; supply its ID as supersedes")
            self._actor(connection, reviewer, actor_kind)
            decision = {**request, "decision_id": "dec_" + request_digest, "request_digest": request_digest, "recorded_at": _now()}
            connection.execute("INSERT INTO decisions VALUES (?,?,?,?,?,?)", (decision["decision_id"], packet_id, reviewer, operation_id, request_digest, canonical(decision)))
            self._event(connection, "decision_recorded", decision)
            return {**decision, "replayed": False}

    def history(self, packet_id: str) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            self._get(connection, packet_id)
            return [json.loads(row["body"]) for row in connection.execute("SELECT body FROM decisions WHERE packet_id=? ORDER BY rowid", (packet_id,))]

    def assess(self, packet_id: str, current_pair: dict[str, Any] | None = None) -> dict[str, Any]:
        if current_pair is not None:
            current_pair = json.loads(canonical(current_pair))
        with self._transaction() as connection:
            packet = self._get(connection, packet_id)
            decisions = [json.loads(row["body"]) for row in connection.execute("SELECT body FROM decisions WHERE packet_id=? ORDER BY rowid", (packet_id,))]
        # Long comparisons use captured immutable packet/history, without a
        # write lock and without rereading a potentially changed input file.
        return _assess_records(packet, decisions, current_pair)

    def export_snapshot(self) -> dict[str, Any]:
        with self._transaction() as connection:
            snapshot = {
                "schema_version": 1,
                "current_policy": dict(EDITORIAL_POLICY), "current_rules_version": SEMANTIC_RULES_VERSION,
                "actors": [dict(row) for row in connection.execute("SELECT id,kind FROM actors ORDER BY id")],
                "packets": [json.loads(row["body"]) for row in connection.execute("SELECT body FROM packets ORDER BY rowid")],
                "decisions": [json.loads(row["body"]) for row in connection.execute("SELECT body FROM decisions ORDER BY rowid")],
                "events": [json.loads(row["body"]) for row in connection.execute("SELECT body FROM events ORDER BY seq")],
                "requires_human_review": True, "auto_accept": False,
                "integrity_scope": "Internal consistency only; hashes do not authenticate authorship, independence, completeness of an externally truncated history, or truth",
            }
        snapshot["export_digest"] = digest(snapshot)
        verify_journal_export(snapshot)
        return snapshot


def verify_journal_export(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Check content addresses, full event payloads and predecessor links.

    This is an integrity check on product data, not a signature, trusted
    checkpoint, factual assessment, or application test runner.
    """
    def check(condition: bool, message: str) -> None:
        _require(condition, "journal_integrity", message)

    try:
        check(type(snapshot["schema_version"]) is int and snapshot["schema_version"] == 1, "Unsupported journal export schema")
        payload = {key: value for key, value in snapshot.items() if key != "export_digest"}
        check(digest(payload) == snapshot["export_digest"], "Export digest mismatch")
        actors = {actor["id"]: actor for actor in snapshot["actors"]}
        packets = {packet["packet_id"]: packet for packet in snapshot["packets"]}
        decisions = {decision["decision_id"]: decision for decision in snapshot["decisions"]}
        check(len(actors) == len(snapshot["actors"]) and len(packets) == len(snapshot["packets"]) and len(decisions) == len(snapshot["decisions"]), "Duplicate record identity")
        for actor in actors.values():
            check(set(actor) == {"id", "kind"} and actor["kind"] in KINDS and bool(ACTOR_ID.fullmatch(actor["id"])), "Invalid actor metadata")
        for packet in packets.values():
            packet_payload = {key: value for key, value in packet.items() if key not in {"packet_id", "packet_digest", "created_at"}}
            check(packet["packet_digest"] == digest(packet_payload) and packet["packet_id"] == "pkt_" + packet["packet_digest"], "Packet content address mismatch")
            check(packet["input_digest"] == digest(packet["pair"]) == packet["analysis"]["input_digest"], "Packet input mismatch")
            check(packet["analysis_digest"] == digest(packet["analysis"]) and packet["rules_version"] == packet["analysis"]["rules_version"], "Stored analysis mismatch")
            check(packet["policy_digest"] == digest(packet["policy"]), "Stored policy mismatch")
            check(actors.get(packet["proposer"]["id"]) == packet["proposer"], "Proposer registry mismatch")
        seen_operations = set()
        latest = {}
        for decision in snapshot["decisions"]:
            request = {key: value for key, value in decision.items() if key not in {"decision_id", "request_digest", "recorded_at"}}
            check(decision["request_digest"] == digest(request) and decision["decision_id"] == "dec_" + decision["request_digest"], "Decision content address mismatch")
            check(decision["operation_id"] not in seen_operations, "Duplicate operation ID")
            seen_operations.add(decision["operation_id"])
            packet = packets.get(decision["packet_id"])
            check(packet is not None and packet["packet_digest"] == decision["packet_digest"], "Decision packet binding mismatch")
            check(actors.get(decision["reviewer"]) == {"id": decision["reviewer"], "kind": decision["actor_kind"]}, "Reviewer registry mismatch")
            check(packet["proposer"]["id"] != decision["reviewer"] and decision["verdict"] in VERDICTS, "Invalid reviewer or verdict")
            key = (decision["packet_id"], decision["reviewer"])
            check(decision["supersedes"] == latest.get(key), "Decision predecessor mismatch")
            latest[key] = decision["decision_id"]
        seen_actors, seen_packets, seen_decisions = set(), set(), set()
        event_decision_order = []
        previous = None
        for sequence, event in enumerate(snapshot["events"], 1):
            check(event["seq"] == sequence and event["previous_hash"] == previous, "Event sequence or predecessor mismatch")
            check(event["event_hash"] == digest({key: value for key, value in event.items() if key != "event_hash"}), "Event hash mismatch")
            previous = event["event_hash"]
            record = event["payload"]
            if event["type"] == "actor_registered":
                check(digest(record) == digest(actors.get(record["id"])) and record["id"] not in seen_actors, "Actor event mismatch")
                seen_actors.add(record["id"])
            elif event["type"] == "packet_created":
                check(digest(record) == digest(packets.get(record["packet_id"])) and record["packet_id"] not in seen_packets and record["proposer"]["id"] in seen_actors, "Packet event mismatch")
                seen_packets.add(record["packet_id"])
            elif event["type"] == "decision_recorded":
                check(digest(record) == digest(decisions.get(record["decision_id"])) and record["decision_id"] not in seen_decisions and record["reviewer"] in seen_actors and record["packet_id"] in seen_packets, "Decision event mismatch")
                seen_decisions.add(record["decision_id"])
                event_decision_order.append(record["decision_id"])
            else:
                check(False, "Unknown journal event type")
        check(seen_actors == set(actors) and seen_packets == set(packets) and seen_decisions == set(decisions), "Journal records and events differ")
        check(event_decision_order == [item["decision_id"] for item in snapshot["decisions"]], "Decision order differs from event order")
        return {"consistent": True, "packets": len(packets), "decisions": len(decisions), "events": len(snapshot["events"]), "head": previous, "authenticated": False}
    except WorkbenchError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError, RecursionError) as error:
        raise WorkbenchError("journal_integrity", "Malformed journal export: " + str(error)) from error


def _assess_records(packet: dict[str, Any], decisions: list[dict[str, Any]], current_pair: dict[str, Any] | None) -> dict[str, Any]:
    """Assess already captured records; perform no database reads."""
    freshness, analysis = EditorialJournal._freshness(packet, current_pair)
    latest_by_reviewer = {decision["reviewer"]: decision for decision in decisions}
    latest = [latest_by_reviewer[key] for key in sorted(latest_by_reviewer)]
    approvals = [decision for decision in latest if decision["verdict"] == "approve"]
    objections = [decision for decision in latest if decision["verdict"] == "request_changes"]
    agent_approvals = [decision for decision in approvals if decision["actor_kind"] == "agent"]
    if approvals and objections:
        review_state = "disagreement"
    elif objections:
        review_state = "changes_requested"
    elif len(agent_approvals) >= EDITORIAL_POLICY["agent_agreement_min"]:
        review_state = "agent_agreement"
    elif approvals:
        review_state = "review_recorded"
    elif latest:
        review_state = "abstained"
    else:
        review_state = "awaiting_reviews"
    human_recorded = any(decision["actor_kind"] == "human" for decision in latest)
    blockers = []
    if freshness != "current":
        blockers.append(freshness)
    if not human_recorded:
        blockers.append("human_review_not_recorded")
    if objections:
        blockers.append("unresolved_changes_requested")
    if (analysis or packet["analysis"])["relation"] == "undetermined":
        blockers.append("semantic_context_incomplete")
    return {
        "packet_id": packet["packet_id"], "packet_digest": packet["packet_digest"],
        "input_digest": packet["input_digest"],
        "current_input_digest": analysis["input_digest"] if analysis is not None else None,
        "freshness": freshness, "is_current": None if freshness == "input_not_checked" else freshness == "current",
        "current_input_provided": current_pair is not None,
        "current_input_checked": current_pair is not None and analysis is not None,
        "review_state": review_state, "latest_decisions": latest,
        "current_basis_decisions": latest if freshness == "current" else [],
        "history_count": len(decisions), "reviewer_count": len(latest),
        "human_review_recorded": human_recorded, "requires_human_review": True,
        "current_human_review_recorded": human_recorded and freshness == "current",
        "auto_accept": False, "blockers": blockers,
        "counts": {"approve": len(approvals), "request_changes": len(objections), "abstain": sum(decision["verdict"] == "abstain" for decision in latest)},
        "limits": [
            "Actor identities and kinds are self-declared local metadata, not authentication or evidence of independence.",
            "Review state describes decisions about the stored packet; stale decisions do not apply to different current input.",
            "No state records factual truth or grants Store.accept/publication rights.",
            "Human-review requirement remains true even when a caller records an actor labelled human.",
        ],
    }


def assess_export(snapshot: dict[str, Any], packet_id: str, current_pair: dict[str, Any] | None = None) -> dict[str, Any]:
    """Assess one packet from a self-contained journal observation.

    Both the packet and latest decision heads come from the same captured
    export. Its hashes establish internal consistency, not trusted identity.
    """
    snapshot = json.loads(canonical(snapshot))
    pair = json.loads(canonical(current_pair)) if current_pair is not None else None
    verify_journal_export(snapshot)
    packet = next((item for item in snapshot["packets"] if item["packet_id"] == packet_id), None)
    _require(packet is not None, "unknown_packet", packet_id)
    decisions = [item for item in snapshot["decisions"] if item["packet_id"] == packet_id]
    return _assess_records(packet, decisions, pair)
