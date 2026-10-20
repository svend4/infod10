"""Transactional local workbench. Agent output is data and is never executed.

SQLite is the canonical runtime store. This module does not update Git refs,
authenticate remote callers, call models, or decide the truth of natural text.
"""

from __future__ import annotations

from contextlib import contextmanager
import ast
import errno
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import tempfile
import time
from typing import Any, Iterator


RULES_VERSION = "mvp-1"
QUERY_SCOPES = ("api:v1", "platforms:api-v1")
ROLE_KINDS = {
    "analyst": {"claim"},
    "doc_author": {"document"},
    "code_author": {"code"},
    "integrator": {"document", "code"},
}
CLAIM_ID = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
PLACEHOLDER = re.compile(r"\{\{([a-z][a-z0-9_.-]{0,100})\}\}")
HASH = re.compile(r"[0-9a-f]{64}\Z")
POLICY_KEYS = ("api.timeout_seconds", "api.max_attempts", "api.budget_seconds")


class WorkbenchError(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def reason(code: str, **details: Any) -> dict[str, Any]:
    return {"code": code, **details}


def safe_file_id(value: str, kind: str) -> bool:
    prefix = "docs/" if kind == "document" else "code/"
    path = PurePosixPath(value)
    return (
        value.startswith(prefix)
        and len(value) <= 200
        and not path.is_absolute()
        and str(path) == value
        and all(part not in {"", ".", ".."} for part in value.split("/"))
        and not any(ord(char) < 32 or char in "\\:" for char in value)
        and (value.endswith(".md") if kind == "document" else value.endswith(".py"))
    )


SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
INSERT OR IGNORE INTO meta VALUES ('revision', '0');
CREATE TABLE IF NOT EXISTS sources (
    id TEXT PRIMARY KEY, text TEXT NOT NULL, digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, scope TEXT,
    body TEXT NOT NULL, digest TEXT NOT NULL, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY, role TEXT NOT NULL, payload TEXT NOT NULL,
    dependencies TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'pending',
    token INTEGER NOT NULL DEFAULT 0, worker TEXT, lease_until REAL,
    result_id TEXT
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES tasks(id),
    author TEXT NOT NULL, token INTEGER NOT NULL, payload TEXT NOT NULL,
    digest TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'proposed',
    accepted_revision INTEGER
);
CREATE TABLE IF NOT EXISTS proposal_components (
    composite_id TEXT NOT NULL REFERENCES proposals(id),
    position INTEGER NOT NULL CHECK (position >= 1),
    source_id TEXT NOT NULL REFERENCES proposals(id), source_digest TEXT NOT NULL,
    writes TEXT NOT NULL,
    PRIMARY KEY (composite_id, position), UNIQUE (composite_id, source_id)
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT, proposal_id TEXT NOT NULL REFERENCES proposals(id),
    reviewer TEXT NOT NULL, proposal_digest TEXT NOT NULL,
    rules_version TEXT NOT NULL, approved INTEGER NOT NULL, report TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS revisions (
    revision INTEGER PRIMARY KEY, proposal_id TEXT, state TEXT NOT NULL, state_digest TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, type TEXT NOT NULL, data TEXT NOT NULL
);
"""


def _execute_schema(connection: sqlite3.Connection, script: str) -> None:
    """Execute only trusted application SQL without an implicit COMMIT."""
    pieces: list[str] = []
    for character in script:
        pieces.append(character)
        if character == ";" and sqlite3.complete_statement("".join(pieces)):
            connection.execute("".join(pieces))
            pieces.clear()
    if "".join(pieces).strip():
        raise RuntimeError("Application schema contains an incomplete SQL statement")


class Store:
    """Use one connection per operation; compatible with local threads/processes.

    Roles enforce a cooperative workflow, not a network security boundary. An
    actor with access to the database file can bypass these Python methods.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._inspection_only = False
        self._source_fenced = False
        from . import source_fence, store_quarantine
        marker = store_quarantine.probe(self.path)
        fence = source_fence.probe(self.path)
        self._inspection_only = marker is not None
        self._source_fenced = fence is not None
        if self._inspection_only or self._source_fenced:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = self._connect()
        try:
            if self._record_access_markers(connection):
                return
            fresh = connection.execute("SELECT 1 FROM main.sqlite_schema LIMIT 1").fetchone() is None
            if fresh:
                # Changing journal mode requires autocommit. EXCLUSIVE locking
                # mode retains the lock across that boundary until close, so
                # another initializer/seal cannot win between this recheck and
                # the fresh database's WAL setup.
                connection.execute("PRAGMA locking_mode=EXCLUSIVE")
                connection.execute("BEGIN EXCLUSIVE")
                if self._record_access_markers(connection):
                    connection.rollback()
                    return
                fresh = connection.execute("SELECT 1 FROM main.sqlite_schema LIMIT 1").fetchone() is None
                connection.commit()
                if fresh:
                    connection.execute("PRAGMA journal_mode=WAL")
            # Existing initialized stores retain their journal mode. The
            # reservation also serializes all schema work with source sealing.
            connection.execute("BEGIN IMMEDIATE")
            if self._record_access_markers(connection):
                connection.rollback()
                return
            _execute_schema(connection, SCHEMA)
            from .governance import SCHEMA as governance_schema
            _execute_schema(connection, governance_schema)
            from .authorship import SCHEMA as authorship_schema
            _execute_schema(connection, authorship_schema)
            from .content_gate import SCHEMA as content_schema
            _execute_schema(connection, content_schema)
            from .review_continuity import SCHEMA as continuity_schema
            _execute_schema(connection, continuity_schema)
            from .review_history_audit import SCHEMA as history_audit_schema
            _execute_schema(connection, history_audit_schema)
            from .review_history_recovery import SCHEMA as history_recovery_schema
            _execute_schema(connection, history_recovery_schema)
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _record_access_markers(self, connection: sqlite3.Connection) -> bool:
        """Check the actual database before any initialization or write body."""
        from . import source_fence, store_quarantine
        marker = store_quarantine.quarantine_info(connection)
        fence = source_fence.fence_info(connection)
        if self._inspection_only and marker is None:
            raise WorkbenchError("store_snapshot_invalid", "An inspection-only Store lost its snapshot marker")
        if self._source_fenced and fence is None:
            raise WorkbenchError("store_source_fence_invalid", "A fenced Store lost its source marker")
        self._inspection_only = marker is not None
        self._source_fenced = fence is not None
        return self._inspection_only or self._source_fenced

    def _connect(self) -> sqlite3.Connection:
        from . import source_fence, store_quarantine
        marker = store_quarantine.probe(self.path)
        fence = source_fence.probe(self.path)
        if self._inspection_only and marker is None:
            raise WorkbenchError("store_snapshot_invalid", "An inspection-only Store lost its snapshot marker")
        if self._source_fenced and fence is None:
            raise WorkbenchError("store_source_fence_invalid", "A fenced Store lost its source marker")
        self._inspection_only = marker is not None
        self._source_fenced = fence is not None
        if self._inspection_only or self._source_fenced:
            return store_quarantine.readonly_connection(self.path)
        connection = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        try:
            connection.row_factory = sqlite3.Row
            if self._record_access_markers(connection):
                connection.close()
                return store_quarantine.readonly_connection(self.path)
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=30000")
            connection.execute("PRAGMA synchronous=FULL")
            return connection
        except BaseException:
            connection.close()
            raise

    @contextmanager
    def _transaction(self, write: bool = False) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            readonly = bool(connection.execute("PRAGMA query_only").fetchone()[0])
            connection.execute("BEGIN IMMEDIATE" if write and not readonly else "BEGIN")
            self._record_access_markers(connection)
            if write and self._inspection_only:
                # This check runs before the action body, including completed
                # operation replays, and before a write BEGIN on a read-only DB.
                raise WorkbenchError("store_snapshot_quarantined", "Snapshot copies permit inspection only")
            if write and self._source_fenced:
                raise WorkbenchError("store_source_fenced", "The source has permanently stopped Store writes")
            if write and readonly:
                raise WorkbenchError("store_source_fence_invalid", "A read-only Store has no valid access marker")
            yield connection
            connection.commit()
        except BaseException:
            connection.rollback()
            raise
        finally:
            connection.close()

    def snapshot_access(self) -> dict[str, Any]:
        """Describe this path's current access boundary without authorizing it."""
        from . import source_fence, store_quarantine
        with self._transaction() as connection:
            marker = store_quarantine.quarantine_info(connection)
            fence = source_fence.fence_info(connection)
            quarantined = marker is not None
            fenced = fence is not None
            inspection_only = quarantined or fenced
            result = {"rules_version": store_quarantine.RULES_VERSION,
                      "quarantined": quarantined, "source_fenced": fenced,
                      "inspection_only": inspection_only,
                      "domain_writes_allowed": not inspection_only,
                      "historical_authorization_only": quarantined,
                      "live_authorization_allowed": not inspection_only,
                      "trusted_database_owner_can_bypass": True}
            if marker is not None:
                result.update({key: marker[key] for key in (
                    "backup_id", "quarantine_id", "source_revision", "event_high_water", "created_at")})
            if fence is not None:
                result["source_fence"] = fence
            return result

    def create_backup(self, destination: str | Path, *, credential: str) -> dict[str, Any]:
        """Create a private snapshot bundle; it does not activate another Store."""
        access = self.snapshot_access()
        if access["quarantined"]:
            raise WorkbenchError("store_snapshot_quarantined", "Snapshot copies cannot authorize a new backup")
        if access["source_fenced"]:
            raise WorkbenchError("store_source_fenced", "A fenced source cannot authorize a new backup")
        from . import store_backup
        return store_backup.create_backup(self.path, destination, credential=credential)

    @staticmethod
    def _event(connection: sqlite3.Connection, event: str, data: Any) -> None:
        connection.execute("INSERT INTO events(type,data) VALUES (?,?)", (event, canonical(data)))

    @staticmethod
    def _revision(connection: sqlite3.Connection) -> int:
        return int(connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()[0])

    @staticmethod
    def _artifacts(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
        return {row["id"]: json.loads(row["body"]) for row in connection.execute("SELECT * FROM artifacts ORDER BY id")}

    @staticmethod
    def _query_digest(artifacts: dict[str, Any], scope: str) -> str:
        return digest({key: digest(body) for key, body in sorted(artifacts.items()) if body.get("scope") == scope})

    @staticmethod
    def empty_query_digest() -> str:
        return digest({})

    def add_source(self, source_id: str, text: str) -> str:
        if not isinstance(source_id, str) or not CLAIM_ID.fullmatch(source_id) or not isinstance(text, str) or not text:
            raise WorkbenchError("invalid_source", "Source needs a safe ID and nonempty text")
        source_digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        with self._transaction(write=True) as connection:
            current = connection.execute("SELECT digest FROM sources WHERE id=?", (source_id,)).fetchone()
            if current:
                if current[0] != source_digest:
                    raise WorkbenchError("immutable_source", "A source ID cannot be replaced")
                return source_digest
            connection.execute("INSERT INTO sources VALUES (?,?,?)", (source_id, text, source_digest))
            self._event(connection, "source_added", {"id": source_id, "digest": source_digest})
        return source_digest

    def _artifact_errors(self, body: Any, connection: sqlite3.Connection) -> list[dict[str, Any]]:
        if not isinstance(body, dict):
            return [reason("invalid_artifact", detail="Artifact must be an object")]
        identifier, kind = body.get("id"), body.get("kind")
        if not isinstance(identifier, str) or kind not in {"claim", "document", "code"}:
            return [reason("invalid_artifact", detail="Invalid ID or kind")]
        errors = []
        if kind == "claim":
            required = {"id", "kind", "scope", "value", "unit", "evidence"}
            if set(body) != required or not CLAIM_ID.fullmatch(identifier) or body.get("scope") not in QUERY_SCOPES:
                return [reason("invalid_claim", id=identifier)]
            value = body["value"]
            if not isinstance(value, (str, int, float, bool)) or isinstance(value, float) and not math.isfinite(value):
                errors.append(reason("invalid_value", id=identifier))
            if not isinstance(body["unit"], str) or len(body["unit"]) > 40:
                errors.append(reason("invalid_unit", id=identifier))
            evidence = body["evidence"]
            if not isinstance(evidence, dict) or set(evidence) != {"source_id", "quote"}:
                return errors + [reason("missing_evidence", id=identifier)]
            source_id, quote = evidence.get("source_id"), evidence.get("quote")
            if not isinstance(source_id, str) or not isinstance(quote, str) or not quote.strip():
                return errors + [reason("invalid_evidence", id=identifier)]
            source = connection.execute("SELECT text FROM sources WHERE id=?", (source_id,)).fetchone()
            if not source or quote not in source[0]:
                errors.append(reason("unmatched_quote", id=identifier, source_id=source_id))
        else:
            if set(body) != {"id", "kind", "template", "bindings"} or not safe_file_id(identifier, kind):
                return [reason("unsafe_or_invalid_file", id=identifier)]
            template, bindings = body["template"], body["bindings"]
            if not isinstance(template, str) or not template or len(template) > 200000:
                errors.append(reason("invalid_template", id=identifier))
            if not isinstance(bindings, list) or any(not isinstance(key, str) or not CLAIM_ID.fullmatch(key) for key in bindings):
                return errors + [reason("invalid_bindings", id=identifier)]
            if len(bindings) != len(set(bindings)):
                errors.append(reason("duplicate_binding", id=identifier))
            if isinstance(template, str):
                placeholders = set(PLACEHOLDER.findall(template))
                remaining = PLACEHOLDER.sub("", template)
                if placeholders != set(bindings) or "{{" in remaining or "}}" in remaining:
                    errors.append(reason("binding_mismatch", id=identifier))
        return errors

    @staticmethod
    def _invariant_errors(artifacts: dict[str, Any]) -> list[dict[str, Any]]:
        errors = []
        file_ids = {key for key, body in artifacts.items() if body["kind"] in {"document", "code"}}
        for key in file_ids:
            if any(parent.as_posix() in file_ids for parent in PurePosixPath(key).parents):
                errors.append(reason("path_collision", id=key))
        if any(key in artifacts for key in POLICY_KEYS):
            if not all(key in artifacts for key in POLICY_KEYS):
                errors.append(reason("invariant_violation", rule="complete_budget_parameters"))
            else:
                values = [artifacts[key].get("value") for key in POLICY_KEYS]
                if any(type(value) is not int or value <= 0 for value in values):
                    errors.append(reason("invariant_violation", rule="positive_integer_parameters"))
                elif artifacts[POLICY_KEYS[0]].get("unit") != "s" or artifacts[POLICY_KEYS[2]].get("unit") != "s" or artifacts[POLICY_KEYS[1]].get("unit") != "count":
                    errors.append(reason("invariant_violation", rule="budget_units"))
                elif values[0] * values[1] > values[2]:
                    errors.append(reason("invariant_violation", rule="timeout_times_attempts", calculated_seconds=values[0] * values[1], budget_seconds=values[2]))
        for key, body in artifacts.items():
            if body["kind"] in {"document", "code"}:
                bindings_valid = True
                for binding in body["bindings"]:
                    if artifacts.get(binding, {}).get("kind") != "claim":
                        errors.append(reason("missing_bound_claim", id=key, binding=binding))
                        bindings_valid = False
                if body["kind"] == "code" and bindings_valid:
                    rendered = PLACEHOLDER.sub(lambda match: repr(artifacts[match.group(1)]["value"]), body["template"])
                    try:
                        ast.parse(rendered, filename=key)
                    except (SyntaxError, ValueError, RecursionError) as error:
                        errors.append(reason("code_syntax", id=key, detail=str(error)))
        return errors

    def _write_artifacts(self, connection: sqlite3.Connection, writes: list[dict[str, Any]], revision: int) -> None:
        for body in writes:
            connection.execute(
                "INSERT INTO artifacts VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,scope=excluded.scope,body=excluded.body,digest=excluded.digest,revision=excluded.revision",
                (body["id"], body["kind"], body.get("scope"), canonical(body), digest(body), revision),
            )

    def _record_revision(self, connection: sqlite3.Connection, proposal_id: str | None, revision: int) -> None:
        state = self._artifacts(connection)
        connection.execute("INSERT INTO revisions VALUES (?,?,?,?)", (revision, proposal_id, canonical(state), digest(state)))
        connection.execute("UPDATE meta SET value=? WHERE key='revision'", (str(revision),))

    def _dependencies(self, connection: sqlite3.Connection) -> dict[str, Any]:
        """Retain declared dependencies with a baseline after atomic acceptance.

        A proposal reads the preimage of every write, including create-only
        absence. Those are not its resulting dependencies. Rebase recorded
        reads and queries to its accepted snapshot; exclude only self when
        checking, retaining conservative cross-artifact dependencies in bundles.
        """
        dependencies = {}
        baseline_cache: dict[int, dict[str, Any]] = {}
        rows = connection.execute(
            "SELECT a.id,a.revision,r.state,p.id AS proposal_id,p.payload "
            "FROM artifacts a JOIN revisions r ON a.revision=r.revision "
            "LEFT JOIN proposals p ON r.proposal_id=p.id ORDER BY a.id"
        )
        for row in rows:
            if row["proposal_id"] is None:
                dependencies[row["id"]] = {
                    "proposal_id": None, "baseline_revision": row["revision"],
                    "readset": {}, "queries": {}, "write_ids": [row["id"]],
                }
                continue
            if row["revision"] not in baseline_cache:
                baseline_cache[row["revision"]] = json.loads(row["state"])
            baseline = baseline_cache[row["revision"]]
            payload = json.loads(row["payload"])
            dependencies[row["id"]] = {
                "proposal_id": row["proposal_id"],
                "baseline_revision": row["revision"],
                "readset": {key: digest(baseline[key]) if key in baseline else None for key in payload["readset"]},
                "queries": {scope: self._query_digest(baseline, scope) for scope in payload["queries"]},
                "write_ids": sorted(body["id"] for body in payload["writes"]),
            }
        return dependencies

    @staticmethod
    def _publication_errors(artifacts: dict[str, Any], dependencies: dict[str, Any]) -> list[dict[str, Any]]:
        """Bindings rerender; other accepted assumptions must remain current."""
        errors = []
        for target, dependency in dependencies.items():
            bindings = set(artifacts[target].get("bindings", []))
            for key, expected in dependency["readset"].items():
                if key == target or key in bindings:
                    continue
                actual = digest(artifacts[key]) if key in artifacts else None
                if expected != actual:
                    errors.append(reason("stale_accepted_read", id=target, dependency=key, expected=expected, actual=actual))
            for scope, expected in dependency["queries"].items():
                actual = Store._query_digest(artifacts, scope)
                if expected != actual:
                    errors.append(reason("stale_accepted_query", id=target, scope=scope, expected=expected, actual=actual))
        return errors

    def bootstrap(self, artifacts: list[dict[str, Any]]) -> None:
        with self._transaction(write=True) as connection:
            from . import governance
            if governance.enabled(connection):
                raise WorkbenchError("governance_bootstrap_forbidden", "Governance cannot be bypassed through bootstrap")
            if self._revision(connection) != 0 or connection.execute("SELECT 1 FROM artifacts").fetchone():
                raise WorkbenchError("already_initialized", "Bootstrap requires an empty knowledge store")
            if not isinstance(artifacts, list) or not artifacts:
                raise WorkbenchError("invalid_bootstrap", "Nonempty artifact list required")
            errors = [error for body in artifacts for error in self._artifact_errors(body, connection)]
            if not errors and len({body["id"] for body in artifacts}) != len(artifacts):
                errors.append(reason("duplicate_write"))
            if not errors:
                errors.extend(self._invariant_errors({body["id"]: body for body in artifacts}))
            if errors:
                raise WorkbenchError("invalid_bootstrap", canonical(errors))
            self._write_artifacts(connection, artifacts, 1)
            self._record_revision(connection, None, 1)
            self._event(connection, "bootstrapped", {"revision": 1, "artifacts": len(artifacts)})

    def snapshot(self) -> dict[str, Any]:
        with self._transaction() as connection:
            artifacts = self._artifacts(connection)
            return {
                "revision": self._revision(connection),
                "artifacts": artifacts,
                "digests": {key: digest(body) for key, body in artifacts.items()},
                "queries": {scope: self._query_digest(artifacts, scope) for scope in QUERY_SCOPES},
                "dependencies": self._dependencies(connection),
                "dependency_coverage": "complete",
                "sources": {row["id"]: dict(row) for row in connection.execute("SELECT * FROM sources ORDER BY id")},
            }

    def query_digest(self, scope: str) -> str:
        if scope not in QUERY_SCOPES:
            raise WorkbenchError("unknown_query", "Only registered scope queries are allowed")
        return self.snapshot()["queries"][scope]

    def enqueue(self, task_id: str, role: str, payload: dict[str, Any], depends_on: list[str] | None = None) -> None:
        dependencies = depends_on or []
        if not isinstance(task_id, str) or not task_id or len(task_id) > 120 or role not in ROLE_KINDS or not isinstance(payload, dict):
            raise WorkbenchError("invalid_task", "Task needs a valid ID, author role and object payload")
        if not isinstance(dependencies, list) or any(not isinstance(key, str) for key in dependencies) or len(set(dependencies)) != len(dependencies):
            raise WorkbenchError("invalid_dependencies", "Unique task IDs required")
        payload_text, dependency_text = canonical(payload), canonical(dependencies)
        with self._transaction(write=True) as connection:
            old = connection.execute("SELECT role,payload,dependencies FROM tasks WHERE id=?", (task_id,)).fetchone()
            if old:
                if tuple(old) != (role, payload_text, dependency_text):
                    raise WorkbenchError("idempotency_conflict", "Task ID reused with different input")
                return
            for dependency in dependencies:
                if not connection.execute("SELECT 1 FROM tasks WHERE id=?", (dependency,)).fetchone():
                    raise WorkbenchError("unknown_dependency", "Dependencies must already exist; forward cycles are forbidden")
            connection.execute("INSERT INTO tasks(id,role,payload,dependencies) VALUES (?,?,?,?)", (task_id, role, payload_text, dependency_text))
            self._event(connection, "task_enqueued", {"task_id": task_id, "role": role})

    def claim_task(self, worker: str, roles: list[str] | None = None, lease_seconds: float = 60, *,
                   credential: str | None = None) -> dict[str, Any] | None:
        if not isinstance(worker, str) or not worker or not isinstance(lease_seconds, (int, float)) or not math.isfinite(lease_seconds) or lease_seconds < 0:
            raise WorkbenchError("invalid_lease", "Worker ID and nonnegative finite lease required")
        if roles is not None and (not isinstance(roles, list) or any(role not in ROLE_KINDS for role in roles)):
            raise WorkbenchError("invalid_role", "Unknown task role")
        with self._transaction(write=True) as connection:
            from . import authorship
            now = time.time()
            authenticated = None
            if authorship.enabled(connection):
                authenticated = authorship.authorize_worker(connection, credential, worker, now=now)
                if roles is not None and any(role != authenticated["role"] for role in roles):
                    raise WorkbenchError("authorship_role_mismatch", "A credential can claim only its own task role")
                roles = [authenticated["role"]] if roles is None else roles
            for row in connection.execute("SELECT * FROM tasks WHERE state='pending' OR (state='leased' AND lease_until<=?) ORDER BY rowid", (now,)):
                if roles is not None and row["role"] not in roles:
                    continue
                dependencies = json.loads(row["dependencies"])
                if any(connection.execute("SELECT state FROM tasks WHERE id=?", (key,)).fetchone()[0] != "done" for key in dependencies):
                    continue
                token = row["token"] + 1
                if authenticated is not None:
                    now = time.time()
                    authenticated = authorship.authorize_worker(connection, credential, worker, row["role"], now=now)
                connection.execute("UPDATE tasks SET state='leased',token=?,worker=?,lease_until=? WHERE id=?", (token, worker, now + lease_seconds, row["id"]))
                if authenticated is not None:
                    authorship.pin_lease(connection, row["id"], token, authenticated, now)
                self._event(connection, "task_leased", {"task_id": row["id"], "worker": worker, "token": token, "reclaimed": row["state"] == "leased"})
                return {"task_id": row["id"], "role": row["role"], "payload": json.loads(row["payload"]), "token": token, "worker": worker, "lease_until": now + lease_seconds}
        return None

    @staticmethod
    def _valid_lease(row: sqlite3.Row | None, worker: str, token: int) -> bool:
        return bool(row and row["state"] == "leased" and row["worker"] == worker and row["token"] == token and row["lease_until"] > time.time())

    def heartbeat(self, task_id: str, worker: str, token: int, lease_seconds: float = 60, *,
                  credential: str | None = None) -> None:
        if not isinstance(lease_seconds, (int, float)) or not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise WorkbenchError("invalid_lease", "Positive finite lease required")
        with self._transaction(write=True) as connection:
            from . import authorship
            row = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            if authorship.enabled(connection):
                authorship.authorize_attempt(connection, row, worker, token, credential)
            if not self._valid_lease(row, worker, token):
                raise WorkbenchError("lease_lost", "Lease expired or a newer attempt owns this task")
            connection.execute("UPDATE tasks SET lease_until=? WHERE id=?", (time.time() + lease_seconds, task_id))

    def _payload_errors(self, payload: Any, connection: sqlite3.Connection, role: str) -> list[dict[str, Any]]:
        if not isinstance(payload, dict) or set(payload) != {"readset", "queries", "writes", "rationale"}:
            return [reason("invalid_proposal", detail="Expected readset, queries, writes, rationale")]
        readset, queries, writes = payload["readset"], payload["queries"], payload["writes"]
        if not isinstance(readset, dict) or any(not isinstance(key, str) or value is not None and (not isinstance(value, str) or not HASH.fullmatch(value)) for key, value in readset.items()):
            return [reason("invalid_readset")]
        if not isinstance(queries, dict) or any(scope not in QUERY_SCOPES or not isinstance(value, str) or not HASH.fullmatch(value) for scope, value in queries.items()):
            return [reason("invalid_query")]
        if not isinstance(payload["rationale"], str) or not payload["rationale"].strip():
            return [reason("missing_rationale")]
        if not isinstance(writes, list) or not writes or len(writes) > 100:
            return [reason("invalid_writes")]
        errors = []
        ids = []
        for body in writes:
            artifact_errors = self._artifact_errors(body, connection)
            errors.extend(artifact_errors)
            if artifact_errors:
                continue
            identifier = body["id"]
            ids.append(identifier)
            if body["kind"] not in ROLE_KINDS[role]:
                errors.append(reason("role_violation", id=identifier, role=role))
            if identifier == "api.budget_seconds":
                errors.append(reason("protected_claim", id=identifier))
            if identifier not in readset:
                errors.append(reason("missing_write_base", id=identifier))
            for binding in body.get("bindings", []):
                if binding not in readset:
                    errors.append(reason("missing_binding_read", id=identifier, binding=binding))
        if len(ids) != len(set(ids)):
            errors.append(reason("duplicate_write"))
        return errors

    def submit(self, task_id: str, worker: str, token: int, proposal: dict[str, Any], *,
               credential: str | None = None) -> dict[str, Any]:
        try:
            payload_text = canonical(proposal)
        except (ValueError, TypeError) as error:
            raise WorkbenchError("invalid_proposal", str(error)) from error
        if len(payload_text.encode("utf-8")) > 1000000:
            raise WorkbenchError("proposal_too_large", "Proposal limit is 1 MB")
        payload_digest = digest(proposal)
        with self._transaction(write=True) as connection:
            from . import authorship
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            attributed = authorship.enabled(connection)
            if attributed:
                authorship.authorize_attempt(connection, task, worker, token, credential)
            if task and task["role"] == "integrator":
                raise WorkbenchError("composition_required", "Integrator tasks must use compose")
            if task and task["state"] == "done" and task["worker"] == worker and task["token"] == token:
                previous = connection.execute("SELECT digest FROM proposals WHERE id=?", (task["result_id"],)).fetchone()
                if previous[0] != payload_digest:
                    raise WorkbenchError("idempotency_conflict", "Completed attempt already returned different content")
                result = {"proposal_id": task["result_id"], "digest": payload_digest, "replayed": True}
                if attributed:
                    previous_row = self._find_proposal(connection, task["result_id"])
                    authorship.authorize_attempt(connection, task, worker, token, credential,
                                                 sorted(body["id"] for body in json.loads(previous_row["payload"])["writes"]))
                    result["authorship"] = authorship.metadata(authorship.require(connection, task["result_id"])["record"])
                return result
            if not self._valid_lease(task, worker, token):
                raise WorkbenchError("lease_lost", "Expired or superseded attempts cannot submit")
            errors = self._payload_errors(proposal, connection, task["role"])
            if errors:
                raise WorkbenchError("invalid_proposal", canonical(errors))
            proposal_id = "p_" + digest({"task": task_id, "token": token, "payload": payload_digest})[:24]
            connection.execute("INSERT INTO proposals(id,task_id,author,token,payload,digest) VALUES (?,?,?,?,?,?)", (proposal_id, task_id, worker, token, payload_text, payload_digest))
            proof = authorship.record(connection, proposal_id, task, worker, token, credential) if attributed else None
            connection.execute("UPDATE tasks SET state='done',result_id=? WHERE id=?", (proposal_id, task_id))
            self._event(connection, "proposal_submitted", {"proposal_id": proposal_id, "task_id": task_id, "digest": payload_digest, "worker": worker, "token": token})
            result = {"proposal_id": proposal_id, "digest": payload_digest, "replayed": False}
            if attributed:
                result["authorship"] = proof
            return result

    @staticmethod
    def _components(connection: sqlite3.Connection, proposal_id: str) -> list[dict[str, Any]]:
        """Return captured lineage in its original, one-based component order."""
        return [
            {"position": row["position"], "proposal_id": row["source_id"],
             "digest": row["source_digest"],
             "write_ids": sorted(body["id"] for body in json.loads(row["writes"]))}
            for row in connection.execute(
                "SELECT position,source_id,source_digest,writes FROM proposal_components "
                "WHERE composite_id=? ORDER BY position", (proposal_id,),
            )
        ]

    def compose(self, task_id: str, worker: str, token: int, *,
                credential: str | None = None) -> dict[str, Any]:
        """Combine exact document/code proposals without accepting any of them.

        The integrator task's immutable payload has exactly ``components``
        (one to 32 distinct ``{proposal_id, digest}`` references) and a nonempty
        ``rationale``. The resulting proposal's rationale is a canonical JSON
        string ``{text, components}``; each embedded component reference also
        contains sorted ``write_ids``, so its digest pins their source mapping.
        No formal or editorial approvals are inherited from the components.
        """
        with self._transaction(write=True) as connection:
            from . import authorship
            task = connection.execute("SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
            attributed = authorship.enabled(connection)
            if attributed:
                authorship.authorize_attempt(connection, task, worker, token, credential)
            if task is None:
                raise WorkbenchError("lease_lost", "Expired or superseded attempts cannot compose")
            if task["role"] != "integrator":
                raise WorkbenchError("invalid_composition", "Composition requires an integrator task")
            # Replay depends on the completed attempt, not on source proposals
            # which may have changed status or acquired stale reads since then.
            if task["state"] == "done" and task["worker"] == worker and task["token"] == token:
                previous = self._find_proposal(connection, task["result_id"])
                result = {"proposal_id": previous["id"], "digest": previous["digest"],
                          "replayed": True, "components": self._components(connection, previous["id"])}
                if attributed:
                    authorship.authorize_attempt(connection, task, worker, token, credential,
                                                 sorted(body["id"] for body in json.loads(previous["payload"])["writes"]))
                    result["authorship"] = authorship.metadata(authorship.require(connection, previous["id"])["record"])
                return result
            if not self._valid_lease(task, worker, token):
                raise WorkbenchError("lease_lost", "Expired or superseded attempts cannot compose")
            request = json.loads(task["payload"])
            if (not isinstance(request, dict) or set(request) != {"components", "rationale"}
                    or not isinstance(request["rationale"], str) or not request["rationale"].strip()
                    or not isinstance(request["components"], list)
                    or not 1 <= len(request["components"]) <= 32):
                raise WorkbenchError("invalid_composition", "Expected 1..32 components and a nonempty rationale")
            references = request["components"]
            for reference in references:
                if (not isinstance(reference, dict) or set(reference) != {"proposal_id", "digest"}
                        or not isinstance(reference["proposal_id"], str) or not reference["proposal_id"]
                        or not isinstance(reference["digest"], str) or not HASH.fullmatch(reference["digest"])):
                    raise WorkbenchError("invalid_composition", "Each component needs a proposal_id and exact digest")
            if len({reference["proposal_id"] for reference in references}) != len(references):
                raise WorkbenchError("invalid_composition", "Component proposal IDs must be unique")

            readset: dict[str, Any] = {}
            queries: dict[str, str] = {}
            writes: dict[str, Any] = {}
            captured = []
            embedded_references = []
            for position, reference in enumerate(references, 1):
                source = self._find_proposal(connection, reference["proposal_id"])
                if attributed:
                    authorship.require(connection, source["id"])
                if source["digest"] != reference["digest"]:
                    raise WorkbenchError("component_digest_mismatch", reference["proposal_id"])
                if source["status"] != "proposed":
                    raise WorkbenchError("component_not_proposed", reference["proposal_id"])
                source_errors = self._evaluate(connection, source)
                if source_errors:
                    raise WorkbenchError("stale_component", canonical({"proposal_id": source["id"], "reasons": source_errors}))
                payload = json.loads(source["payload"])
                for body in payload["writes"]:
                    if body["kind"] not in ROLE_KINDS["integrator"]:
                        raise WorkbenchError("component_write_kind", "Only document and code writes can be composed")
                    if body["id"] in writes:
                        raise WorkbenchError("composition_write_conflict", body["id"])
                    writes[body["id"]] = body
                for key, expected in payload["readset"].items():
                    if key in readset and readset[key] != expected:
                        raise WorkbenchError("composition_read_conflict", key)
                    readset[key] = expected
                for scope, expected in payload["queries"].items():
                    if scope in queries and queries[scope] != expected:
                        raise WorkbenchError("composition_query_conflict", scope)
                    queries[scope] = expected
                captured.append((position, source["id"], source["digest"], canonical(payload["writes"])))
                embedded_references.append({
                    "proposal_id": source["id"], "digest": source["digest"],
                    "write_ids": sorted(body["id"] for body in payload["writes"]),
                })

            proposal = {
                "readset": {key: readset[key] for key in sorted(readset)},
                "queries": {scope: queries[scope] for scope in sorted(queries)},
                "writes": [writes[key] for key in sorted(writes)],
                "rationale": canonical({"text": request["rationale"], "components": embedded_references}),
            }
            payload_text = canonical(proposal)
            if len(payload_text.encode("utf-8")) > 1000000:
                raise WorkbenchError("proposal_too_large", "Proposal limit is 1 MB")
            errors = self._payload_errors(proposal, connection, "integrator")
            if not errors:
                errors.extend(self._invariant_errors({**self._artifacts(connection), **writes}))
            if errors:
                raise WorkbenchError("invalid_proposal", canonical(errors))
            payload_digest = digest(proposal)
            proposal_id = "p_" + digest({"task": task_id, "token": token, "payload": payload_digest})[:24]
            connection.execute(
                "INSERT INTO proposals(id,task_id,author,token,payload,digest) VALUES (?,?,?,?,?,?)",
                (proposal_id, task_id, worker, token, payload_text, payload_digest),
            )
            connection.executemany(
                "INSERT INTO proposal_components(composite_id,position,source_id,source_digest,writes) VALUES (?,?,?,?,?)",
                [(proposal_id, *item) for item in captured],
            )
            proof = authorship.record(connection, proposal_id, task, worker, token, credential) if attributed else None
            connection.execute("UPDATE tasks SET state='done',result_id=? WHERE id=?", (proposal_id, task_id))
            components = self._components(connection, proposal_id)
            self._event(connection, "proposal_composed", {
                "proposal_id": proposal_id, "task_id": task_id, "digest": payload_digest,
                "worker": worker, "token": token, "components": components,
            })
            result = {"proposal_id": proposal_id, "digest": payload_digest,
                      "replayed": False, "components": components}
            if attributed:
                result["authorship"] = proof
            return result

    def _evaluate(self, connection: sqlite3.Connection, row: sqlite3.Row) -> list[dict[str, Any]]:
        payload = json.loads(row["payload"])
        if digest(payload) != row["digest"]:
            return [reason("digest_mismatch")]
        role = connection.execute("SELECT role FROM tasks WHERE id=?", (row["task_id"],)).fetchone()[0]
        errors = self._payload_errors(payload, connection, role)
        current = self._artifacts(connection)
        for identifier, expected in payload["readset"].items():
            actual = digest(current[identifier]) if identifier in current else None
            if actual != expected:
                errors.append(reason("stale_read", id=identifier, expected=expected, actual=actual))
        for scope, expected in payload["queries"].items():
            actual = self._query_digest(current, scope)
            if actual != expected:
                errors.append(reason("query_changed", scope=scope, expected=expected, actual=actual))
        if not errors:
            merged = {**current, **{body["id"]: body for body in payload["writes"]}}
            errors.extend(self._invariant_errors(merged))
        return errors

    @staticmethod
    def _find_proposal(connection: sqlite3.Connection, proposal_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if not row:
            raise WorkbenchError("unknown_proposal", proposal_id)
        return row

    def governance_enable(self, policy: dict[str, Any], administrator_id: str,
                          new_credential: str) -> dict[str, Any]:
        from . import governance
        with self._transaction(write=True) as connection:
            return governance.enable(connection, policy, administrator_id, new_credential)

    def authorship_enable(self, *, credential: str,
                          expected_policy_generation: int) -> dict[str, Any]:
        from . import authorship
        with self._transaction(write=True) as connection:
            return authorship.enable(connection, credential=credential,
                                     expected_policy_generation=expected_policy_generation)

    def authorship_status(self, proposal_id: str | None = None) -> dict[str, Any]:
        from . import authorship
        with self._transaction() as connection:
            if proposal_id is not None:
                self._find_proposal(connection, proposal_id)
            return authorship.status(connection, proposal_id)

    def content_enable(self, policy: dict[str, Any], *, credential: str,
                       expected_policy_generation: int) -> dict[str, Any]:
        from . import content_gate
        with self._transaction(write=True) as connection:
            return content_gate.enable(connection, policy, credential=credential,
                                       expected_policy_generation=expected_policy_generation)

    def content_set_policy(self, policy: dict[str, Any], *, credential: str,
                           expected_policy_generation: int) -> dict[str, Any]:
        from . import content_gate
        with self._transaction(write=True) as connection:
            return content_gate.set_policy(connection, policy, credential=credential,
                                           expected_policy_generation=expected_policy_generation)

    def content_status(self, proposal_id: str | None = None) -> dict[str, Any]:
        from . import content_gate
        with self._transaction() as connection:
            if proposal_id is not None:
                self._find_proposal(connection, proposal_id)
            return content_gate.status(connection, proposal_id)

    def content_packet(self, packet_id: str) -> dict[str, Any]:
        from . import content_gate
        with self._transaction() as connection:
            return content_gate.load_packet(connection, packet_id)

    def continuity_enable(self, *, credential: str,
                          expected_policy_generation: int) -> dict[str, Any]:
        from . import review_continuity
        with self._transaction(write=True) as connection:
            return review_continuity.enable(connection, credential=credential,
                                            expected_policy_generation=expected_policy_generation)

    def continuity_status(self, proposal_id: str | None = None) -> dict[str, Any]:
        from . import review_continuity
        with self._transaction() as connection:
            if proposal_id is not None:
                self._find_proposal(connection, proposal_id)
            return review_continuity.status(connection, proposal_id)

    def continuity_audit_start(self, decision_id: str, *, credential: str,
                               expected_policy_generation: int,
                               expected_target_packet_digest: str,
                               operation_id: str) -> dict[str, Any]:
        from . import review_history_audit
        with self._transaction(write=True) as connection:
            return review_history_audit.start(
                connection, decision_id, credential=credential,
                expected_policy_generation=expected_policy_generation,
                expected_target_packet_digest=expected_target_packet_digest,
                operation_id=operation_id)

    def continuity_audit_advance(self, audit_id: str, *, credential: str,
                                 expected_checkpoint_id: str, operation_id: str,
                                 limit: int = 64) -> dict[str, Any]:
        from . import review_history_audit
        with self._transaction(write=True) as connection:
            return review_history_audit.advance(
                connection, audit_id, credential=credential,
                expected_checkpoint_id=expected_checkpoint_id, limit=limit,
                operation_id=operation_id)

    def continuity_audit_show(self, audit_id: str,
                              checkpoint_id: str | None = None) -> dict[str, Any]:
        from . import review_history_audit
        with self._transaction() as connection:
            return review_history_audit.show(connection, audit_id, checkpoint_id)

    def continuity_audit_recover(self, operation_id: str, *, credential: str) -> dict[str, Any]:
        from . import review_history_recovery
        with self._transaction() as connection:
            return review_history_recovery.recover(connection, operation_id, credential=credential)

    def continuity_audit_catalogue(self, *, after_seq: int = 0,
                                  snapshot_max_seq: int | None = None,
                                  limit: int = 10) -> dict[str, Any]:
        from . import review_history_recovery
        with self._transaction() as connection:
            return review_history_recovery.catalogue(
                connection, after_seq=after_seq, snapshot_max_seq=snapshot_max_seq, limit=limit)

    def governance_grant(self, principal_id: str, role: str, artifact_ids: list[str],
                         expires_at: float, *, credential: str, new_credential: str,
                         operation_id: str) -> dict[str, Any]:
        from . import governance
        with self._transaction(write=True) as connection:
            return governance.grant(connection, principal_id, role, artifact_ids, expires_at,
                                    credential=credential, new_credential=new_credential,
                                    operation_id=operation_id)

    def governance_revoke(self, grant_id: str, *, credential: str) -> dict[str, Any]:
        from . import governance
        with self._transaction(write=True) as connection:
            return governance.revoke(connection, grant_id, credential=credential)

    def governance_set_policy(self, policy: dict[str, Any], *, credential: str,
                              expected_generation: int) -> dict[str, Any]:
        from . import governance
        with self._transaction(write=True) as connection:
            return governance.set_policy(connection, policy, credential=credential,
                                         expected_generation=expected_generation)

    def governance_decide(self, proposal_id: str, artifact_id: str, verdict: str,
                          rationale: str, *, credential: str,
                          expected_proposal_digest: str, expected_policy_generation: int,
                          expected_previous_decision: str | None,
                          operation_id: str,
                          expected_content_packet_digest: str | None = None) -> dict[str, Any]:
        from . import governance
        with self._transaction(write=True) as connection:
            return governance.decide(connection, self._find_proposal(connection, proposal_id),
                                     artifact_id, verdict, rationale, credential=credential,
                                     expected_proposal_digest=expected_proposal_digest,
                                     expected_policy_generation=expected_policy_generation,
                                     expected_previous_decision=expected_previous_decision,
                                     operation_id=operation_id,
                                     expected_content_packet_digest=expected_content_packet_digest)

    def governance_status(self, proposal_id: str | None = None) -> dict[str, Any]:
        from . import governance
        with self._transaction() as connection:
            if proposal_id is not None:
                self._find_proposal(connection, proposal_id)
            return governance.status(connection, proposal_id)

    def governance_observe(self, proposal_id: str, *, credential: str,
                           expected_policy_generation: int) -> dict[str, Any]:
        from . import governance
        with self._transaction(write=True) as connection:
            row = self._find_proposal(connection, proposal_id)
            now = time.time()
            coordinator = governance.authorize(connection, credential, "coordinator",
                                                governance.write_ids(row), now=now)
            return governance.observe(connection, row, coordinator, expected_policy_generation, now=now)

    def review(self, proposal_id: str, reviewer: str = "reviewer", *,
               credential: str | None = None) -> dict[str, Any]:
        with self._transaction(write=True) as connection:
            from . import authorship, governance
            row = self._find_proposal(connection, proposal_id)
            governed = governance.enabled(connection)
            if governed:
                reviewer = governance.authorize(connection, credential, "reviewer",
                                                governance.write_ids(row))["principal_id"]
            if authorship.enabled(connection):
                authorship.separate(connection, proposal_id, reviewer)
            if not isinstance(reviewer, str) or not reviewer or reviewer == row["author"]:
                raise WorkbenchError("self_review", "Reviewer must be distinct from proposal author")
            errors = self._evaluate(connection, row)
            evidence = {}
            for body in json.loads(row["payload"])["writes"]:
                if body["kind"] == "claim":
                    source_id = body["evidence"]["source_id"]
                    evidence[source_id] = connection.execute("SELECT digest FROM sources WHERE id=?", (source_id,)).fetchone()[0]
            if governed:
                governance.authorize(connection, credential, "reviewer", governance.write_ids(row), now=time.time())
            report = {"proposal_id": proposal_id, "proposal_digest": row["digest"], "rules_version": RULES_VERSION, "approved": not errors, "reasons": errors, "reviewer": reviewer, "observed_revision": self._revision(connection), "source_digests": evidence, "meaning_check": "Exact quote presence only; not an entailment or truth judgment"}
            cursor = connection.execute("INSERT INTO reviews(proposal_id,reviewer,proposal_digest,rules_version,approved,report) VALUES (?,?,?,?,?,?)", (proposal_id, reviewer, row["digest"], RULES_VERSION, int(not errors), canonical(report)))
            report["review_id"] = cursor.lastrowid
            self._event(connection, "proposal_reviewed", report)
            return report

    def accept(self, proposal_id: str, coordinator: str = "coordinator", *,
               credential: str | None = None,
               expected_policy_generation: int | None = None) -> dict[str, Any]:
        if not isinstance(coordinator, str) or not coordinator:
            raise WorkbenchError("invalid_coordinator", "Coordinator name required")
        with self._transaction(write=True) as connection:
            from . import governance
            row = self._find_proposal(connection, proposal_id)
            governed = governance.enabled(connection)
            authenticated = None
            if governed:
                authentication_time = time.time()
                authenticated = governance.authorize(connection, credential, "coordinator",
                                                     governance.write_ids(row), now=authentication_time)
                coordinator = authenticated["principal_id"]
                policy = governance.current_policy(connection)
                if (type(expected_policy_generation) is not int
                        or expected_policy_generation != policy["generation"]):
                    observation = governance.observe(connection, row, authenticated, expected_policy_generation)
                    result = {"accepted": False, "proposal_id": proposal_id,
                              "revision": self._revision(connection), "replayed": False,
                              "reasons": [reason("governance_policy_generation_mismatch",
                                                 expected=expected_policy_generation,
                                                 actual=policy["generation"])],
                              "governance": observation}
                    self._event(connection, "accept_blocked", result)
                    return result
            if row["status"] == "accepted":
                result = {"accepted": True, "proposal_id": proposal_id, "revision": row["accepted_revision"], "replayed": True, "reasons": []}
                if governed:
                    result["coordinator"] = authenticated["principal_id"]
                    result["coordinator_grant_id"] = authenticated["grant_id"]
                    result["authorization_time"] = authentication_time
                    result["governance_receipt"] = governance.existing_receipt(connection, proposal_id)
                    result["legacy_acceptance"] = result["governance_receipt"] is None
                return result
            review = connection.execute("SELECT * FROM reviews WHERE proposal_id=? ORDER BY id DESC LIMIT 1", (proposal_id,)).fetchone()
            errors = []
            if not review or not review["approved"]:
                errors.append(reason("unapproved"))
            elif review["proposal_digest"] != row["digest"] or review["rules_version"] != RULES_VERSION:
                errors.append(reason("stale_review"))
            errors.extend(self._evaluate(connection, row))
            observation = None
            if governed:
                now = time.time()
                authenticated = governance.authorize(connection, credential, "coordinator",
                                                     governance.write_ids(row), now=now)
                observation = governance.observe(connection, row, authenticated,
                                                 expected_policy_generation, now=now)
                errors.extend(observation["reasons"])
                if not errors:
                    observation, authenticated = governance.finalize_observation(
                        connection, row, credential, expected_policy_generation, observation)
                    errors.extend(observation["reasons"])
            if errors:
                result = {"accepted": False, "proposal_id": proposal_id, "revision": self._revision(connection), "replayed": False, "reasons": errors}
                if governed:
                    result["governance"] = observation
                    if observation and "authorization_time" in observation:
                        result["authorization_time"] = observation["authorization_time"]
                self._event(connection, "accept_blocked", result)
                return result
            revision = self._revision(connection) + 1
            self._write_artifacts(connection, json.loads(row["payload"])["writes"], revision)
            self._record_revision(connection, proposal_id, revision)
            connection.execute("UPDATE proposals SET status='accepted',accepted_revision=? WHERE id=?", (revision, proposal_id))
            result = {"accepted": True, "proposal_id": proposal_id, "revision": revision, "replayed": False, "reasons": [], "review_id": review["id"], "coordinator": coordinator}
            if governed:
                result["governance"] = observation
                result["governance_receipt"] = governance.receipt(connection, row, revision, observation)
                result["authorization_time"] = observation["authorization_time"]
                result["coordinator_grant_id"] = authenticated["grant_id"]
                result["legacy_acceptance"] = False
            self._event(connection, "proposal_accepted", result)
            return result

    def proposal(self, proposal_id: str) -> dict[str, Any]:
        with self._transaction() as connection:
            result = dict(self._find_proposal(connection, proposal_id))
            result["payload"] = json.loads(result["payload"])
            return result

    def proposal_context(self, proposal_id: str) -> dict[str, Any]:
        """Capture a proposal and its current basis in one read transaction.

        This is an observation for an editor. It does not reserve a revision,
        write a review, or grant acceptance rights after the transaction ends.
        """
        with self._transaction() as connection:
            row = self._find_proposal(connection, proposal_id)
            proposal = dict(row)
            proposal["payload"] = json.loads(proposal["payload"])
            payload = proposal["payload"]
            current = self._artifacts(connection)
            review = connection.execute(
                "SELECT * FROM reviews WHERE proposal_id=? ORDER BY id DESC LIMIT 1",
                (proposal_id,),
            ).fetchone()
            formal_errors = []
            if not review or not review["approved"]:
                formal_errors.append(reason("unapproved"))
            elif review["proposal_digest"] != row["digest"] or review["rules_version"] != RULES_VERSION:
                formal_errors.append(reason("stale_review"))
            formal_review = None
            if review:
                formal_review = {**json.loads(review["report"]), "review_id": review["id"]}
            identifiers = set(payload["readset"]) | {body["id"] for body in payload["writes"]}
            return {
                "schema_version": 2, "runtime_rules_version": RULES_VERSION,
                "observed_revision": self._revision(connection), "proposal": proposal,
                "components": self._components(connection, proposal_id),
                "artifacts": {key: current.get(key) for key in sorted(identifiers)},
                "actual_readset": {
                    key: digest(current[key]) if key in current else None
                    for key in sorted(payload["readset"])
                },
                "actual_queries": {
                    scope: self._query_digest(current, scope) for scope in sorted(payload["queries"])
                },
                "sources": {
                    source["id"]: dict(source)
                    for source in connection.execute("SELECT id,text,digest FROM sources ORDER BY id")
                },
                "formal_review": formal_review, "formal_errors": formal_errors,
                "runtime_errors": self._evaluate(connection, row),
            }

    def tasks(self) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            tasks = []
            for row in connection.execute("SELECT * FROM tasks ORDER BY rowid"):
                task = dict(row)
                task["task_id"] = task.pop("id")
                task["payload"] = json.loads(task["payload"])
                task["dependencies"] = json.loads(task["dependencies"])
                tasks.append(task)
            return tasks

    def events(self) -> list[dict[str, Any]]:
        with self._transaction() as connection:
            return [{"seq": row["seq"], "type": row["type"], "data": json.loads(row["data"])} for row in connection.execute("SELECT * FROM events ORDER BY seq")]

    def status(self) -> dict[str, Any]:
        with self._transaction() as connection:
            return {
                "revision": self._revision(connection),
                "artifacts": connection.execute("SELECT count(*) FROM artifacts").fetchone()[0],
                "sources": connection.execute("SELECT count(*) FROM sources").fetchone()[0],
                "tasks": {row[0]: row[1] for row in connection.execute("SELECT state,count(*) FROM tasks GROUP BY state")},
                "proposals": {row[0]: row[1] for row in connection.execute("SELECT status,count(*) FROM proposals GROUP BY status")},
                "events": connection.execute("SELECT count(*) FROM events").fetchone()[0],
                "rules_version": RULES_VERSION,
            }

    def publish(self, output_dir: str | Path) -> dict[str, Any]:
        """Export a pinned snapshot. No live site or active release is switched."""
        # This reservation serializes the entire filesystem effect, including
        # cleanup, with source sealing. It writes no domain rows or events.
        with self._transaction(write=True) as connection:
            return self._publish(connection, output_dir)

    def _publish(self, connection: sqlite3.Connection, output_dir: str | Path) -> dict[str, Any]:
        revision = self._revision(connection)
        row = connection.execute("SELECT * FROM revisions WHERE revision=?", (revision,)).fetchone()
        if not row:
            raise WorkbenchError("empty_store", "Bootstrap before exporting")
        artifacts = json.loads(row["state"])
        dependencies = self._dependencies(connection)
        sources = {source["id"]: dict(source) for source in connection.execute("SELECT * FROM sources ORDER BY id")}
        # Rendering uses the captured immutable state while retaining the
        # writer reservation through all output and staging cleanup below.
        publication_errors = self._publication_errors(artifacts, dependencies)
        if publication_errors:
            raise WorkbenchError("release_needs_review", canonical(publication_errors))
        files: dict[str, bytes] = {}
        index = []
        for identifier, body in artifacts.items():
            if body["kind"] not in {"document", "code"}:
                continue
            if not safe_file_id(identifier, body["kind"]):
                raise WorkbenchError("unsafe_path", identifier)
            def substitute(match: re.Match[str]) -> str:
                value = artifacts[match.group(1)]["value"]
                # repr is a Python scalar literal; no expression from agents
                # is evaluated. Markdown displays the value as ordinary text.
                return repr(value) if body["kind"] == "code" else str(value)
            content = PLACEHOLDER.sub(substitute, body["template"])
            files[identifier] = content.encode("utf-8")
            if body["kind"] == "document":
                index.append({"path": identifier, "text": content, "terms": sorted(set(re.findall(r"\w+", content.casefold())))})
        files["knowledge.json"] = (json.dumps(artifacts, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        used_sources = {body["evidence"]["source_id"] for body in artifacts.values() if body["kind"] == "claim"}
        files["sources.json"] = (json.dumps({key: sources[key] for key in sorted(used_sources)}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        files["dependencies.json"] = (json.dumps({"schema_version": 1, "revision": revision, "artifacts": dependencies}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        files["search-index.json"] = (json.dumps({"revision": revision, "documents": index}, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        manifest = {
            "schema_version": 2, "revision": revision, "state_digest": row["state_digest"],
            "rules_version": RULES_VERSION,
            "files": {name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for name, data in sorted(files.items())},
        }
        release_id = f"r{revision}-{digest(manifest)[:16]}"
        manifest["release_id"] = release_id
        files["manifest.json"] = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
        output = Path(output_dir)
        if output.is_symlink():
            raise WorkbenchError("unsafe_path", "Output directory cannot be a symlink")
        output.mkdir(parents=True, exist_ok=True)
        # Private, newly created staging directories avoid traversal through
        # symlinks inside an existing release. No existing file is overwritten.
        destination = output / release_id
        staging = Path(tempfile.mkdtemp(prefix=".building-", dir=output))
        try:
            for name, data in files.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
            if destination.exists() or destination.is_symlink():
                _verify_release_files(destination, files)
            else:
                try:
                    staging.rename(destination)
                except OSError as error:
                    if error.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                        raise
                    _verify_release_files(destination, files)
            return {"release_id": release_id, "revision": revision, "path": str(destination), "files": manifest["files"], "state_digest": row["state_digest"]}
        finally:
            if staging.exists():
                shutil.rmtree(staging)


def _verify_release_files(directory: Path, expected: dict[str, bytes]) -> None:
    if directory.is_symlink() or not directory.is_dir():
        raise WorkbenchError("release_conflict", "Release path is not a regular directory")
    actual = set()
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise WorkbenchError("release_conflict", "Release contains a symlink")
        if path.is_file():
            actual.add(path.relative_to(directory).as_posix())
    if actual != set(expected):
        raise WorkbenchError("release_conflict", "Existing release has missing or extra files")
    if any((directory / name).read_bytes() != data for name, data in expected.items()):
        raise WorkbenchError("release_conflict", "Existing release content differs")


def search_release(release: str | Path, query: str) -> dict[str, Any]:
    """Search one immutable release and verify every file before returning hits."""
    from .releases import load_release

    loaded = load_release(release)
    manifest = loaded["manifest"]
    index = json.loads(loaded["files"]["search-index.json"])
    terms = set(re.findall(r"\w+", query.casefold()))
    results = []
    for document in index["documents"]:
        score = len(terms.intersection(document["terms"]))
        if score:
            results.append({"path": document["path"], "score": score, "snippet": document["text"][:240]})
    results.sort(key=lambda item: (-item["score"], item["path"]))
    return {"release_id": manifest["release_id"], "revision": manifest["revision"], "results": results}
