"""Demonstrate exact local bearer permits and irreversible revocation for actions.

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
from .workspace_restore_case_retention_review_write_action_demo import run_workspace_restore_case_retention_review_write_action_demo
from .workspace_restore_case_retention_review_write_action_cli import _public_response as _action_response
from .workspace_restore_case_retention_review_write_permit_cli import _public_response as _permit_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-permit-1"
COMMAND = "workspace-restore-case-retention-review-write-permit"
ACTION_COMMAND = "workspace-restore-case-retention-review-write-action"
PREFIX = "restore-case-retention-review-write-permit"
CATALOG_COMMAND = "workspace-restore-case-retention-review-write-catalog"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"
LEASE_COMMAND = "workspace-restore-case-retention-review-write-leases"
REVIEW_COMMAND = "workspace-restore-case-retention-review"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write permit demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_permit_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Keep exact action permits, credentials and revocation separate from job phases."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_action_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 413, "the preceding scenario did not produce 413 public files")
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
        _require(command in {COMMAND, ACTION_COMMAND, CATALOG_COMMAND, WRITE_COMMAND, LEASE_COMMAND, REVIEW_COMMAND, "workspace-restore"},
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
            raise RuntimeError("Restore case retention review write permit demonstration stopped: ordinary CLI timed out") from error
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
            raise RuntimeError("Restore case retention review write permit demonstration stopped: invalid public CLI response") from error
        if discard:
            discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        return value

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> Any:
        with launch(command, action, arguments) as process:
            return collect(process, label, command, action, expected_error=expected_error, discard=discard)

    job = private / (PREFIX + "-job")
    review = private / (PREFIX + "-review")
    catalog = private / (PREFIX + "-catalog")
    ledger = private / (PREFIX + "-ledger")
    action_names = ("fill", "link", "finish")
    permit_names = ("fill", "old-link", "replacement-link", "finish")
    action_directories = {name: private / f"{PREFIX}-action-{index:02d}"
                          for index, name in enumerate(action_names, 1)}
    permit_directories = {name: private / f"{PREFIX}-permit-{index:02d}"
                          for index, name in enumerate(permit_names, 1)}
    credential_paths = {name: private / f"{PREFIX}-credential-{index:02d}.credential"
                        for index, name in enumerate(permit_names, 1)}
    permit_actions = {"fill": "fill", "old-link": "link", "replacement-link": "link", "finish": "finish"}
    actor = "simulated-permit-review-operator"
    worker = "simulated-permit-worker"
    principal = "simulated-permit-holder"
    unrelated_credential = sorted(private.glob("*.credential"))[0]
    prior_statuses = json.loads((workspace / "restore-case-retention-review-write-catalog-write-observations.json").read_text())["final_statuses"]
    prior_statuses["action"] = json.loads((workspace / "restore-case-retention-review-write-action-execution-observations.json").read_text())["statuses"]["restored"]
    prior_lease_pages = json.loads((workspace / "restore-case-retention-review-write-leases-pages.json").read_text())
    prior_jobs = {name: private / ("restore-case-retention-review-write-" + name) for name in ("minimum", "long")}
    prior_jobs["late"] = private / "restore-case-retention-review-write-catalog-late-job"
    prior_jobs["action"] = private / "restore-case-retention-review-write-action-job"
    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    original_paths = sorted(path for path in private.iterdir() if path.is_dir())
    original_controls = {path: controls(path) for path in original_paths}
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    actions: dict[str, Any] = {}
    grants: dict[str, Any] = {}
    revocations: dict[str, Any] = {}
    recorded: dict[str, Any] = {}
    protected: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    executions: dict[str, Any] = {}
    permit_observations: dict[str, Any] = {}
    lease_observations: dict[str, Any] = {}
    catalog_observations: dict[str, Any] = {}
    error_prefix = "workspace_restore_case_retention_review_write_permit_"
    credential_invalid, revoked = error_prefix + "credential_invalid", error_prefix + "revoked"
    conflict, invalid = error_prefix + "conflict", error_prefix + "invalid_input"
    lease_fields = ("lease_id", "generation", "worker", "lease_seconds", "claimed_at", "expires_at", "released_at")

    def originals_unchanged() -> bool:
        return (all(controls(path) == before for path, before in original_controls.items())
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == before
                        for index, before in old_observations.items()))

    def prepare_action(name: str, expected_state: str, claim: dict[str, Any]) -> dict[str, Any]:
        value = cli(name + "-action-prepare", ACTION_COMMAND, "prepare",
                    ["--action", str(action_directories[name]), "--ledger", str(ledger), "--index", "1",
                     "--worker", worker, "--lease-id", claim["lease_id"], "--generation", str(claim["generation"]),
                     "--operation-id", PREFIX + "-action-" + name, "--expected-state", expected_state])
        _require(value["before_state"] == expected_state and value["lease"] == claim
                 and value["write"]["write_id"] == statuses["prepared"]["write_id"],
                 "a prepared action lost its exact phase, write or captured claim")
        actions[name] = value
        return value

    def issue_args(name: str, *, first: bool = False, principal_label: str | None = None,
                   operation_id: str | None = None, action_name: str | None = None,
                   permit_directory: Path | None = None) -> list[str]:
        return ["--permit", str(permit_directories[name] if permit_directory is None else permit_directory),
                "--action", str(action_directories[permit_actions[name] if action_name is None else action_name]),
                "--principal", principal if principal_label is None else principal_label,
                "--operation-id", PREFIX + "-issue-" + name if operation_id is None else operation_id,
                "--credential-out" if first else "--prepared-credential", str(credential_paths[name])]

    def issue(name: str) -> dict[str, Any]:
        value = cli(name + "-permit-issue", COMMAND, "issue", issue_args(name, first=True))
        token = read_credential(credential_paths[name])
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
        public(value)
        _require(value["action"] == actions[permit_actions[name]] and value["principal"] == principal
                 and value["local_capability_only"] is True and value["external_identity_verified"] is False,
                 "a permit changed its explicit action scope or capability boundary")
        grants[name] = value
        return value

    def show(name: str, label: str) -> dict[str, Any]:
        before = controls(permit_directories[name])
        value = cli(label, COMMAND, "show", ["--permit", str(permit_directories[name])])
        _require(value["permit"] == grants[name] and controls(permit_directories[name]) == before,
                 "permit show changed its grant or private controls")
        permit_observations[label] = value
        return value

    def execute(name: str, *, label: str | None = None, credential_path: Path | None = None,
                expected_error: str | None = None, discard: bool = False) -> Any:
        label = name + "-protected-execute" if label is None else label
        action_name = permit_actions[name]
        watched = [permit_directories[name], action_directories[action_name], ledger, job, review]
        before = {path: controls(path) for path in watched if path.is_dir()}
        value = cli(label, COMMAND, "execute",
                    ["--permit", str(permit_directories[name]), "--credential-file",
                     str(credential_paths[name] if credential_path is None else credential_path)],
                    expected_error=expected_error, discard=discard)
        _require(controls(permit_directories[name]) == before[permit_directories[name]],
                 "protected execution changed its permit controls")
        if ledger in before:
            _require(controls(ledger) == before[ledger], "protected execution changed or renewed its lease")
        if expected_error is not None or action_name in recorded:
            _require(all(controls(path) == previous for path, previous in before.items()),
                     "a refusal or saved protected replay changed action, job or permit controls")
        if expected_error is None and not discard:
            _require(value["permit"] == grants[name] and value["action_result"]["action"] == actions[action_name]
                     and value["action_result"]["state"] == "recorded"
                     and value["credential_verified"] is True and value["revocation_checked"] is True
                     and value["historical_effect_authenticated"] is False,
                     "protected execution lost exact scope or overstated historical authentication")
            if action_name in recorded:
                _require(value["action_result"] == recorded[action_name], "the saved inner action result changed")
            if name in protected:
                _require(value == protected[name], "an active permit's complete saved response changed")
            recorded[action_name], protected[name] = value["action_result"], value
        executions[label] = value
        return value

    def revoke(name: str, *, label: str | None = None, credential_path: Path | None = None,
               operation_id: str | None = None, expected_error: str | None = None) -> Any:
        label = name + "-permit-revoke" if label is None else label
        watched = [action_directories[permit_actions[name]], ledger, job, review]
        before = {path: controls(path) for path in watched if path.is_dir()}
        permit_before = controls(permit_directories[name])
        value = cli(label, COMMAND, "revoke",
                    ["--permit", str(permit_directories[name]), "--operation-id",
                     PREFIX + "-revoke-" + name if operation_id is None else operation_id,
                     "--credential-file", str(credential_paths[name] if credential_path is None else credential_path)],
                    expected_error=expected_error)
        _require(all(controls(path) == previous for path, previous in before.items()),
                 "revocation changed action, lease or write controls")
        if expected_error is not None or name in revocations:
            _require(controls(permit_directories[name]) == permit_before, "a refusal or revoke replay changed its permit")
        if expected_error is None:
            _require(value["permit"] == grants[name] and value["state"] == "revoked", "revocation changed its grant")
            if name in revocations:
                _require(value == revocations[name], "exact revocation replay changed its saved marker")
            revocations[name] = value
        return value

    def status(label: str, expected_state: str) -> dict[str, Any]:
        value = cli(label + "-write-status", WRITE_COMMAND, "status", ["--job", str(job)])
        _require(value["state"] == expected_state and value["completed"] is (expected_state == "completed")
                 and value["intended_review"] == statuses["prepared"]["intended_review"],
                 "the write left its expected phase or changed intended content")
        statuses[label] = value
        return value

    def lease_page(label: str) -> dict[str, Any]:
        before = controls(ledger)
        value = cli(label, LEASE_COMMAND, "page", ["--ledger", str(ledger), "--limit", "16"])
        _require(controls(ledger) == before, "reading a lease page changed the ledger")
        pages[label] = value
        return value

    statuses["prepared"] = cli("fresh-write-prepare", WRITE_COMMAND, "prepare",
                               ["--plan", str(private / "restore-case-retention-minimum"), "--review", str(review),
                                "--job", str(job), "--operation-id", PREFIX + "-write", "--actor", actor, "--verdict", "defer"])
    _require(statuses["prepared"]["state"] == "prepared"
             and statuses["prepared"]["intended_review"]["actor"] == actor
             and statuses["prepared"]["intended_review"]["plan"] == prior_statuses["minimum"]["intended_review"]["plan"],
             "fresh write did not retain its exact historical minimum plan")
    catalog_receipt = cli("fresh-catalog-create", CATALOG_COMMAND, "create",
                          ["--catalog", str(catalog), "--job", str(job), "--operation-id", PREFIX + "-catalog"])
    ledger_receipt = cli("fresh-ledger-create", LEASE_COMMAND, "create",
                         ["--ledger", str(ledger), "--catalog", str(catalog), "--operation-id", PREFIX + "-ledger"])
    claim = cli("fresh-lease-claim", LEASE_COMMAND, "claim",
                ["--ledger", str(ledger), "--index", "1", "--worker", worker, "--lease-seconds", "3600",
                 "--operation-id", PREFIX + "-claim"])
    lease_observations.update({"ledger": ledger_receipt, "claim": claim})
    prepare_action("fill", "prepared", claim)
    issue("fill")
    fill_issue_replay = cli("fill-issue-exact-replay", COMMAND, "issue", issue_args("fill"))
    fill_initial_show = show("fill", "fill-initial-show")
    _require(fill_issue_replay == grants["fill"] and fill_initial_show["state"] == "active"
             and fill_initial_show["revocation"] is None and actor != worker and principal != actor,
             "initial permit replay or explicit self-declared labels changed")
    stage("exact-action-and-private-bearer-scope",
          "A fifth write job has an explicit catalog, one lease claim and one prepared-to-ready action. A private bearer permit captures exactly that action; exact issue replay returns its fixed grant and show observes an active permit without authenticating a holder.",
          {"write": statuses["prepared"], "catalog": catalog_receipt, "ledger": ledger_receipt,
           "claim": claim, "action": actions["fill"], "permit": grants["fill"],
           "issue_replay": fill_issue_replay, "show": fill_initial_show, "credentials_exported": False})

    wrong_fill_execute = execute("fill", label="fill-wrong-credential-execute", credential_path=unrelated_credential,
                                 expected_error=credential_invalid)
    wrong_fill_revoke = revoke("fill", label="fill-wrong-credential-revoke", credential_path=unrelated_credential,
                               expected_error=credential_invalid)
    concurrent_before = {path: controls(path) for path in (ledger, permit_directories["fill"])}
    processes: list[subprocess.Popen[str]] = []
    concurrent_results: list[Any] = []
    try:
        for _ in range(2):
            processes.append(launch(COMMAND, "execute", ["--permit", str(permit_directories["fill"]),
                                                        "--credential-file", str(credential_paths["fill"])]))
        for index, process in enumerate(processes, 1):
            concurrent_results.append(collect(process, f"fill-concurrent-protected-execute-{index}", COMMAND, "execute"))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=15)
    fill_result = concurrent_results[0]
    _require(fill_result == concurrent_results[1] and fill_result["permit"] == grants["fill"]
             and fill_result["action_result"]["action"] == actions["fill"]
             and fill_result["action_result"]["result"]["outcome"] == "phase_performed"
             and fill_result["action_result"]["result"]["effect_performed"] is True
             and fill_result["historical_effect_authenticated"] is False
             and all(controls(path) == before for path, before in concurrent_before.items()),
             "two protected callers did not share one saved effect without changing permit or lease")
    recorded["fill"], protected["fill"] = fill_result["action_result"], fill_result
    executions.update({f"fill-concurrent-protected-execute-{index}": value for index, value in enumerate(concurrent_results, 1)})
    ready_status = status("after-fill", "ready")
    fill_historic_show = cli("fill-recorded-action-show", ACTION_COMMAND, "show", ["--action", str(action_directories["fill"])])
    fill_protected_replay = execute("fill", label="fill-active-protected-replay")
    claimed_page = lease_page("claimed-lease-page")
    ready_catalog_page = cli("ready-catalog-page", CATALOG_COMMAND, "page", ["--catalog", str(catalog), "--limit", "16"])
    catalog_observations["ready"] = ready_catalog_page
    _require(fill_historic_show == recorded["fill"] and fill_protected_replay == fill_result
             and claimed_page["operation_count"] == 1 and claimed_page["entries"][0]["state"] == "active"
             and claimed_page["entries"][0]["current_lease"] == {key: claim[key] for key in lease_fields}
             and ready_catalog_page["entries"][0]["current_write"]["status"] == ready_status,
             "the recorded fill result changed or advanced beyond ready")
    stage("authenticated-delivery-and-one-saved-effect",
          "Wrong credentials refuse execute and revoke. Two ordinary protected execute processes are launched before collection for one permit and return the same complete response; the write reaches only ready. The response verifies current local capability delivery and explicitly declines historical effect authentication.",
          {"wrong_execute": wrong_fill_execute, "wrong_revoke": wrong_fill_revoke, "results": concurrent_results,
           "historic_action_show": fill_historic_show, "protected_replay": fill_protected_replay,
           "write_status": ready_status, "lease_page": claimed_page, "catalog_page": ready_catalog_page,
           "launched_before_collection_count": 2, "process_overlap_measured": False})

    fill_revocation = revoke("fill")
    fill_revoke_replay = revoke("fill", label="fill-revoke-exact-replay")
    fill_revoked_show = show("fill", "fill-revoked-show")
    fill_revoked_execute = execute("fill", label="fill-revoked-protected-replay", expected_error=revoked)
    fill_old_show = cli("fill-revoked-old-action-show", ACTION_COMMAND, "show", ["--action", str(action_directories["fill"])])
    fill_old_execute = cli("fill-revoked-old-action-execute", ACTION_COMMAND, "execute", ["--action", str(action_directories["fill"])])
    fill_revoked_issue_replay = cli("fill-revoked-issue-exact-replay", COMMAND, "issue", issue_args("fill"))
    fill_still_revoked = show("fill", "fill-still-revoked-show")
    _require(fill_revocation == fill_revoke_replay == fill_revoked_show == fill_still_revoked
             and fill_old_show == fill_old_execute == recorded["fill"] and fill_revoked_issue_replay == grants["fill"],
             "revocation changed history or issue replay reactivated the old permit")
    stage("revocation-blocks-protected-historical-replay",
          "The holder irreversibly revokes the fill permit. Protected replay refuses even though the result exists. Old action show/execute still return historical evidence; exact revoke replay is fixed and exact issue replay does not reactivate the permit.",
          {"revocation": fill_revocation, "revoke_replay": fill_revoke_replay, "show": fill_revoked_show,
           "protected_refusal": fill_revoked_execute, "old_action_show": fill_old_show,
           "old_action_execute": fill_old_execute, "issue_replay": fill_revoked_issue_replay,
           "after_issue_replay_show": fill_still_revoked, "old_direct_action_api_still_available": True})

    prepare_action("link", "ready", claim)
    issue("old-link")
    old_link_revocation = revoke("old-link")
    old_link_denied_before = execute("old-link", label="old-link-revoked-before-effect", expected_error=revoked)
    after_revoked_link_status = status("after-revoked-link", "ready")
    issue("replacement-link")
    link_result = execute("replacement-link")
    linked_status = status("after-link", "linked")
    old_link_denied_after = execute("old-link", label="old-link-still-revoked-after-replacement", expected_error=revoked)
    old_link_show = show("old-link", "old-link-revoked-show")
    replacement_show = show("replacement-link", "replacement-link-active-show")
    replacement_wrong_revoke = revoke("replacement-link", label="replacement-link-wrong-credential-revoke",
                                      credential_path=unrelated_credential, expected_error=credential_invalid)
    _require(after_revoked_link_status == ready_status and old_link_show == old_link_revocation
             and grants["old-link"]["action"] == grants["replacement-link"]["action"] == actions["link"]
             and grants["old-link"]["permit_id"] != grants["replacement-link"]["permit_id"]
             and replacement_show["state"] == "active"
             and link_result["action_result"]["result"]["outcome"] == "phase_performed"
             and link_result["action_result"]["result"]["effect_performed"] is True,
             "a replacement permit revived the revoked permit or changed action scope")
    stage("independent-replacement-does-not-revive-revocation",
          "A second permit is revoked before its link action runs, leaving the write ready. A new independent permit for the same exact action performs ready-to-linked. The original permit still refuses; replacement does not renew or reactivate it and its principal remains a self-declared label.",
          {"action": actions["link"], "old_grant": grants["old-link"], "old_revocation": old_link_revocation,
           "before_replacement_refusal": old_link_denied_before, "before_replacement_status": after_revoked_link_status,
           "replacement_grant": grants["replacement-link"], "replacement_execution": link_result,
           "write_status": linked_status, "after_replacement_refusal": old_link_denied_after,
           "old_show": old_link_show, "replacement_show": replacement_show,
           "wrong_revoke": replacement_wrong_revoke, "revoked_permit_reactivated": False})

    prepare_action("finish", "linked", claim)
    issue("finish")
    wrong_finish_execute = execute("finish", label="finish-other-permit-credential-execute",
                                   credential_path=credential_paths["fill"], expected_error=credential_invalid)
    detached: list[tuple[Path, Path]] = []
    offline_action_shows: dict[str, Any] = {}
    offline_permit_shows: dict[str, Any] = {}
    offline_issue_replays: dict[str, Any] = {}
    try:
        held_catalog = private / (PREFIX + "-offline-catalog")
        catalog.rename(held_catalog)
        detached.append((catalog, held_catalog))
        execute("finish", label="finish-protected-response-discarded", discard=True)
        recovery_before = {path: controls(path) for path in (ledger, job, review, action_directories["finish"], permit_directories["finish"])}
        finished_result = execute("finish", label="finish-protected-response-recovered")
        recovered = (discarded_addresses.pop("finish-protected-response-discarded")
                     == hashlib.sha256(canonical(finished_result).encode("utf-8")).hexdigest())
        _require(recovered and all(controls(path) == before for path, before in recovery_before.items())
                 and finished_result["action_result"]["result"]["outcome"] == "phase_performed"
                 and finished_result["action_result"]["result"]["effect_performed"] is True,
                 "the actually discarded complete protected response was not recovered exactly")
        completed_status = status("after-finish", "completed")
        release = cli("fresh-lease-release", LEASE_COMMAND, "release",
                      ["--ledger", str(ledger), "--index", "1", "--worker", worker,
                       "--lease-id", claim["lease_id"], "--generation", str(claim["generation"]),
                       "--operation-id", PREFIX + "-release"])
        released_page = lease_page("released-lease-page")
        lease_observations["release"] = release
        for name, original in (("ledger", ledger), ("job", job), ("review", review)):
            held = private / (PREFIX + "-offline-" + name)
            original.rename(held)
            detached.append((original, held))
        offline_action_controls = {path: controls(path) for path in action_directories.values()}
        offline_active_replay = execute("replacement-link", label="offline-active-replacement-protected-replay")
        for name in action_names:
            value = cli("offline-" + name + "-old-action-show", ACTION_COMMAND, "show", ["--action", str(action_directories[name])])
            _require(value == recorded[name], "historical action show changed with unavailable bindings")
            offline_action_shows[name] = value
        _require(offline_active_replay == link_result
                 and all(controls(path) == before for path, before in offline_action_controls.items()),
                 "active cached protected replay changed controls or depended on current bindings")
        for name in action_names:
            original = action_directories[name]
            held = private / (PREFIX + "-offline-action-" + name)
            original.rename(held)
            detached.append((original, held))
        for name in permit_names:
            offline_permit_shows[name] = show(name, "offline-" + name + "-permit-show")
        for name in ("fill", "replacement-link"):
            value = cli("offline-" + name + "-issue-exact-replay", COMMAND, "issue", issue_args(name))
            _require(value == grants[name], "own-only exact issue replay inspected an absent action")
            offline_issue_replays[name] = value
        offline_fill_denial = execute("fill", label="offline-revoked-fill-before-missing-action", expected_error=revoked)
        finish_revocation = revoke("finish", label="offline-finish-permit-revoke")
        offline_finish_denial = execute("finish", label="offline-revoked-finish-before-missing-action", expected_error=revoked)
        offline_finish_show = show("finish", "offline-finish-revoked-show")
        offline_finish_revoke_replay = revoke("finish", label="offline-finish-revoke-exact-replay")
        _require(offline_finish_show == offline_finish_revoke_replay == finish_revocation,
                 "own-only revocation replay changed its fixed record")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    _require(len(detached) == 7 and released_page["operation_count"] == 2
             and released_page["entries"][0]["state"] == "released"
             and released_page["entries"][0]["current_lease"] == {key: release[key] for key in lease_fields},
             "the fresh ledger did not retain exactly claim/release or detached paths were lost")
    restored_catalog = cli("restored-catalog-show", CATALOG_COMMAND, "show", ["--catalog", str(catalog)])
    restored_status = status("restored", "completed")
    written_review = cli("written-review-old-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    _require(restored_catalog == catalog_receipt and restored_status == completed_status
             and written_review == statuses["prepared"]["intended_review"] and originals_unchanged(),
             "completion or offline checks changed exact review content or original controls")
    stage("whole-protected-reply-recovery-and-offline-revocation",
          "Finish completes with its captured catalog absent; the actual complete protected reply is discarded and recovered exactly. After lease release and hiding four bindings, active replacement replay checks its bearer and returns saved history. Hiding all three actions raises the total to seven absent paths: own-only show/issue replay and finish revoke still work, while revoked protected execute refuses before missing-action access. All paths return in finally.",
          {"wrong_finish_credential": wrong_finish_execute, "finish": finished_result, "write_status": completed_status,
           "released_lease": release, "lease_page": released_page, "offline_active_replay": offline_active_replay,
           "offline_action_shows": offline_action_shows, "offline_permit_shows": offline_permit_shows,
           "offline_issue_replays": offline_issue_replays, "offline_fill_denial": offline_fill_denial,
           "offline_finish_revocation": finish_revocation, "offline_finish_denial": offline_finish_denial,
           "offline_finish_show": offline_finish_show, "offline_finish_revoke_replay": offline_finish_revoke_replay,
           "restored_catalog": restored_catalog, "restored_status": restored_status, "review": written_review,
           "actual_complete_protected_reply_recovered_exactly": recovered, "maximum_detached_path_count": 7,
           "all_detached_paths_restored": True, "comparison_digest_exported": False,
           "cached_protected_delivery_checks_current_credential_and_revocation": True,
           "historical_effect_authenticated": False})

    for label, arguments in (
        ("issue-replay-other-principal", issue_args("fill", principal_label="simulated-other-holder")),
        ("issue-replay-other-action", issue_args("fill", action_name="link")),
        ("issue-replay-other-operation", issue_args("fill", operation_id=PREFIX + "-other-issue")),
    ):
        cli(label, COMMAND, "issue", arguments, expected_error=conflict)
    revoke("fill", label="revoke-replay-other-operation", operation_id=PREFIX + "-other-revoke", expected_error=conflict)
    invalid_paths = []
    for label, overrides in (
        ("issue-malformed-principal", {"principal_label": "invalid/principal"}),
        ("issue-malformed-operation", {"operation_id": "invalid/operation"}),
        ("issue-overlong-operation", {"operation_id": "x" * 102}),
    ):
        path = private / (PREFIX + "-" + label)
        invalid_paths.append(path)
        cli(label, COMMAND, "issue", issue_args("fill", permit_directory=path, **overrides), expected_error=invalid)
    _require(all(not path.exists() for path in invalid_paths), "invalid issue reserved a fresh permit directory")
    previous_statuses = {name: cli("original-" + name + "-write-status", WRITE_COMMAND, "status", ["--job", str(path)])
                         for name, path in prior_jobs.items()}
    original_lease_pages = {}
    for name in ("primary", "secondary"):
        value = cli("original-" + name + "-lease-page", LEASE_COMMAND, "page",
                    ["--ledger", str(private / ("restore-case-retention-review-write-leases-" + name)), "--limit", "16"])
        previous = prior_lease_pages[name + "-final-released-page"]
        _require({key: child for key, child in value.items() if key != "checked_at"}
                 == {key: child for key, child in previous.items() if key != "checked_at"},
                 "an earlier ledger changed its membership or released head")
        original_lease_pages[name] = value
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    final_catalog_page = cli("completed-catalog-page", CATALOG_COMMAND, "page", ["--catalog", str(catalog), "--limit", "16"])
    final_lease_page = lease_page("final-released-lease-page")
    final_permit_shows = {name: show(name, "final-" + name + "-permit-show") for name in permit_names}
    catalog_observations["completed"] = final_catalog_page
    source_after, live_after = observe(source), observe(live)
    permit_outcomes = [value for value in cli_outcomes if value["command"] == COMMAND]
    _require(previous_statuses == prior_statuses and originals_unchanged()
             and matches(source_before, source_after) and matches(live_before, live_after)
             and len(actions) == len(recorded) == 3 and len(grants) == 4 and len(revocations) == 3
             and set(revocations) == {"fill", "old-link", "finish"}
             and all(value["result"]["outcome"] == "phase_performed" and value["result"]["effect_performed"] is True
                     for value in recorded.values())
             and all(final_permit_shows[name] == revocations[name] for name in revocations)
             and final_permit_shows["replacement-link"]["state"] == "active"
             and final_catalog_page["entries"][0]["current_write"]["status"] == completed_status
             and final_lease_page["operation_count"] == 2 and final_lease_page["entries"] == released_page["entries"]
             and len(cli_outcomes) == 85 and len(rejections) == 19
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 66
             and len(permit_outcomes) == 50 and sum(value["returncode"] == 0 for value in permit_outcomes) == 34
             and len(pages) == 3 and not discarded_addresses,
             "final permit, action, phase, process or unchanged-history accounting differs from the fixed scenario")
    stage("exact-inputs-and-unchanged-domain-history",
          "Changed exact requests and malformed identifiers refuse without creating new permits. Four earlier write jobs, earlier released lease heads and three blocked restore jobs preserve their observations. Four grants retain three irreversible revocations; three fresh actions retain exactly three performed phases and the replacement permit stays active over historical evidence.",
          {"rejections": rejections, "grants": grants, "revocations": revocations, "recorded": recorded,
           "final_permit_shows": final_permit_shows, "original_write_statuses": previous_statuses,
           "original_lease_pages": original_lease_pages, "catalog_page": final_catalog_page, "lease_page": final_lease_page,
           "original_control_metadata_unchanged": True, "original_blocked_job_metadata_unchanged": True,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "actor_authenticated": False, "worker_authenticated": False, "external_identity_verified": False,
           "domain_writes_allowed": False, "archival_performed": False, "deletion_performed": False})

    artifacts = {
        PREFIX + "-grants.json": grants,
        PREFIX + "-revocations.json": revocations,
        PREFIX + "-action-observations.json": {"actions": actions, "recorded": recorded},
        PREFIX + "-execution-observations.json": {
            "executions": executions, "protected": protected, "statuses": statuses, "concurrent_results": concurrent_results,
            "permits": permit_observations, "final_permit_shows": final_permit_shows,
            "catalogs": {"receipt": catalog_receipt, "restored": restored_catalog, **catalog_observations},
            "written_review": written_review, "offline_active_replay": offline_active_replay,
            "offline_action_shows": offline_action_shows, "offline_permit_shows": offline_permit_shows,
            "offline_issue_replays": offline_issue_replays, "original_control_metadata_unchanged": True,
            "original_directory_times_compared": False, "old_blocked_job_metadata_unchanged": True,
            "all_detached_paths_restored": True, "maximum_detached_path_count": 7,
            "new_lease_control_metadata_unchanged_by_permits": True,
            "private_control_contents_read_by_demo": False, "private_action_result_contents_read_by_demo": False,
            "private_lease_database_read_by_demo": False, "private_stage_contents_read_by_demo": False,
            "direct_step_used_for_new_write": False,
        },
        PREFIX + "-lease-observations.json": {**lease_observations, "pages": pages, "original_final_pages": original_lease_pages},
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {"actual_complete_protected_finish_response_discarded": True,
                                          "exact_complete_protected_reply_recovered": recovered,
                                          "every_protected_replay_checks_current_credential_and_revocation": True,
                                          "saved_inner_action_result_requires_current_lease": False,
                                          "historical_effect_authenticated": False, "comparison_digest_exported": False},
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "public artifact accounting differs from six stages and ten data files")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Exact local bearer permits and irreversible revocation for historical write actions",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "permit_stage_count": len(stages),
            "new_permit_count": 4, "revoked_permit_count": 3, "active_permit_count": 1,
            "new_private_credential_file_count": 4, "credentials_exported": False,
            "new_action_count": 3, "recorded_action_count": 3, "pending_action_count": 0,
            "total_action_count": 9, "total_recorded_action_count": 7, "total_pending_action_count": 2,
            "performed_phase_count": 3, "expected_phase_observation_count": 0,
            "new_write_job_count": 1, "total_write_job_count": 5,
            "new_review_record_count": 1, "total_review_record_count": 7,
            "new_write_catalog_count": 1, "total_write_catalog_count": 4,
            "new_lease_ledger_count": 1, "total_lease_ledger_count": 4,
            "new_lease_operation_count": 2, "total_lease_operation_count": 18,
            "new_ledger_page_response_count": len(pages), "prior_ledger_compatibility_page_count": 2,
            "lease_page_response_count": len(pages) + 2, "new_catalog_page_response_count": 2,
            "new_cli_child_process_count": len(cli_outcomes), "new_successful_cli_count": 66,
            "new_refused_cli_count": len(rejections), "permit_command_count": len(permit_outcomes),
            "permit_successful_command_count": 34, "permit_refused_command_count": 16,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "concurrent_same_permit_caller_count": 2, "concurrent_complete_responses_equal": True,
            "single_phase_after_concurrent_calls": "ready", "process_overlap_measured": False,
            "discarded_complete_finish_response_count": 1, "discarded_complete_finish_response_recovered_exactly": recovered,
            "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0, "injected_partial_write_count": 0,
            "maximum_offline_path_count": 7, "all_detached_paths_restored": True,
            "completion_without_captured_catalog": True, "active_cached_protected_replay_without_live_bindings": True,
            "own_only_permit_show_issue_replay_and_revoke_without_action": True,
            "revoked_protected_replay_rejected_before_missing_action": True,
            "new_permit_revives_revoked_permit": False, "issue_replay_reactivates_revoked_permit": False,
            "every_protected_replay_checks_current_credential_and_revocation": True,
            "historical_effect_authenticated": False, "old_direct_action_api_still_available": True,
            "new_lease_control_metadata_unchanged_by_permits": True,
            "original_control_metadata_unchanged": True, "old_blocked_job_metadata_unchanged": True,
            "original_blocked_jobs_still_blocked": True,
            "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
            "jobs_advanced": True, "direct_step_used_for_new_write": False,
            "lease_required_for_new_execution": True, "job_steps_enforced": False,
            "local_capability_only": True, "external_identity_verified": False, "permit_has_expiry": False,
            "new_restore_attempt_count": 0, "domain_writes_allowed": False,
            "archival_performed": False, "deletion_performed": False,
            "expiry_path_demonstrated": False, "clock_regression_path_demonstrated": False,
            "interruption_recovery_demonstrated": False, "partial_result_repair_demonstrated": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "actor_authenticated": False, "worker_authenticated": False, "old_authority_activated": False,
            "external_fencing_established": False, "external_llm_used": False, "human_review_recorded": False,
            "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "A permit is a private local bearer capability for one exact historical action; its principal is a self-declared label and the filesystem owner remains the issuer. No person, external identity, semantic approval or domain authority is established.",
            "One bearer executes and may revoke its own permit. Revocation is irreversible through this API and has no expiry. A new permit for the same action is independent and never reactivates an earlier revoked permit.",
            "Every protected execute checks the supplied credential and current own revocation state before action or cached-result access. The full response is stable for exact reply recovery, but historical_effect_authenticated remains false: authenticated delivery does not establish who caused an earlier effect.",
            "Old action show/execute still expose historical evidence without this bearer gate; direct write step, filesystem ownership and other ledgers remain outside permit admission. job_steps_enforced is false and no external fencing or common SQL/filesystem transaction exists.",
            "Two callers launch before collection and share one exact saved response; this does not measure process overlap, maximum agents, useful throughput, independent models or semantic correctness.",
            "The actual complete finish response is discarded after a successful ordinary command and recovered exactly. No interruption, partial-result repair, power loss, clock regression or expiry waiting is demonstrated.",
            "Completion needs no captured catalog. Cached protected replay still authenticates an active bearer after lease release with four bindings absent. Permit own-only show, exact issue replay and revoke need no action, and revoked execute refuses before seven missing bindings are accessed. Every detached path returns in finally.",
            "The driver reads validated public responses, private credential markers and lstat metadata. It never reads private control bodies, action results, lease SQL rows or staging contents; tokens, credential hashes and control digests stay private.",
            "The earlier source/live histories remain R4/36 and R5/59, with previous quarantine, origin and blocked restore cases. New controls neither archive nor delete content and do not activate old authority.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Допуск к точному действию по локальному bearer и необратимый отзыв", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий долговечных действий](restore-case-retention-review-write-action-report.md); его 413 публичных файлов сохранены.",
        "Добавлены пятый write job, седьмой review, четвёртые каталог и ledger. Три новых action проходят prepared→ready→linked→completed с одной captured арендой; direct step для них не используется.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "Четыре private bearer разрешения привязаны к трём точным действиям. Чужой credential отказывает. Два ordinary protected execute для fill возвращают одинаковый полный ответ, а job достигает только ready. Перекрытие выполнения и ёмкость не измерялись.",
        "Три разрешения отозваны: fill после эффекта, первое link до эффекта, finish после сохранения результата. Второе независимое link разрешение остаётся active над историческим результатом; прежнее отозванное разрешение не оживает.",
        "Protected execute проверяет credential и текущий отзыв до чтения cached action result. Поэтому отозванный permit не возвращает защищённый повтор, хотя прежний action show/execute по-прежнему возвращает историческую квитанцию. Exact issue replay не меняет отзыв.",
        "Настоящий полный ответ finish отброшен и точно восстановлен при недоступном исходном каталоге. После release активный protected replay с четырьмя отсутствующими bindings читает сохранённый результат и заново проверяет bearer. При семи отсутствующих путях own-only permit show, exact issue replay и revoke работают, а revoked execute отказывает до доступа к action. Все пути возвращены.",
        "Поле historical_effect_authenticated=false сохраняется во всех защищённых ответах. Проверка права получить ответ сейчас не устанавливает автора уже совершённого эффекта; principal остаётся самозаявленным.", "",
        "## Публичные основания", "",
        f"- [Четыре grants]({PREFIX}-grants.json), [три отзыва]({PREFIX}-revocations.json), [три действия и результата]({PREFIX}-action-observations.json).",
        f"- [Фазы, разрешения и offline наблюдения]({PREFIX}-execution-observations.json), [аренда]({PREFIX}-lease-observations.json).",
        f"- [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json), [восстановление ответа]({PREFIX}-reply-recovery.json).",
        f"- [Источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы", "",
        "Шесть этапов, 85 новых CLI: 66 успешных и 19 ожидаемых отказов. Из них 50 permit команд: 34 успешных и 16 отказов. С основанием 672 ordinary CLI и 14 прежних interrupt workers, всего 686 дочерних процессов.",
        "Всего девять action intents, семь saved results и два прежних pending intents; пять write jobs, семь reviews, четыре каталога, четыре lease DB и 18 lease событий. Новая аренда содержит только claim и release; проверки permit её не продлевают.",
        "Один локальный bearer исполняет и отзывает только своё разрешение; срока истечения нет. Файловый владелец выдаёт разрешения. Старые action API, direct step, прямой SQL и файловый владелец остаются за этой границей; общего SQL/filesystem commit, внешнего fencing или проверки личности нет.",
        "Private credentials и их hashes, управляющие записи, pins и digests не экспортируются. Новых fault workers, injected partial writes, ожидания expiry, внешних LLM либо запуска предложенного кода нет; смысловая достоверность документации этой демонстрацией не устанавливается.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
