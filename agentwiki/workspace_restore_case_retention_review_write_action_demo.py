"""Demonstrate durable lease-checked actions without activating domain authority.

The driver invokes ordinary public CLI commands. It reads validated public
responses, credential markers and lstat metadata, but never private control
bodies, lease SQL rows or staging contents. The preceding development scenario
is preserved; no new fault worker, injected error or expiry wait is introduced.
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
from .workspace_restore_case_retention_review_write_leases_demo import run_workspace_restore_case_retention_review_write_leases_demo
from .workspace_restore_case_retention_review_write_action_cli import _public_response as _action_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-action-1"
COMMAND = "workspace-restore-case-retention-review-write-action"
PREFIX = "restore-case-retention-review-write-action"
CATALOG_COMMAND = "workspace-restore-case-retention-review-write-catalog"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"
LEASE_COMMAND = "workspace-restore-case-retention-review-write-leases"
REVIEW_COMMAND = "workspace-restore-case-retention-review"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write action demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_action_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Prepare one new review write and perform bounded lease-checked actions."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_leases_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 395, "the preceding scenario did not produce 395 public files")
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
    }

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not forbidden.intersection(value), "private metadata reached public output")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (tuple, list)):
            return [public(child) for child in value]
        if isinstance(value, str):
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
        _require(command in {COMMAND, CATALOG_COMMAND, WRITE_COMMAND, LEASE_COMMAND, REVIEW_COMMAND, "workspace-restore"},
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
            raise RuntimeError("Restore case retention review write action demonstration stopped: ordinary CLI timed out") from error
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
            raise RuntimeError("Restore case retention review write action demonstration stopped: invalid public CLI response") from error
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
    names = ("fill", "stale", "link", "observe", "beyond", "finish")
    lease_fields = ("lease_id", "generation", "worker", "lease_seconds", "claimed_at", "expires_at", "released_at")
    action_directories = {name: private / f"{PREFIX}-action-{index:02d}"
                          for index, name in enumerate(names, 1)}
    action_operations = {name: PREFIX + "-" + name for name in names}
    write_operation = PREFIX + "-write"
    catalog_operation = PREFIX + "-catalog"
    ledger_operation = PREFIX + "-ledger"
    actor = "simulated-action-review-operator"
    verdict = "defer"
    workers_by_generation = {1: "simulated-action-worker-a", 2: "simulated-action-worker-b"}
    prior_statuses = json.loads((workspace / "restore-case-retention-review-write-catalog-write-observations.json").read_text())["final_statuses"]
    prior_lease_pages = json.loads((workspace / "restore-case-retention-review-write-leases-pages.json").read_text())
    prior_jobs = {name: private / ("restore-case-retention-review-write-" + name) for name in ("minimum", "long")}
    prior_jobs["late"] = private / "restore-case-retention-review-write-catalog-late-job"
    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    original_paths = sorted(path for path in private.iterdir() if path.is_dir())
    original_controls = {path: controls(path) for path in original_paths}
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    actions: dict[str, Any] = {}
    recorded: dict[str, Any] = {}
    pending: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    executions: dict[str, Any] = {}
    lease_observations: dict[str, Any] = {}
    catalog_observations: dict[str, Any] = {}
    invalid = "workspace_restore_case_retention_review_write_action_invalid_input"
    conflict = "workspace_restore_case_retention_review_write_action_conflict"
    lease_changed = "workspace_restore_case_retention_review_write_action_lease_changed"
    phase_changed = "workspace_restore_case_retention_review_write_action_phase_changed"

    def originals_unchanged() -> bool:
        return (all(controls(path) == before for path, before in original_controls.items())
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == before
                        for index, before in old_observations.items()))

    def prepare_args(name: str, expected_state: str, lease: dict[str, Any], *,
                     operation_id: str | None = None, action_directory: Path | None = None,
                     index: int = 1, worker: str | None = None, lease_id: str | None = None,
                     generation: int | None = None) -> list[str]:
        return ["--action", str(action_directories[name] if action_directory is None else action_directory),
                "--ledger", str(ledger), "--index", str(index),
                "--worker", lease["worker"] if worker is None else worker,
                "--lease-id", lease["lease_id"] if lease_id is None else lease_id,
                "--generation", str(lease["generation"] if generation is None else generation),
                "--operation-id", action_operations[name] if operation_id is None else operation_id,
                "--expected-state", expected_state]

    def prepare_action(name: str, expected_state: str, lease: dict[str, Any]) -> dict[str, Any]:
        value = cli(name + "-action-prepare", COMMAND, "prepare", prepare_args(name, expected_state, lease))
        _require(value["before_state"] == expected_state and value["lease"] == lease
                 and value["write"]["write_id"] == statuses["prepared"]["write_id"]
                 and value["lease_required_for_new_execution"] is True and value["job_steps_enforced"] is False,
                 "an action changed its explicit phase, captured claim or guard boundary")
        actions[name] = value
        return value

    def execute_action(name: str, *, label: str | None = None, expected_error: str | None = None,
                       discard: bool = False) -> Any:
        label = name + "-action-execute" if label is None else label
        before = {"ledger": controls(ledger), "job": controls(job), "review": controls(review),
                  "action": controls(action_directories[name])}
        value = cli(label, COMMAND, "execute", ["--action", str(action_directories[name])],
                    expected_error=expected_error, discard=discard)
        _require(controls(ledger) == before["ledger"], "an action changed or renewed its lease ledger")
        if expected_error is not None or name in recorded:
            _require(controls(job) == before["job"] and controls(review) == before["review"]
                     and controls(action_directories[name]) == before["action"],
                     "an expected refusal or saved replay changed action/job controls")
        if expected_error is None and not discard:
            _require(value["action"] == actions[name] and value["state"] == "recorded"
                     and value["result"]["observed_state"] == actions[name]["target_state"],
                     "execution lost its fixed action or target phase")
            if name in recorded:
                _require(value == recorded[name], "a saved action replay changed its historical result")
            recorded[name] = value
        executions[label] = value
        return value

    def status(label: str, expected_state: str) -> dict[str, Any]:
        value = cli(label + "-write-status", WRITE_COMMAND, "status", ["--job", str(job)])
        _require(value["state"] == expected_state
                 and value["intended_review"] == statuses["prepared"]["intended_review"]
                 and value["completed"] is (expected_state == "completed"),
                 "the current write phase differs from the single-phase action")
        statuses[label] = value
        return value

    def lease_page(label: str) -> dict[str, Any]:
        before = controls(ledger)
        value = cli(label, LEASE_COMMAND, "page", ["--ledger", str(ledger), "--limit", "16"])
        _require(controls(ledger) == before, "a lease page changed its ledger")
        pages[label] = value
        return value

    def release_args(claim: dict[str, Any], operation: str) -> list[str]:
        return ["--ledger", str(ledger), "--index", "1", "--worker", claim["worker"],
                "--lease-id", claim["lease_id"], "--generation", str(claim["generation"]), "--operation-id", operation]

    plan = private / "restore-case-retention-minimum"
    statuses["prepared"] = cli("fresh-write-prepare", WRITE_COMMAND, "prepare",
                               ["--plan", str(plan), "--review", str(review), "--job", str(job),
                                "--operation-id", write_operation, "--actor", actor, "--verdict", verdict])
    _require(statuses["prepared"]["state"] == "prepared"
             and statuses["prepared"]["operation_id"] == write_operation
             and statuses["prepared"]["intended_review"]["actor"] == actor
             and statuses["prepared"]["intended_review"]["verdict"] == verdict
             and statuses["prepared"]["intended_review"]["plan"] == prior_statuses["minimum"]["intended_review"]["plan"],
             "fresh preparation lost the exact historical minimum plan")
    catalog_receipt = cli("fresh-catalog-create", CATALOG_COMMAND, "create",
                          ["--catalog", str(catalog), "--job", str(job), "--operation-id", catalog_operation])
    ledger_receipt = cli("fresh-ledger-create", LEASE_COMMAND, "create",
                         ["--ledger", str(ledger), "--catalog", str(catalog), "--operation-id", ledger_operation])
    claim_one = cli("generation-one-claim", LEASE_COMMAND, "claim",
                    ["--ledger", str(ledger), "--index", "1", "--worker", workers_by_generation[1],
                     "--lease-seconds", "3600", "--operation-id", PREFIX + "-claim-one"])
    lease_observations.update({"ledger": ledger_receipt, "claim_one": claim_one})
    prepare_action("fill", "prepared", claim_one)
    initial_show = cli("fill-initial-show", COMMAND, "show", ["--action", str(action_directories["fill"])])
    initial_replay = cli("fill-prepare-exact-replay", COMMAND, "prepare", prepare_args("fill", "prepared", claim_one))
    _require(initial_show["action"] == actions["fill"] and initial_show["state"] == "prepared"
             and initial_show["result"] is None and initial_replay == actions["fill"],
             "initial action show or exact prepare replay changed its fixed receipt")
    stage("explicit-claim-and-single-phase-intent",
          "One fresh write job, its one-member explicit catalog and a new lease ledger are prepared. A generation-1 claim is captured in an immutable prepared-to-ready action; show remains a read and exact prepare replay returns its fixed receipt.",
          {"write": statuses["prepared"], "catalog": catalog_receipt, "ledger": ledger_receipt,
           "claim": claim_one, "action": actions["fill"], "show": initial_show, "prepare_replay": initial_replay})

    before_concurrent_ledger = controls(ledger)
    processes: list[subprocess.Popen[str]] = []
    concurrent_results = []
    try:
        for _ in range(2):
            processes.append(launch(COMMAND, "execute", ["--action", str(action_directories["fill"])]))
        for index, process in enumerate(processes, 1):
            concurrent_results.append(collect(process, f"fill-concurrent-execute-{index}", COMMAND, "execute"))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=15)
    _require(concurrent_results[0] == concurrent_results[1]
             and concurrent_results[0]["action"] == actions["fill"]
             and concurrent_results[0]["state"] == "recorded"
             and concurrent_results[0]["result"]["outcome"] == "phase_performed"
             and concurrent_results[0]["result"]["effect_performed"] is True
             and controls(ledger) == before_concurrent_ledger,
             "same-action callers did not recover one saved phase result without lease renewal")
    recorded["fill"] = concurrent_results[0]
    executions.update({f"fill-concurrent-execute-{index}": value for index, value in enumerate(concurrent_results, 1)})
    ready_status = status("after-fill", "ready")
    fill_show = cli("fill-recorded-show", COMMAND, "show", ["--action", str(action_directories["fill"])])
    fill_replay = execute_action("fill", label="fill-saved-execute-replay")
    claimed_page = lease_page("generation-one-lease-page")
    ready_catalog_page = cli("ready-catalog-page", CATALOG_COMMAND, "page", ["--catalog", str(catalog), "--limit", "16"])
    catalog_observations["ready"] = ready_catalog_page
    _require(fill_show == fill_replay == recorded["fill"]
             and claimed_page["operation_count"] == 1 and claimed_page["entries"][0]["state"] == "active"
             and claimed_page["entries"][0]["current_lease"] == {key: claim_one[key] for key in lease_fields}
             and claim_one["worker"] != actor
             and ready_catalog_page["entries"][0]["current_write"]["status"] == ready_status,
             "historical action replay advanced the job or changed the active claim")
    stage("same-action-callers-share-one-recorded-effect",
          "Two ordinary execute processes are launched before collection for the same action. Both return the exact same saved result and the write reaches only ready. Separate show and execute replays preserve that result; lease history remains at one event.",
          {"action": actions["fill"], "results": concurrent_results, "show": fill_show, "replay": fill_replay,
           "write_status": ready_status, "lease_page": claimed_page, "catalog_page": ready_catalog_page,
           "launched_before_collection_count": 2, "process_overlap_measured": False,
           "single_phase_reached": "ready", "lease_renewed": False})

    prepare_action("stale", "ready", claim_one)
    release_one = cli("generation-one-release", LEASE_COMMAND, "release", release_args(claim_one, PREFIX + "-release-one"))
    claim_two = cli("generation-two-claim", LEASE_COMMAND, "claim",
                    ["--ledger", str(ledger), "--index", "1", "--worker", workers_by_generation[2],
                     "--lease-seconds", "3600", "--operation-id", PREFIX + "-claim-two"])
    stale_refusal = execute_action("stale", expected_error=lease_changed)
    pending["stale"] = cli("stale-pending-show", COMMAND, "show", ["--action", str(action_directories["stale"])])
    stale_status = status("after-stale-refusal", "ready")
    generation_two_page = lease_page("generation-two-lease-page")
    lease_observations.update({"release_one": release_one, "claim_two": claim_two})
    _require(claim_one["generation"] == 1 and claim_two["generation"] == 2
             and claim_two["lease_id"] != claim_one["lease_id"]
             and pending["stale"]["state"] == "prepared" and pending["stale"]["result"] is None
             and stale_status == ready_status and generation_two_page["operation_count"] == 3
             and generation_two_page["entries"][0]["current_lease"] == {key: claim_two[key] for key in lease_fields},
             "stale generation obtained a phase result or modified the latest attempt")
    stage("new-generation-rejects-the-old-pending-action",
          "A ready-to-linked action captures generation 1 before release. A fresh claim receives generation 2. Executing the old pending action refuses lease_changed before any phase effect; its own show remains prepared and the job stays ready.",
          {"old_action": actions["stale"], "release": release_one, "new_claim": claim_two,
           "refusal": stale_refusal, "pending_show": pending["stale"], "write_status": stale_status,
           "lease_page": generation_two_page})

    for name in ("link", "observe", "beyond"):
        prepare_action(name, "ready", claim_two)
    linked_result = execute_action("link")
    linked_status = status("after-link", "linked")
    observed_result = execute_action("observe")
    observed_linked_status = status("after-post-observation", "linked")
    prepare_action("finish", "linked", claim_two)
    _require(linked_result["result"]["outcome"] == "phase_performed"
             and linked_result["result"]["effect_performed"] is True
             and observed_result["result"]["outcome"] == "expected_phase_observed"
             and observed_result["result"]["effect_performed"] is False
             and observed_linked_status == linked_status,
             "observing the expected post phase performed another effect or claimed causal ownership")
    stage("performed-phase-and-separate-post-observation",
          "Three generation-2 actions capture the same ready phase. One performs ready-to-linked. A second observes that exact post phase and saves expected_phase_observed with effect_performed=false; the job remains linked. The third stays pending for the later beyond-post refusal.",
          {"actions": {name: actions[name] for name in ("link", "observe", "beyond", "finish")},
           "performed": linked_result, "observed": observed_result, "linked_status": linked_status,
           "after_observation_status": observed_linked_status, "direct_step_used": False})

    detached: list[tuple[Path, Path]] = []
    offline_records: dict[str, Any] = {}
    offline_pending: dict[str, Any] = {}
    try:
        held_catalog = private / (PREFIX + "-offline-catalog")
        catalog.rename(held_catalog)
        detached.append((catalog, held_catalog))
        execute_action("finish", label="finish-response-discarded", discard=True)
        recovery_before = {path: controls(path) for path in (ledger, job, review, action_directories["finish"])}
        finished_result = execute_action("finish", label="finish-response-recovered")
        recovered = (discarded_addresses.pop("finish-response-discarded")
                     == hashlib.sha256(canonical(finished_result).encode("utf-8")).hexdigest())
        _require(recovered and all(controls(path) == before for path, before in recovery_before.items())
                 and finished_result["result"]["outcome"] == "phase_performed"
                 and finished_result["result"]["effect_performed"] is True,
                 "the actually discarded finish reply was not recovered as a read-only saved result")
        completed_status = status("after-finish", "completed")
        beyond_refusal = execute_action("beyond", expected_error=phase_changed)
        pending["beyond"] = cli("beyond-pending-show", COMMAND, "show", ["--action", str(action_directories["beyond"])])
        release_two = cli("generation-two-release", LEASE_COMMAND, "release", release_args(claim_two, PREFIX + "-release-two"))
        released_page = lease_page("released-lease-page")
        lease_observations["release_two"] = release_two
        for name, original in (("ledger", ledger), ("job", job), ("review", review)):
            held = private / (PREFIX + "-offline-" + name)
            original.rename(held)
            detached.append((original, held))
        offline_metadata = {path: controls(path) for path in action_directories.values()}
        for name in ("fill", "link", "observe", "finish"):
            shown = cli("offline-" + name + "-show", COMMAND, "show", ["--action", str(action_directories[name])])
            replayed = cli("offline-" + name + "-execute-replay", COMMAND, "execute", ["--action", str(action_directories[name])])
            _require(shown == replayed == recorded[name], "a saved action depended on unavailable bindings or current authorization")
            offline_records[name] = {"show": shown, "execute_replay": replayed}
        offline_prepare = cli("offline-fill-prepare-replay", COMMAND, "prepare", prepare_args("fill", "prepared", claim_one))
        _require(offline_prepare == actions["fill"], "historical prepare replay rechecked its released lease or absent source")
        for name in ("stale", "beyond"):
            shown = cli("offline-" + name + "-pending-show", COMMAND, "show", ["--action", str(action_directories[name])])
            _require(shown == pending[name], "a pending own-only show inspected the current job or lease")
            offline_pending[name] = shown
        _require(all(controls(path) == before for path, before in offline_metadata.items()),
                 "historical offline reads changed an action's own controls")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    _require(len(detached) == 4 and released_page["operation_count"] == 4
             and released_page["entries"][0]["state"] == "released"
             and released_page["entries"][0]["current_lease"] == {key: release_two[key] for key in lease_fields}
             and pending["beyond"]["state"] == "prepared" and pending["beyond"]["result"] is None,
             "final action history lost its bound phase, released claim or restored paths")
    restored_catalog = cli("restored-catalog-show", CATALOG_COMMAND, "show", ["--catalog", str(catalog)])
    restored_status = status("restored", "completed")
    written_review = cli("written-review-old-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    _require(restored_catalog == catalog_receipt and restored_status == completed_status
             and written_review == statuses["prepared"]["intended_review"]
             and originals_unchanged(), "source-free completion or replay changed earlier controls or exact review content")
    stage("finish-reply-recovery-and-own-only-history",
          "With the captured write catalog absent, the final action completes the review. Its actual reply is discarded and recovered exactly. A pending action beyond its target refuses phase_changed. After lease release, all four catalog/ledger/job/target paths are hidden: recorded action show/execute replay and pending show still read only own controls. Every path returns in finally.",
          {"finish": finished_result, "write_status": completed_status, "beyond_refusal": beyond_refusal,
           "beyond_pending_show": pending["beyond"], "released_lease": release_two, "lease_page": released_page,
           "offline_recorded": offline_records, "offline_pending": offline_pending,
           "offline_prepare_replay": offline_prepare, "restored_catalog": restored_catalog,
           "restored_status": restored_status, "review": written_review,
           "actual_discarded_reply_recovered_exactly": recovered, "maximum_detached_path_count": 4,
           "all_detached_paths_restored": True, "comparison_digest_exported": False,
           "historical_replay_checks_live_lease": False})
    for label, arguments in (
        ("prepare-replay-other-operation", prepare_args("fill", "prepared", claim_one, operation_id=PREFIX + "-other-operation")),
        ("prepare-replay-other-worker", prepare_args("fill", "prepared", claim_one, worker="simulated-action-worker-other")),
        ("prepare-replay-other-phase", prepare_args("fill", "ready", claim_one)),
        ("prepare-replay-other-lease", prepare_args("fill", "prepared", claim_one, lease_id="wrrwl_" + "0" * 32)),
    ):
        cli(label, COMMAND, "prepare", arguments, expected_error=conflict)
    invalid_paths = []
    invalid_requests = (
        ("prepare-index-zero", {"index": 0}),
        ("prepare-index-too-large", {"index": 257}),
        ("prepare-worker-malformed", {"worker": "invalid/worker"}),
        ("prepare-lease-malformed", {"lease_id": "invalid-lease"}),
        ("prepare-generation-zero", {"generation": 0}),
        ("prepare-generation-too-large", {"generation": 1025}),
        ("prepare-completed-phase", {"expected_state": "completed"}),
        ("prepare-operation-malformed", {"operation_id": "invalid/operation"}),
        ("prepare-phase-malformed", {"expected_state": "invalid"}),
    )
    for label, overrides in invalid_requests:
        path = private / (PREFIX + "-" + label)
        invalid_paths.append(path)
        options = {"operation_id": PREFIX + "-" + label, "action_directory": path, **overrides}
        expected_state = options.pop("expected_state", "ready")
        cli(label, COMMAND, "prepare", prepare_args("fill", expected_state, claim_two, **options), expected_error=invalid)
    released_action_path = private / (PREFIX + "-released-claim-action")
    cli("prepare-released-claim", COMMAND, "prepare",
        prepare_args("finish", "linked", claim_two, operation_id=PREFIX + "-released-claim",
                     action_directory=released_action_path), expected_error=lease_changed)
    _require(all(not path.exists() for path in [*invalid_paths, released_action_path]),
             "a refused fresh preparation reserved an action directory")
    previous_statuses = {name: cli("original-" + name + "-write-status", WRITE_COMMAND, "status", ["--job", str(path)])
                         for name, path in prior_jobs.items()}
    original_lease_pages = {}
    for name in ("primary", "secondary"):
        path = private / ("restore-case-retention-review-write-leases-" + name)
        value = cli("original-" + name + "-lease-page", LEASE_COMMAND, "page", ["--ledger", str(path), "--limit", "16"])
        previous = prior_lease_pages[name + "-final-released-page"]
        _require({key: child for key, child in value.items() if key != "checked_at"}
                 == {key: child for key, child in previous.items() if key != "checked_at"},
                 "an earlier ledger changed its frozen membership or released heads")
        original_lease_pages[name] = value
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    final_catalog_page = cli("completed-catalog-page", CATALOG_COMMAND, "page", ["--catalog", str(catalog), "--limit", "16"])
    final_lease_page = lease_page("final-released-lease-page")
    catalog_observations["completed"] = final_catalog_page
    source_after, live_after = observe(source), observe(live)
    action_outcomes = [value for value in cli_outcomes if value["command"] == COMMAND]
    _require(previous_statuses == prior_statuses and originals_unchanged()
             and matches(source_before, source_after) and matches(live_before, live_after)
             and len(actions) == 6 and set(recorded) == {"fill", "link", "observe", "finish"}
             and set(pending) == {"stale", "beyond"}
             and sum(value["result"]["outcome"] == "phase_performed" for value in recorded.values()) == 3
             and sum(value["result"]["outcome"] == "expected_phase_observed" for value in recorded.values()) == 1
             and final_catalog_page["entries"][0]["current_write"]["status"] == completed_status
             and final_lease_page["operation_count"] == 4 and final_lease_page["entries"] == released_page["entries"]
             and len(cli_outcomes) == 74 and len(rejections) == 19
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 55
             and len(action_outcomes) == 45 and sum(value["returncode"] == 0 for value in action_outcomes) == 29
             and len(pages) == 4 and not discarded_addresses,
             "final action, phase, process or unchanged-history accounting differs from the fixed scenario")
    stage("exact-inputs-and-domain-boundaries-remain-explicit",
          "Changed exact requests, malformed scalar inputs and a released claim refuse. Original write jobs and lease heads remain unchanged; blocked restore jobs stay blocked. Six action intents contain four saved observations and two pending records, while only three phase effects completed the one new review.",
          {"rejections": rejections, "actions": actions, "recorded": recorded, "pending": pending,
           "original_write_statuses": previous_statuses, "original_lease_pages": original_lease_pages,
           "completed_catalog_page": final_catalog_page, "released_lease_page": final_lease_page,
           "original_control_metadata_unchanged": True, "original_blocked_job_metadata_unchanged": True,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "old_direct_step_still_available": True, "worker_authenticated": False,
           "domain_writes_allowed": False, "archival_performed": False, "deletion_performed": False})

    artifacts = {
        PREFIX + "-actions.json": actions,
        PREFIX + "-recorded.json": recorded,
        PREFIX + "-pending.json": pending,
        PREFIX + "-execution-observations.json": {
            "executions": executions, "statuses": statuses, "concurrent_results": concurrent_results,
            "catalogs": {"receipt": catalog_receipt, "restored": restored_catalog, **catalog_observations},
            "written_review": written_review, "offline_recorded": offline_records, "offline_pending": offline_pending,
            "offline_prepare_replay": offline_prepare, "original_control_metadata_unchanged": True,
            "original_directory_times_compared": False, "old_blocked_job_metadata_unchanged": True,
            "all_detached_paths_restored": True, "maximum_detached_path_count": 4,
            "new_lease_control_metadata_unchanged_by_actions": True,
            "private_control_contents_read_by_demo": False, "private_action_result_contents_read_by_demo": False,
            "private_lease_database_read_by_demo": False, "private_stage_contents_read_by_demo": False,
            "direct_step_used_for_new_write": False,
        },
        PREFIX + "-lease-observations.json": {**lease_observations, "pages": pages,
                                              "original_final_pages": original_lease_pages},
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {"actual_finish_response_discarded": True, "exact_saved_reply_recovered": recovered,
                                          "saved_reply_does_not_require_current_lease": True,
                                          "comparison_digest_exported": False},
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "public artifact accounting differs from six stages and ten data files")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Durable single-phase write actions checked against a captured local lease",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "action_stage_count": len(stages),
            "new_action_count": 6, "recorded_action_count": 4, "pending_action_count": 2,
            "performed_phase_count": 3, "expected_phase_observation_count": 1,
            "new_write_job_count": 1, "total_write_job_count": 4,
            "new_review_record_count": 1, "total_review_record_count": 6,
            "new_write_catalog_count": 1, "total_write_catalog_count": 3,
            "new_lease_ledger_count": 1, "total_lease_ledger_count": 3,
            "new_lease_operation_count": 4, "total_lease_operation_count": 16,
            "new_ledger_page_response_count": len(pages), "prior_ledger_compatibility_page_count": 2,
            "lease_page_response_count": len(pages) + 2, "new_catalog_page_response_count": 2,
            "new_cli_child_process_count": len(cli_outcomes), "new_successful_cli_count": 55,
            "new_refused_cli_count": len(rejections), "action_command_count": len(action_outcomes),
            "action_successful_command_count": 29, "action_refused_command_count": 16,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "concurrent_same_action_caller_count": 2, "concurrent_saved_results_equal": True,
            "single_phase_after_concurrent_calls": "ready", "process_overlap_measured": False,
            "discarded_finish_response_count": 1, "discarded_finish_response_recovered_exactly": recovered,
            "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0, "injected_partial_write_count": 0,
            "maximum_offline_path_count": 4, "all_detached_paths_restored": True,
            "completion_without_captured_catalog": True, "historical_replay_without_current_bindings": True,
            "historical_replay_requires_current_lease": False, "historical_replay_extends_expiry": False,
            "stale_generation_action_rejected": True, "beyond_target_phase_action_rejected": True,
            "observed_post_phase_claims_own_effect": False, "new_lease_control_metadata_unchanged_by_actions": True,
            "original_control_metadata_unchanged": True, "old_blocked_job_metadata_unchanged": True,
            "original_blocked_jobs_still_blocked": True,
            "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
            "jobs_advanced": True, "direct_step_used_for_new_write": False,
            "lease_required_for_new_execution": True, "job_steps_enforced": False,
            "actor_and_worker_are_distinct_self_declared_labels": True,
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
            "A prepared action captures one exact existing local lease and one write phase. Every new result requires that same current active claim; original review actor and execution worker are distinct self-declared labels, not authenticated participants.",
            "The dedicated action path holds an immutable intent and at most one immutable observation. Two callers executing the same action return one saved result, while only one bounded phase is reached.",
            "A different prepared action may find its exact expected post phase. It records expected_phase_observed with effect_performed=false and never advances to the following phase or claims causal authorship.",
            "A pending action with stale generation refuses lease_changed; a pending action beyond its target refuses phase_changed. Its own historical show remains prepared and does not describe the current job as pending.",
            "A real final-action reply is discarded and recovered exactly after its result is saved. No interruption, partial result, power loss, expiry wait, clock regression or unsupported bootstrap repair is demonstrated.",
            "Completion runs with the captured catalog absent. Once a result is saved, historical show/execute replay needs only the action's own controls, even after lease release and while four catalog/ledger/job/target paths are absent. All paths return in finally.",
            "The new wrapper checks a lease immediately before its local effect under local locks. The old direct write API and other ledgers remain outside this guard, and SQLite assignment history and filesystem effect/result do not form a common transaction.",
            "The driver reads public API responses and lstat metadata, never private intents/results, lease SQL rows or staging contents. Original control metadata is captured after opening initial Store observations. New actions do not renew leases; metadata observations compare both jobs and earlier ledgers without changing them.",
            "The old source/live histories remain R4/36 and R5/59 with previous quarantine, origin and blocked restore cases. New controls do not grant domain authority, deletion or archival permission.",
            "The count of launched processes or matching saved observations does not measure maximum useful agents, simultaneous execution, model independence, semantic quality or correctness of arbitrary proposed code.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Долговечное действие одной фазы с проверкой локальной аренды", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий локальных аренд](restore-case-retention-review-write-leases-report.md); его 395 публичных файлов сохранены.",
        "Подготовлен один новый write job с точным историческим minimum plan и verdict defer. Actor review и worker исполнения остаются разными самозаявленными метками.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "Два обычных execute callers запущены до сбора ответов для одного action. Они вернули одну и ту же сохранённую квитанцию, а job достиг только ready. Перекрытие исполнения и пропускная способность не измерялись.",
        "Action поколения 1, подготовленный для ready→linked, отказал после release и нового claim поколения 2. Новое действие выполнило ready→linked; соседнее действие лишь зафиксировало уже наблюдаемый linked с effect_performed=false.",
        "Последнее действие выполнило linked→completed при недоступном исходном write catalog. Его настоящий ответ отброшен и восстановлен точным replay. Другое pending действие, оставшееся позади target phase, отказало phase_changed без нового шага.",
        "После освобождения аренды четыре catalog/ledger/job/target пути временно скрыты. Исторические show/execute replay записанных действий и show двух pending intents продолжили чтение только собственных controls. Все пути возвращены.",
        "Шесть action intents сохранили четыре наблюдения: три phase_performed и одно expected_phase_observed. Два pending intents остались без result. В новом lease ledger четыре события; проверки действий не продлили аренды и не изменили эту историю.", "",
        "## Публичные основания", "",
        f"- [Шесть действий]({PREFIX}-actions.json), [четыре записанных результата]({PREFIX}-recorded.json), [два pending intents]({PREFIX}-pending.json).",
        f"- [Фазы, повтор и controls]({PREFIX}-execution-observations.json), [аренды]({PREFIX}-lease-observations.json).",
        f"- [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json), [восстановление ответа]({PREFIX}-reply-recovery.json).",
        f"- [Источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы", "",
        "Шесть этапов, 74 новых CLI: 55 успешных и 19 ожидаемых отказов. Из них 45 action-команд: 29 успешных и 16 отказов. С основанием 587 ordinary CLI и 14 прежних interrupt workers, всего 601 дочерний процесс.",
        "Добавлен один write job и один review; всего четыре write jobs и шесть reviews. Исходные Store-наблюдения сохранили R4/36 и R5/59 событий, прежние write/lease controls и blocked restore состояния прежние.",
        "Нет новых fault workers, injected partial writes или ожидания expiry. Восстановление после реального прерывания, частичный result, регресс часов, отказ хранилища и потеря питания данным запуском не проверялись.",
        "Новый wrapper требует текущую captured аренду перед новой записью результата и локальным эффектом. Прежний direct step её не требует; других ledgers, прямого SQL и файлового владельца этот протокол не ограничивает. Общей SQL/filesystem транзакции, внешнего fencing, удостоверения worker либо смыслового одобрения текста нет.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
