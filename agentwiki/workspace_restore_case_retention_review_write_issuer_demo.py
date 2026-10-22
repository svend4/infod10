"""Demonstrate local issuer policies and durable receipts for exact permits.

The driver invokes ordinary public CLI commands. It reads validated public
responses, credential markers and lstat metadata, but never private control
bodies, lease SQL rows or staging contents. The preceding development scenario
is preserved; credentials stay in private files and no new fault worker or expiry wait is introduced.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import stat
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo

from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .store import Store, canonical
from .workspace_restore_case_retention_review_cli import _public_response as _review_response
from .workspace_restore_case_retention_review_write_catalog_cli import _public_response as _catalog_response
from .workspace_restore_case_retention_review_write_cli import _public_response as _write_response
from .workspace_restore_case_retention_review_write_leases_cli import _public_response as _lease_response
from .workspace_restore_case_retention_review_write_permit_demo import run_workspace_restore_case_retention_review_write_permit_demo
from .workspace_restore_case_retention_review_write_action_cli import _public_response as _action_response
from .workspace_restore_case_retention_review_write_permit_cli import _public_response as _permit_response
from .workspace_restore_case_retention_review_write_issuer_cli import _public_response as _issuer_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-issuer-1"
COMMAND = "workspace-restore-case-retention-review-write-issuer"
PERMIT_COMMAND = "workspace-restore-case-retention-review-write-permit"
ACTION_COMMAND = "workspace-restore-case-retention-review-write-action"
PREFIX = "restore-case-retention-review-write-issuer"
CATALOG_COMMAND = "workspace-restore-case-retention-review-write-catalog"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"
LEASE_COMMAND = "workspace-restore-case-retention-review-write-leases"
REVIEW_COMMAND = "workspace-restore-case-retention-review"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write issuer demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_issuer_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Keep issuance authority and historical receipts separate from native permits."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_permit_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 431, "the preceding scenario did not produce 431 public files")
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").lstat().st_size
    markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "job_directory", "job_record", "job_lock", "source_bundle",
        "original_manifest", "restore_manifest", "owner", "staging_path", "sha256", "schema_fingerprint",
        "request_digest", "result_digest", "plan_path", "plan_directory", "catalog_path", "case_path", "case_directory",
        "catalog_directory", "parent", "pins", "review_path", "review_directory", "plan_anchor", "plan_source",
        "review_record", "review_lock", "stage_pin", "case_record", "case_lock", "ledger_path",
        "ledger_directory", "database_path", "database_pin", "catalog_record", "catalog_lock", "catalog_request_digest",
        "action_path", "action_directory", "intent_pin", "intent_record", "result_record", "source_pins",
        "intent_digest", "ledger_anchor", "catalog_anchor", "ledger_manifest",
        "credential", "credential_hash", "credential_digest", "permit_path", "permit_directory",
        "grant_record", "revoke_record", "grant_digest", "action_anchor", "action_intent", "action_lock",
        "issuer_anchor", "permit_anchor", "holder_credential_hash", "issuer_path", "issuance_path",
        "issuer_directory", "issuance_directory", "issuer_record", "issuer_lock", "permit_record", "permit_lock",
    }

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not forbidden.intersection(value), "private metadata reached public output")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(re.search(r"awg_[A-Za-z0-9_-]{43}", value) is None, "a bearer token reached public output")
            _require(not any(marker in value for marker in markers), "a credential or its hash reached public output")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def controls(path: Path) -> dict[str, Any]:
        directory = path.lstat()
        return {"directory": (directory.st_dev, directory.st_ino, directory.st_uid, stat.S_IMODE(directory.st_mode)),
                "files": {entry.name: (details.st_dev, details.st_ino, details.st_uid, details.st_mode,
                                        details.st_nlink, details.st_size, details.st_mtime_ns, details.st_ctime_ns)
                          for entry in sorted(path.iterdir()) for details in [entry.lstat()]}}

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(), "access": store.snapshot_access()}

    def matches(before: dict[str, Any], after: dict[str, Any]) -> bool:
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {key: child for key, child in value["governance"].items() if key != "observed_at"}}
        return canonical(comparable(before)) == canonical(comparable(after))

    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    pages: dict[str, Any] = {}
    discarded_addresses: dict[str, str] = {}

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"{PREFIX}-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def launch(command: str, action: str, arguments: list[str]) -> subprocess.Popen[str]:
        _require(command in {COMMAND, PERMIT_COMMAND, ACTION_COMMAND, CATALOG_COMMAND, WRITE_COMMAND, LEASE_COMMAND, REVIEW_COMMAND, "workspace-restore"},
                 "unsupported ordinary command")
        return subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                                shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def collect(process: subprocess.Popen[str], label: str, command: str, action: str, *,
                expected_error: str | None = None, allowed_error: str | None = None,
                discard: bool = False) -> Any:
        try:
            stdout, stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate(timeout=15)
            raise RuntimeError("Restore case retention review write issuer demonstration stopped: ordinary CLI timed out") from error
        match = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
        code = match.group(1) if match else None
        cli_outcomes.append({"label": label, "command": command, "action": action,
                             "child_process_id": process.pid, "returncode": process.returncode,
                             "error_code": code, "stderr_empty": not stderr, "completed_child_process": True,
                             "shell": False, "business_response_discarded": discard})
        if expected_error is not None or process.returncode != 0 and allowed_error is not None:
            _require(not discard and process.returncode != 0 and code == (expected_error or allowed_error) and not stdout,
                     "an ordinary command did not return its expected safe refusal")
            refusal = {"label": label, "command": command, "action": action, "code": code, "rejected": True}
            rejections.append(refusal)
            return refusal
        _require(process.returncode == 0 and not stderr, "an ordinary CLI command failed")
        try:
            value = json.loads(stdout)
            if command == COMMAND:
                value = _issuer_response(value, action)
            elif command == PERMIT_COMMAND:
                value = _permit_response(value, action)
            elif command == ACTION_COMMAND:
                value = _action_response(value, action)
            elif command == LEASE_COMMAND:
                value = _lease_response(value, action)
            elif command == REVIEW_COMMAND:
                value = _review_response(value, action)
            elif command == CATALOG_COMMAND:
                value = _catalog_response(value, action)
            elif command == WRITE_COMMAND:
                value = _write_response(value, action)
            else:
                value = _public_status(value)
            value = public(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Restore case retention review write issuer demonstration stopped: invalid public CLI response") from error
        if discard:
            discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        return value

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> Any:
        with launch(command, action, arguments) as process:
            return collect(process, label, command, action, expected_error=expected_error, discard=discard)

    job, review = private / (PREFIX + "-job"), private / (PREFIX + "-review")
    catalog, ledger = private / (PREFIX + "-catalog"), private / (PREFIX + "-ledger")
    names = ("fill", "link", "finish")
    action_directories = {name: private / f"{PREFIX}-action-{index:02d}" for index, name in enumerate(names, 1)}
    permit_directories = {name: private / f"{PREFIX}-permit-{index:02d}" for index, name in enumerate(names, 1)}
    issuance_directories = {name: private / f"{PREFIX}-issuance-{index:02d}" for index, name in enumerate(names, 1)}
    holder_paths = {name: private / f"{PREFIX}-holder-{index:02d}.credential" for index, name in enumerate(names, 1)}
    issuer_directories = {name: private / f"{PREFIX}-issuer-{name}" for name in ("main", "deny")}
    administrator_paths = {name: private / f"{PREFIX}-admin-{name}.credential" for name in ("main", "deny")}
    actor, worker, principal = "simulated-issuer-review-operator", "simulated-issuer-worker", "simulated-issuer-holder"
    administrators = {"main": "simulated-issuer-administrator", "deny": "simulated-deny-administrator"}
    old_credentials = sorted(private.glob("*.credential"))
    _require(len(old_credentials) >= 2, "preceding private credentials are unavailable")
    unrelated_credential, second_unrelated_credential = old_credentials[:2]
    prior_statuses = json.loads((workspace / "restore-case-retention-review-write-catalog-write-observations.json").read_text())["final_statuses"]
    for label in ("action", "permit"):
        prior_statuses[label] = json.loads((workspace / f"restore-case-retention-review-write-{label}-execution-observations.json").read_text())["statuses"]["restored"]
    prior_lease_pages = json.loads((workspace / "restore-case-retention-review-write-leases-pages.json").read_text())
    prior_jobs = {name: private / ("restore-case-retention-review-write-" + name) for name in ("minimum", "long")}
    prior_jobs["late"] = private / "restore-case-retention-review-write-catalog-late-job"
    prior_jobs["action"] = private / "restore-case-retention-review-write-action-job"
    prior_jobs["permit"] = private / "restore-case-retention-review-write-permit-job"
    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    original_paths = sorted(path for path in private.iterdir() if path.is_dir())
    original_controls = {path: controls(path) for path in original_paths}
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    statuses: dict[str, Any] = {}
    actions: dict[str, Any] = {}
    receipts: dict[str, Any] = {}
    issuers: dict[str, Any] = {}
    issuer_shows: dict[str, Any] = {}
    revocations: dict[str, Any] = {}
    recorded: dict[str, Any] = {}
    protected: dict[str, Any] = {}
    receipt_observations: dict[str, Any] = {}
    native_shows: dict[str, Any] = {}
    catalog_observations: dict[str, Any] = {}
    issuance_replays: dict[str, Any] = {}
    failed_new_paths: list[Path] = []
    error_prefix = "workspace_restore_case_retention_review_write_issuer_"
    credential_invalid, policy_denied = error_prefix + "credential_invalid", error_prefix + "policy_denied"
    conflict, invalid, revoked = error_prefix + "conflict", error_prefix + "invalid_input", error_prefix + "revoked"
    lease_fields = ("lease_id", "generation", "worker", "lease_seconds", "claimed_at", "expires_at", "released_at")

    def remember(path: Path) -> None:
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))

    def originals_unchanged() -> bool:
        return (all(controls(path) == previous for path, previous in original_controls.items())
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == previous
                        for index, previous in old_observations.items()))

    def create_arguments(name: str, *, first: bool = False, directory: Path | None = None,
                         policy_override: list[dict[str, Any]] | None = None) -> list[str]:
        result = ["--issuer", str(issuer_directories[name] if directory is None else directory),
                  "--operation-id", PREFIX + "-create-" + name, "--administrator", administrators[name],
                  "--credential-out" if first else "--prepared-credential", str(administrator_paths[name])]
        for rule in policies[name] if policy_override is None else policy_override:
            result.extend(["--rule", rule["rule_id"], rule["principal"], rule["worker"], rule["ledger_id"],
                           str(rule["index"]), rule["before_state"]])
        return result

    def show_issuer(name: str, label: str) -> dict[str, Any]:
        before = controls(issuer_directories[name])
        value = cli(label, COMMAND, "show", ["--issuer", str(issuer_directories[name])])
        _require(value["issuer"] == issuers[name] and controls(issuer_directories[name]) == before,
                 "issuer show changed its immutable policy or controls")
        issuer_shows[label] = value
        return value

    def issue_arguments(name: str, *, first: bool = False, issuer_name: str = "main",
                        issuer_credential_path: Path | None = None, holder_path: Path | None = None,
                        issuance_path: Path | None = None, permit_path: Path | None = None,
                        action_name: str | None = None, principal_label: str | None = None,
                        operation_id: str | None = None) -> list[str]:
        return ["--issuer", str(issuer_directories[issuer_name]),
                "--issuance", str(issuance_directories[name] if issuance_path is None else issuance_path),
                "--permit", str(permit_directories[name] if permit_path is None else permit_path),
                "--action", str(action_directories[name if action_name is None else action_name]),
                "--principal", principal if principal_label is None else principal_label,
                "--operation-id", PREFIX + "-issue-" + name if operation_id is None else operation_id,
                "--issuer-credential-file", str(administrator_paths[issuer_name] if issuer_credential_path is None else issuer_credential_path),
                "--holder-credential-out" if first else "--prepared-holder-credential",
                str(holder_paths[name] if holder_path is None else holder_path)]

    def issue(name: str, *, label: str | None = None, first: bool = False,
              discard: bool = False, **overrides: Any) -> Any:
        label = name + "-issuer-issue" if label is None else label
        before = controls(issuer_directories[overrides.get("issuer_name", "main")])
        value = cli(label, COMMAND, "issue", issue_arguments(name, first=first, **overrides), discard=discard)
        if first:
            remember(holder_paths[name])
        _require(controls(issuer_directories[overrides.get("issuer_name", "main")]) == before,
                 "issuing a child changed issuer policy controls")
        if not discard:
            public(value)
            _require(value["issuer"] == issuers["main"] and value["permit"]["action"] == actions[name]
                     and value["permit"]["principal"] == principal and value["policy_authorized"] is True,
                     "issuer receipt changed its exact policy, action or principal")
            if name in receipts:
                _require(value == receipts[name], "the complete immutable issuance response changed")
            receipts[name] = value
        return value

    def rejected_issue(label: str, name: str, error: str, *, fresh: bool = False, **overrides: Any) -> Any:
        if fresh:
            issuance_path = private / (PREFIX + "-" + label + "-issuance")
            permit_path = private / (PREFIX + "-" + label + "-permit")
            failed_new_paths.extend((issuance_path, permit_path))
            overrides.update({"issuance_path": issuance_path, "permit_path": permit_path})
        watched = [*issuer_directories.values(), *action_directories.values(), *permit_directories.values(),
                   *issuance_directories.values(), ledger, job, review]
        before = {path: controls(path) for path in watched if path.is_dir()}
        result = cli(label, COMMAND, "issue", issue_arguments(name, **overrides), expected_error=error)
        _require(all(controls(path) == previous for path, previous in before.items())
                 and all(not path.exists() for path in failed_new_paths),
                 "a refused issue changed existing controls or reserved child paths")
        return result

    def read_receipt(name: str, label: str) -> dict[str, Any]:
        before = controls(issuance_directories[name])
        value = cli(label, COMMAND, "receipt", ["--issuance", str(issuance_directories[name])])
        _require(value == receipts[name] and controls(issuance_directories[name]) == before,
                 "own-only receipt reading changed immutable history")
        receipt_observations[label] = value
        return value

    def revoke_issuer(name: str, *, label: str | None = None, credential_path: Path | None = None,
                      operation_id: str | None = None, expected_error: str | None = None) -> Any:
        label = name + "-issuer-revoke" if label is None else label
        watched = [*action_directories.values(), *permit_directories.values(), *issuance_directories.values(), ledger, job, review]
        before = {path: controls(path) for path in watched if path.is_dir()}
        own_before = controls(issuer_directories[name])
        value = cli(label, COMMAND, "revoke", ["--issuer", str(issuer_directories[name]), "--operation-id",
                    PREFIX + "-revoke-" + name if operation_id is None else operation_id,
                    "--credential-file", str(administrator_paths[name] if credential_path is None else credential_path)],
                    expected_error=expected_error)
        _require(all(controls(path) == previous for path, previous in before.items()),
                 "issuer revocation changed existing children or leases")
        if expected_error is not None or name in revocations:
            _require(controls(issuer_directories[name]) == own_before, "a refusal or revocation replay changed issuer controls")
        if expected_error is None:
            _require(value["issuer"] == issuers[name] and value["state"] == "revoked",
                     "revocation changed issuer identity or policy")
            if name in revocations:
                _require(value == revocations[name], "exact issuer revocation replay changed its marker")
            revocations[name] = value
        return value

    def prepare_action(name: str, state: str) -> dict[str, Any]:
        value = cli(name + "-action-prepare", ACTION_COMMAND, "prepare",
                    ["--action", str(action_directories[name]), "--ledger", str(ledger), "--index", "1", "--worker", worker,
                     "--lease-id", claim["lease_id"], "--generation", str(claim["generation"]),
                     "--operation-id", PREFIX + "-action-" + name, "--expected-state", state])
        _require(value["before_state"] == state and value["lease"] == claim
                 and value["write"]["write_id"] == statuses["prepared"]["write_id"],
                 "a fresh action changed its selected phase or captured claim")
        actions[name] = value
        return value

    def execute_native(name: str, label: str | None = None) -> dict[str, Any]:
        label = name + "-native-permit-execute" if label is None else label
        before = {path: controls(path) for path in (ledger, issuer_directories["main"], issuance_directories[name])
                  if path.is_dir()}
        value = cli(label, PERMIT_COMMAND, "execute",
                    ["--permit", str(permit_directories[name]), "--credential-file", str(holder_paths[name])])
        _require(value["permit"] == receipts[name]["permit"] and value["action_result"]["action"] == actions[name]
                 and value["credential_verified"] is True and value["revocation_checked"] is True
                 and value["historical_effect_authenticated"] is False
                 and all(controls(path) == previous for path, previous in before.items()),
                 "native execution changed issuance authority or captured scope")
        if name in protected:
            _require(value == protected[name], "native saved protected result changed")
        protected[name], recorded[name] = value, value["action_result"]
        return value

    def status(label: str, state: str) -> dict[str, Any]:
        value = cli(label + "-write-status", WRITE_COMMAND, "status", ["--job", str(job)])
        _require(value["state"] == state and value["completed"] is (state == "completed")
                 and value["intended_review"] == statuses["prepared"]["intended_review"],
                 "the new write changed intended content or expected phase")
        statuses[label] = value
        return value

    def lease_page(label: str) -> dict[str, Any]:
        before = controls(ledger)
        value = cli(label, LEASE_COMMAND, "page", ["--ledger", str(ledger), "--limit", "16"])
        _require(controls(ledger) == before, "lease page changed the private journal")
        pages[label] = value
        return value

    statuses["prepared"] = cli("fresh-write-prepare", WRITE_COMMAND, "prepare",
                               ["--plan", str(private / "restore-case-retention-minimum"), "--review", str(review),
                                "--job", str(job), "--operation-id", PREFIX + "-write", "--actor", actor, "--verdict", "defer"])
    _require(statuses["prepared"]["state"] == "prepared"
             and statuses["prepared"]["intended_review"]["actor"] == actor
             and statuses["prepared"]["intended_review"]["plan"] == prior_statuses["minimum"]["intended_review"]["plan"],
             "the sixth write changed the exact historical minimum plan")
    catalog_receipt = cli("fresh-catalog-create", CATALOG_COMMAND, "create",
                          ["--catalog", str(catalog), "--job", str(job), "--operation-id", PREFIX + "-catalog"])
    ledger_receipt = cli("fresh-ledger-create", LEASE_COMMAND, "create",
                         ["--ledger", str(ledger), "--catalog", str(catalog), "--operation-id", PREFIX + "-ledger"])
    claim = cli("fresh-lease-claim", LEASE_COMMAND, "claim",
                ["--ledger", str(ledger), "--index", "1", "--worker", worker, "--lease-seconds", "3600",
                 "--operation-id", PREFIX + "-claim"])
    policies = {
        "main": [{"rule_id": "allow-" + name, "principal": principal, "worker": worker,
                  "ledger_id": ledger_receipt["ledger_id"], "index": 1, "before_state": before, "target_state": target}
                 for name, before, target in (("fill", "prepared", "ready"), ("link", "ready", "linked"), ("finish", "linked", "completed"))],
        "deny": [{"rule_id": "deny-other-worker", "principal": principal, "worker": "simulated-issuer-other-worker",
                  "ledger_id": ledger_receipt["ledger_id"], "index": 1, "before_state": "prepared", "target_state": "ready"}],
    }
    for name in ("main", "deny"):
        issuers[name] = cli(name + "-issuer-create", COMMAND, "create", create_arguments(name, first=True))
        remember(administrator_paths[name])
        public(issuers[name])
        _require(issuers[name]["policy"] == policies[name] and issuers[name]["administrator"] == administrators[name],
                 "issuer creation changed the exact explicit policy")
    initial_create_replay = cli("main-issuer-create-exact-replay", COMMAND, "create", create_arguments("main"))
    initial_main = show_issuer("main", "main-initial-show")
    initial_deny = show_issuer("deny", "deny-initial-show")
    _require(initial_create_replay == issuers["main"] and initial_main["state"] == initial_deny["state"] == "active",
             "issuer create replay changed identity or initial state")
    prepare_action("fill", "prepared")
    stage("exact-issuer-policy-and-separate-keys",
          "A sixth write job has an explicit catalog and one claimed lease. Two owner-created local issuers have separate administrator credentials and immutable exact rules; the main policy covers three phases, while the other names a different worker. No person or model identity is established.",
          {"write": statuses["prepared"], "catalog": catalog_receipt, "ledger": ledger_receipt, "claim": claim,
           "issuers": issuers, "policies": policies, "create_replay": initial_create_replay,
           "shows": {"main": initial_main, "deny": initial_deny}, "action": actions["fill"],
           "credentials_exported": False, "external_identity_verified": False})

    wrong_admin = rejected_issue("wrong-administrator-issue", "fill", credential_invalid, fresh=True,
                                 issuer_credential_path=unrelated_credential, holder_path=second_unrelated_credential)
    wrong_principal = rejected_issue("unlisted-principal-issue", "fill", policy_denied, fresh=True,
                                     principal_label="simulated-unlisted-holder", holder_path=unrelated_credential)
    wrong_worker_policy = rejected_issue("nonmatching-worker-policy-issue", "fill", policy_denied, fresh=True,
                                         issuer_name="deny", holder_path=unrelated_credential)
    wrong_revoke = revoke_issuer("main", label="wrong-administrator-revoke", credential_path=unrelated_credential,
                                expected_error=credential_invalid)
    issue("fill", first=True)
    fill_replay = issue("fill", label="fill-issuance-exact-replay")
    fill_receipt = read_receipt("fill", "fill-own-receipt")
    holder_is_not_admin = rejected_issue("holder-is-not-administrator-issue", "fill", credential_invalid, fresh=True,
                                         issuer_credential_path=holder_paths["fill"], holder_path=unrelated_credential)
    fill_execution = execute_native("fill")
    ready_status = status("after-fill", "ready")
    claimed_page = lease_page("claimed-lease-page")
    ready_catalog = cli("ready-catalog-page", CATALOG_COMMAND, "page", ["--catalog", str(catalog), "--limit", "16"])
    catalog_observations["ready"] = ready_catalog
    _require(fill_replay == fill_receipt == receipts["fill"]
             and ready_catalog["entries"][0]["current_write"]["status"] == ready_status
             and claimed_page["operation_count"] == 1 and claimed_page["entries"][0]["state"] == "active",
             "issuer-gated fill changed receipt or advanced beyond its one phase")
    stage("credential-and-policy-gated-fill",
          "Wrong administrator credentials, an unlisted principal and a nonmatching worker policy refuse before new child directories are reserved. The holder credential does not administer issuance. A permitted fill creates a fresh native permit and a separate durable issuance receipt, then native protected execute reaches only ready.",
          {"wrong_administrator": wrong_admin, "unlisted_principal": wrong_principal, "nonmatching_policy": wrong_worker_policy,
           "wrong_revoke": wrong_revoke, "holder_is_not_administrator": holder_is_not_admin,
           "issuance": receipts["fill"], "exact_replay": fill_replay, "own_receipt": fill_receipt,
           "native_execution": fill_execution, "write_status": ready_status, "lease_page": claimed_page,
           "catalog_page": ready_catalog, "refused_child_paths_reserved": False})

    prepare_action("link", "ready")
    issue("link", label="link-issuance-response-discarded", first=True, discard=True)
    recovery_before = {path: controls(path) for path in (issuer_directories["main"], issuance_directories["link"],
                                                        permit_directories["link"], action_directories["link"], ledger, job, review)}
    recovered_link = issue("link", label="link-issuance-response-recovered")
    recovered = (discarded_addresses.pop("link-issuance-response-discarded")
                 == hashlib.sha256(canonical(recovered_link).encode("utf-8")).hexdigest())
    _require(recovered and all(controls(path) == previous for path, previous in recovery_before.items()),
             "the actual complete discarded issuance reply was not recovered exactly without new controls")
    link_receipt = read_receipt("link", "link-own-receipt")
    changed_requests = {}
    for label, overrides in (
        ("replay-other-holder-credential", {"holder_path": holder_paths["fill"]}),
        ("replay-other-principal", {"principal_label": "simulated-other-holder"}),
        ("replay-other-operation", {"operation_id": PREFIX + "-other-issue"}),
        ("replay-other-action", {"action_name": "fill"}),
        ("replay-other-native-permit", {"permit_path": permit_directories["fill"]}),
        ("replay-other-issuer", {"issuer_name": "deny"}),
    ):
        changed_requests[label] = rejected_issue(label, "link", conflict, **overrides)
    no_adoption_path = private / (PREFIX + "-no-adoption-issuance")
    failed_new_paths.append(no_adoption_path)
    no_adoption = rejected_issue("existing-native-permit-not-adopted", "fill", conflict,
                                 issuance_path=no_adoption_path, permit_path=permit_directories["fill"])
    link_execution = execute_native("link")
    linked_status = status("after-link", "linked")
    _require(link_receipt == recovered_link and linked_status["state"] == "linked",
             "link receipt recovery or exact phase execution changed")
    stage("durable-issuance-reply-recovery-and-no-adoption",
          "The actual complete link issuance response is discarded after success and recovered exactly from its completed receipt. Changed holder credentials, principal, operation, action, permit or issuer conflict. A fresh issuance cannot adopt an already existing native permit. Native protected execution then performs only ready-to-linked.",
          {"issuance": recovered_link, "own_receipt": link_receipt, "changed_requests": changed_requests,
           "no_adoption": no_adoption, "native_execution": link_execution, "write_status": linked_status,
           "actual_complete_issuance_reply_recovered_exactly": recovered, "comparison_digest_exported": False,
           "native_permit_adopted": False})

    prepare_action("finish", "linked")
    issue("finish", first=True)
    finish_receipt = read_receipt("finish", "finish-own-receipt")
    main_revocation = revoke_issuer("main")
    main_revoke_replay = revoke_issuer("main", label="main-issuer-revoke-exact-replay")
    deny_revocation = revoke_issuer("deny")
    main_revoked = show_issuer("main", "main-revoked-show")
    deny_revoked = show_issuer("deny", "deny-revoked-show")
    revoked_main_issue = rejected_issue("revoked-main-new-issuance", "finish", revoked, fresh=True)
    revoked_deny_issue = rejected_issue("revoked-deny-new-issuance", "finish", revoked, fresh=True, issuer_name="deny")
    post_revoke_replays = {name: issue(name, label=name + "-issuance-replay-after-issuer-revoke") for name in ("fill", "finish")}
    revoked_create_replay = cli("main-revoked-create-exact-replay", COMMAND, "create", create_arguments("main"))
    main_still_revoked = show_issuer("main", "main-still-revoked-show")
    finish_execution = execute_native("finish")
    completed_status = status("after-finish", "completed")
    _require(main_revocation == main_revoke_replay == main_revoked == main_still_revoked
             and deny_revocation == deny_revoked and revoked_create_replay == issuers["main"]
             and finish_receipt == receipts["finish"] and all(post_revoke_replays[name] == receipts[name] for name in post_revoke_replays)
             and all(value["action_result"]["result"]["effect_performed"] is True for value in protected.values()),
             "issuer revocation changed historical issuance or recalled an existing native permit")
    stage("revocation-stops-new-issuance-preserves-native-permits",
          "Both issuers are irreversibly revoked. New issuance refuses, while exact historical issuance replay remains fixed after current administrator authentication. Exact create/revoke replay never reactivates the issuer. The previously issued finish permit still performs linked-to-completed with its own holder credential.",
          {"finish_issuance": finish_receipt, "revocations": revocations, "main_revoke_replay": main_revoke_replay,
           "shows": {"main": main_revoked, "deny": deny_revoked}, "new_main_refusal": revoked_main_issue,
           "new_deny_refusal": revoked_deny_issue, "historical_replays": post_revoke_replays,
           "create_replay": revoked_create_replay, "after_create_show": main_still_revoked,
           "native_execution": finish_execution, "write_status": completed_status,
           "existing_permits_revoked": False, "issuers_reactivated": False})
    release = cli("fresh-lease-release", LEASE_COMMAND, "release",
                  ["--ledger", str(ledger), "--index", "1", "--worker", worker, "--lease-id", claim["lease_id"],
                   "--generation", str(claim["generation"]), "--operation-id", PREFIX + "-release"])
    released_page = lease_page("released-lease-page")
    detached: list[tuple[Path, Path]] = []
    offline_receipts: dict[str, Any] = {}
    offline_replays: dict[str, Any] = {}
    try:
        for label, original in (("catalog", catalog), ("ledger", ledger), ("job", job), ("review", review)):
            held = private / (PREFIX + "-offline-" + label)
            original.rename(held)
            detached.append((original, held))
        cached_finish = execute_native("finish", "cached-finish-after-issuer-revoke-and-lease-release")
        for name in names:
            for label, original in (("permit-" + name, permit_directories[name]), ("action-" + name, action_directories[name])):
                held = private / (PREFIX + "-offline-" + label)
                original.rename(held)
                detached.append((original, held))
        issuance_controls = {name: controls(path) for name, path in issuance_directories.items()}
        for name in names:
            offline_replays[name] = issue(name, label="offline-" + name + "-issuance-exact-replay")
            _require(offline_replays[name] == receipts[name], "completed issuance replay inspected absent child bindings")
        held_issuer = private / (PREFIX + "-offline-main-issuer")
        issuer_directories["main"].rename(held_issuer)
        detached.append((issuer_directories["main"], held_issuer))
        for name in names:
            offline_receipts[name] = read_receipt(name, "offline-" + name + "-own-receipt")
        _require(all(controls(issuance_directories[name]) == previous for name, previous in issuance_controls.items()),
                 "offline historical reading changed an issuance control")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    _require(len(detached) == 11 and cached_finish == finish_execution
             and released_page["operation_count"] == 2 and released_page["entries"][0]["state"] == "released"
             and released_page["entries"][0]["current_lease"] == {key: release[key] for key in lease_fields},
             "offline receipt observations changed lease history or lost detached paths")
    restored_catalog = cli("restored-catalog-show", CATALOG_COMMAND, "show", ["--catalog", str(catalog)])
    restored_status = status("restored", "completed")
    written_review = cli("written-review-old-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    _require(restored_catalog == catalog_receipt and restored_status == completed_status
             and written_review == statuses["prepared"]["intended_review"] and originals_unchanged(),
             "offline history checks changed the exact completed review or original controls")
    stage("historical-receipts-with-eleven-bindings-absent",
          "After lease release, an existing native permit still returns its cached protected finish result while four live bindings are absent. With three native permits and three actions also absent, exact completed issuance replay uses the live revoked issuer and administrator credential. Hiding the issuer makes eleven absent paths; own receipt lookup still reads only its controls. Every path returns in finally.",
          {"released_lease": release, "lease_page": released_page, "cached_native_finish": cached_finish,
           "issuance_replays_without_children": offline_replays, "receipts_without_issuer": offline_receipts,
           "restored_catalog": restored_catalog, "restored_status": restored_status, "written_review": written_review,
           "maximum_detached_path_count": 11, "all_detached_paths_restored": True,
           "own_receipt_needs_current_issuer": False, "exact_issue_replay_needs_live_issuer_authentication": True})

    other_revoke = revoke_issuer("main", label="revoke-replay-other-operation",
                                 operation_id=PREFIX + "-other-revoke", expected_error=conflict)
    bad_policy_refusals = {}
    for label, policy in (
        ("duplicate-rule-id-create", [policies["main"][0], {**policies["main"][1], "rule_id": policies["main"][0]["rule_id"]}]),
        ("duplicate-rule-scope-create", [policies["main"][0], {**policies["main"][0], "rule_id": "other-fill-rule"}]),
    ):
        path = private / (PREFIX + "-" + label)
        failed_new_paths.append(path)
        bad_policy_refusals[label] = cli(label, COMMAND, "create",
                                         create_arguments("main", directory=path, policy_override=policy), expected_error=invalid)
    previous_statuses = {name: cli("original-" + name + "-write-status", WRITE_COMMAND, "status", ["--job", str(path)])
                         for name, path in prior_jobs.items()}
    original_lease_pages = {}
    for name in ("primary", "secondary"):
        value = cli("original-" + name + "-lease-page", LEASE_COMMAND, "page",
                    ["--ledger", str(private / ("restore-case-retention-review-write-leases-" + name)), "--limit", "16"])
        previous = prior_lease_pages[name + "-final-released-page"]
        _require({key: child for key, child in value.items() if key != "checked_at"}
                 == {key: child for key, child in previous.items() if key != "checked_at"},
                 "an original ledger changed its exact membership or released head")
        original_lease_pages[name] = value
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    final_catalog_page = cli("completed-catalog-page", CATALOG_COMMAND, "page", ["--catalog", str(catalog), "--limit", "16"])
    final_lease_page = lease_page("final-released-lease-page")
    for name in names:
        value = cli("final-" + name + "-native-permit-show", PERMIT_COMMAND, "show", ["--permit", str(permit_directories[name])])
        _require(value["permit"] == receipts[name]["permit"] and value["state"] == "active" and value["revocation"] is None,
                 "issuer revocation changed a child native permit")
        native_shows[name] = value
    final_action_shows = {}
    for name in names:
        value = cli("final-" + name + "-action-show", ACTION_COMMAND, "show", ["--action", str(action_directories[name])])
        _require(value == recorded[name], "final historical action reading changed its saved result")
        final_action_shows[name] = value
    for name in names:
        read_receipt(name, "final-" + name + "-own-receipt")
    final_issuer_shows = {name: show_issuer(name, "final-" + name + "-issuer-show") for name in ("main", "deny")}
    source_after, live_after = observe(source), observe(live)
    issuer_outcomes = [value for value in cli_outcomes if value["command"] == COMMAND]
    _require(len(stages) == 5 and len(cli_outcomes) == 89 and len(rejections) == 20
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 69
             and len(issuer_outcomes) == 50 and sum(value["returncode"] == 0 for value in issuer_outcomes) == 33
             and len(issuers) == len(revocations) == 2 and len(receipts) == len(actions) == len(recorded) == 3
             and all(value["result"]["outcome"] == "phase_performed" and value["result"]["effect_performed"] is True
                     for value in recorded.values())
             and all(final_issuer_shows[name] == revocations[name] for name in revocations)
             and all(not path.exists() for path in failed_new_paths)
             and previous_statuses == prior_statuses and originals_unchanged()
             and matches(source_before, source_after) and matches(live_before, live_after)
             and final_catalog_page["entries"][0]["current_write"]["status"] == completed_status
             and final_lease_page["operation_count"] == 2 and final_lease_page["entries"] == released_page["entries"]
             and len(pages) == 3 and not discarded_addresses,
             "final issuer, receipt, phase, CLI or preserved-history accounting differs from the fixed scenario")
    catalog_observations["completed"] = final_catalog_page
    stage("exact-inputs-and-unchanged-domain-history",
          "Duplicate rule IDs and duplicate exact scopes refuse without reserving an issuer. Another revocation operation conflicts. Five earlier write jobs, released lease heads and three blocked restore jobs preserve their observations. Two issuers retain revocation, three durable issuance receipts and three active native permits retain history, and the new write remains completed.",
          {"other_revoke": other_revoke, "bad_policies": bad_policy_refusals, "rejections": rejections,
           "issuers": issuers, "revocations": revocations, "issuances": receipts,
           "final_issuer_shows": final_issuer_shows, "native_permit_shows": native_shows,
           "recorded": recorded, "final_action_shows": final_action_shows,
           "original_write_statuses": previous_statuses, "original_lease_pages": original_lease_pages,
           "catalog_page": final_catalog_page, "lease_page": final_lease_page,
           "original_control_metadata_unchanged": True, "source_public_observations_unchanged": True,
           "live_public_observations_unchanged": True, "existing_permits_revoked": False,
           "domain_writes_allowed": False, "archival_performed": False, "deletion_performed": False})

    artifacts = {
        PREFIX + "-issuers.json": {"created": issuers, "policies": policies, "observations": issuer_shows, "revocations": revocations},
        PREFIX + "-issuances.json": receipts,
        PREFIX + "-action-observations.json": {"actions": actions, "recorded": recorded},
        PREFIX + "-execution-observations.json": {
            "protected": protected, "statuses": statuses, "receipts": receipt_observations, "native_permit_shows": native_shows,
            "final_issuer_shows": final_issuer_shows, "final_action_shows": final_action_shows,
            "catalogs": {"receipt": catalog_receipt, "restored": restored_catalog, **catalog_observations},
            "written_review": written_review, "offline_receipts": offline_receipts, "offline_replays": offline_replays,
            "post_revoke_replays": post_revoke_replays, "original_write_statuses": previous_statuses,
            "original_control_metadata_unchanged": True, "original_directory_times_compared": False,
            "old_blocked_job_metadata_unchanged": True, "all_detached_paths_restored": True,
            "maximum_detached_path_count": 11, "new_lease_control_metadata_unchanged_by_issuance": True,
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "direct_step_used_for_new_write": False,
        },
        PREFIX + "-lease-observations.json": {"ledger": ledger_receipt, "claim": claim, "release": release,
                                               "pages": pages, "original_final_pages": original_lease_pages},
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {
            "actual_complete_link_issuance_response_discarded": True, "exact_complete_issuance_reply_recovered": recovered,
            "exact_issue_replay_checks_live_issuer_credential": True, "exact_issue_replay_allowed_after_issuer_revocation": True,
            "own_receipt_lookup_needs_live_issuer": False, "existing_native_permits_revoked": False,
            "comparison_digest_exported": False,
        },
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "six stages and ten public data files were not retained")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Local issuer policy and durable historical receipts for exact native write permits",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "issuer_stage_count": len(stages),
            "new_issuer_count": 2, "new_issuer_rule_count": 4, "revoked_issuer_count": 2, "active_issuer_count": 0,
            "new_issuance_count": 3, "new_native_permit_count": 3, "active_new_native_permit_count": 3,
            "new_native_permit_revocation_count": 0, "total_native_permit_count": 7, "total_native_permit_revocation_count": 3,
            "new_private_credential_file_count": 5, "new_administrator_credential_file_count": 2,
            "new_holder_credential_file_count": 3, "credentials_exported": False,
            "new_action_count": 3, "recorded_action_count": 3, "pending_action_count": 0,
            "total_action_count": 12, "total_recorded_action_count": 10, "total_pending_action_count": 2,
            "performed_phase_count": 3, "expected_phase_observation_count": 0,
            "new_write_job_count": 1, "total_write_job_count": 6, "new_review_record_count": 1, "total_review_record_count": 8,
            "new_write_catalog_count": 1, "total_write_catalog_count": 5,
            "new_lease_ledger_count": 1, "total_lease_ledger_count": 5,
            "new_lease_operation_count": 2, "total_lease_operation_count": 20,
            "new_ledger_page_response_count": len(pages), "prior_ledger_compatibility_page_count": 2,
            "lease_page_response_count": len(pages) + 2, "new_catalog_page_response_count": 2,
            "new_cli_child_process_count": len(cli_outcomes), "new_successful_cli_count": 69,
            "new_refused_cli_count": len(rejections), "issuer_command_count": len(issuer_outcomes),
            "issuer_successful_command_count": 33, "issuer_refused_command_count": 17,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "discarded_complete_issuance_response_count": 1, "discarded_complete_issuance_response_recovered_exactly": recovered,
            "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0, "injected_partial_write_count": 0,
            "maximum_offline_path_count": 11, "all_detached_paths_restored": True,
            "exact_issue_replay_needs_live_issuer_authentication": True, "own_receipt_lookup_needs_live_issuer": False,
            "exact_issue_replay_allowed_after_issuer_revocation": True, "issuer_revocation_blocks_new_issuance": True,
            "existing_native_permits_revoked": False, "issuer_create_replay_reactivates_issuer": False,
            "existing_native_permit_adopted": False, "issuer_policy_mutated": False,
            "native_execution_after_issuer_revocation_completed": True,
            "cached_native_replay_after_issuer_revocation_and_lease_release": True,
            "new_lease_control_metadata_unchanged_by_issuance": True, "original_control_metadata_unchanged": True,
            "old_blocked_job_metadata_unchanged": True, "original_blocked_jobs_still_blocked": True,
            "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
            "jobs_advanced": True, "direct_step_used_for_new_write": False,
            "issuance_policy_only": True, "local_capability_only": True, "external_identity_verified": False,
            "new_restore_attempt_count": 0, "domain_writes_allowed": False, "archival_performed": False, "deletion_performed": False,
            "expiry_path_demonstrated": False, "clock_regression_path_demonstrated": False,
            "interruption_recovery_demonstrated": False, "partial_issuance_repair_demonstrated": False,
            "cross_directory_atomicity_claimed": False, "external_fencing_established": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "actor_authenticated": False, "worker_authenticated": False, "old_authority_activated": False,
            "external_llm_used": False, "human_review_recorded": False, "proposed_code_executed": False,
            "measured_capacity": False, "process_overlap_measured": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "The owner bootstraps immutable local issuer policies. Administrator, principal and worker are self-declared labels; a bearer proves local possession, not a person, organization, independent model or semantic authority.",
            "Rules match an exact principal, captured worker, ledger ID, index and PRE/POST phase pair. They contain no wildcard or priority and do not constrain lease generation; every native permit still captures its exact original action and claim.",
            "New issuance requires the live issuer credential, an unrevoked issuer and a matching explicit rule. The issuer, issuance, native permit and action locks share one deadline. Issuance itself performs no write phase and does not renew a lease.",
            "A separate immutable issuance receipt follows the durable native grant. Their directories do not form an atomic transaction; a failure between writes may leave a native permit usable through the old API without a completed issuance receipt. Such partial states are not repaired or adopted here.",
            "Issuer revocation blocks future issuance and waits behind admitted issuing. Existing native permits remain independent; their own bearer executes or self-revokes them. Old APIs, other issuers, filesystem owners and copies remain outside this managed issuance policy.",
            "An exact complete issue replay authenticates the live issuer and holder request but returns immutable history even after issuer revocation, without reading child permits or actions. Own receipt lookup needs only its own controls and does not establish current issuer or child permission.",
            "The actual complete link issuance reply was discarded and recovered exactly without new controls. This ordinary workflow does not demonstrate process overlap, issuance/revoke races, partial writes, interrupted receipt sealing, power loss, expiry waits or clock regression.",
            "Eleven paths are temporarily absent at the widest observation: issuer, three native permits, three actions and four live bindings. All return in finally. Receipt lookup neither reconstructs missing private bodies nor repairs historical directories.",
            "The driver reads validated public responses, private credential markers and lstat metadata, never private control bodies, SQL rows or staging. The earlier source/live histories remain R4/36 and R5/59; no archival, deletion, external LLM or semantic approval is performed.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Локальная политика выдачи и неизменяемые квитанции permit", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий bearer permits](restore-case-retention-review-write-permit-report.md); его 431 публичный файл сохранён.",
        "Добавлены шестой write job, восьмой review, пятые каталог и ledger. Три точных action выполняют prepared→ready→linked→completed через новые native permits.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "Два локальных издателя имеют отдельные ключи администратора и четыре точных policy rules. Чужой credential, незаявленный principal и несовпадающий worker блокируют новую выдачу без резервирования child directories. Holder credential не является ключом издателя.",
        "Три разрешённые выдачи сохраняют три новых native permits и три отдельные issuance receipts. Настоящий полный ответ выдачи link отброшен и точно восстановлен. Изменённые точные запросы конфликтуют; существующий native permit не усыновляется новой выдачей.",
        "Оба издателя отозваны. Новые выдачи блокируются; точный исторический replay после проверки исходного ключа остаётся доступен и не оживляет издателя. Уже выданный finish permit после отзыва издателя завершает фазу linked→completed со своим holder credential.",
        "После release и скрытия четырёх live bindings cached native finish replay сохранился. С десятью отсутствующими bindings точные issue replays читают исторические receipts при живом отозванном issuer. Скрытие issuer доводит число путей до одиннадцати; own-only receipt lookup работает без issuer/native/action. Все пути возвращены.",
        "Публичная issuance receipt фиксирует локальное разрешение выдачи по исторической политике. Она не подтверждает текущее право новой выдачи, действующий child permit, личность человека, автора эффекта либо смысловую достоверность документа.", "",
        "## Публичные основания", "",
        f"- [Издатели, политики и отзывы]({PREFIX}-issuers.json), [три квитанции выдачи]({PREFIX}-issuances.json).",
        f"- [Три действия и результата]({PREFIX}-action-observations.json), [исполнение и offline наблюдения]({PREFIX}-execution-observations.json), [аренда]({PREFIX}-lease-observations.json).",
        f"- [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json), [восстановление ответа]({PREFIX}-reply-recovery.json).",
        f"- [Источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы", "",
        "Шесть этапов, 89 новых CLI: 69 успешных и 20 ожидаемых отказов. Из них 50 issuer-команд: 33 успешных и 17 отказов. С основанием 761 ordinary CLI и 14 прежних interrupt workers, всего 775 дочерних процессов.",
        "Всего двенадцать action intents, десять saved results и два прежних pending intents; семь native permits с тремя прежними отзывами; шесть write jobs, восемь reviews, пять каталогов, пять lease DB и 20 lease событий. Выдача не исполняет action и не продлевает claim.",
        "Native grant и отдельная issuance receipt не образуют атомарную транзакцию каталогов. Неполные controls не ремонтируются; окно до receipt не выдаётся за проверенное восстановление после прерывания. Старые APIs и файловый владелец остаются за новой политикой.",
        "Private credentials/hashes, управляющие записи, pins и digests не экспортируются. Новых fault workers, injected partial writes, ожидания expiry, LLM либо исполнения предложенного кода нет. Ёмкость агентов и качество содержания не измерялись.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
