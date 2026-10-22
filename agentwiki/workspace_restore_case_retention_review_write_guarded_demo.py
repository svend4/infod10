"""Demonstrate a distinct credential-gated review write family.

Ordinary public CLI children provide every workflow response. The driver reads
validated public output, credential markers and lstat metadata, never private
control bodies, SQL rows or staging contents. Earlier demonstrations remain
unchanged and no new fault worker, partial write or expiry wait is introduced.
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
from .workspace_restore_case_retention_cli import _public_response as _retention_response
from .workspace_restore_case_retention_review_cli import _public_response as _review_response
from .workspace_restore_case_retention_review_write_catalog_cli import _public_response as _catalog_response
from .workspace_restore_case_retention_review_write_cli import _public_response as _write_response
from .workspace_restore_case_retention_review_write_guarded_cli import _public_response as _guarded_response
from .workspace_restore_case_retention_review_write_issuer_demo import run_workspace_restore_case_retention_review_write_issuer_demo
from .workspace_restore_case_retention_review_write_leases_cli import _public_response as _lease_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-guarded-1"
COMMAND = "workspace-restore-case-retention-review-write-guarded"
PREFIX = "restore-case-retention-review-write-guarded"
RETENTION_COMMAND = "workspace-restore-case-retention"
REVIEW_COMMAND = "workspace-restore-case-retention-review"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"
CATALOG_COMMAND = "workspace-restore-case-retention-review-write-catalog"
LEASE_COMMAND = "workspace-restore-case-retention-review-write-leases"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write guarded demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_guarded_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Complete one new guarded job, recover history and revoke its local key."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_issuer_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 449, "the preceding scenario did not produce 449 public files")
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").lstat().st_size
    markers: list[str] = []
    old_credentials = sorted(private.glob("*.credential"))
    _require(len(old_credentials) == 32, "the preceding scenario did not retain 32 credentials")
    for path in old_credentials:
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
        "intent_digest", "ledger_anchor", "catalog_anchor", "ledger_manifest", "credential", "credential_hash",
        "credential_digest", "permit_path", "permit_directory", "grant_record", "revoke_record", "grant_digest",
        "action_anchor", "action_intent", "action_lock", "issuer_anchor", "permit_anchor", "holder_credential_hash",
        "issuer_path", "issuance_path", "issuer_directory", "issuance_directory", "issuer_record", "issuer_lock",
        "permit_record", "permit_lock", "guard_record", "guard_lock", "guard_directory",
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
    discarded_addresses: dict[str, str] = {}

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"{PREFIX}-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> Any:
        _require(command in {COMMAND, RETENTION_COMMAND, REVIEW_COMMAND, WRITE_COMMAND,
                             CATALOG_COMMAND, LEASE_COMMAND, "workspace-restore"}, "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore case retention review write guarded demonstration stopped: ordinary CLI timed out") from error
        match = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
        code = match.group(1) if match else None
        cli_outcomes.append({"label": label, "command": command, "action": action,
                             "child_process_id": process.pid, "returncode": process.returncode,
                             "error_code": code, "stderr_empty": not stderr, "completed_child_process": True,
                             "shell": False, "business_response_discarded": discard})
        if expected_error is not None:
            _require(not discard and process.returncode != 0 and code == expected_error and not stdout,
                     "an ordinary command did not return its expected safe refusal")
            refusal = {"label": label, "command": command, "action": action, "code": code, "rejected": True}
            rejections.append(refusal)
            return refusal
        _require(process.returncode == 0 and not stderr, "an ordinary CLI command failed")
        validators = {COMMAND: _guarded_response, RETENTION_COMMAND: _retention_response,
                      REVIEW_COMMAND: _review_response, WRITE_COMMAND: _write_response,
                      CATALOG_COMMAND: _catalog_response, LEASE_COMMAND: _lease_response}
        try:
            value = json.loads(stdout)
            value = validators[command](value, action) if command in validators else _public_status(value)
            value = public(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Restore case retention review write guarded demonstration stopped: invalid public CLI response") from error
        if discard:
            discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        return value

    plan, job, review = (private / (PREFIX + suffix) for suffix in ("-plan", "-job", "-review"))
    credential = private / (PREFIX + ".credential")
    unrelated_credential = old_credentials[0]
    actor, principal, verdict = "simulated-guarded-review-operator", "simulated-guarded-holder", "defer"
    operation = PREFIX + "-prepare"
    revoke_operation = PREFIX + "-revoke"
    step_operations = {state: PREFIX + "-step-" + state for state in ("prepared", "ready", "linked")}
    failed_catalog = private / (PREFIX + "-legacy-catalog")
    prior_plan = json.loads((workspace / "restore-case-retention-plans.json").read_text())["minimum"]
    prior_statuses = json.loads((workspace / "restore-case-retention-review-write-catalog-write-observations.json").read_text())["final_statuses"]
    for label in ("action", "permit", "issuer"):
        prior_statuses[label] = json.loads((workspace / f"restore-case-retention-review-write-{label}-execution-observations.json").read_text())["statuses"]["restored"]
    prior_jobs = {name: private / ("restore-case-retention-review-write-" + name) for name in ("minimum", "long")}
    prior_jobs.update({"late": private / "restore-case-retention-review-write-catalog-late-job",
                       **{name: private / ("restore-case-retention-review-write-" + name + "-job")
                          for name in ("action", "permit", "issuer")}})
    prior_lease_pages = json.loads((workspace / "restore-case-retention-review-write-leases-pages.json").read_text())
    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    original_paths = sorted(path for path in private.iterdir() if path.is_dir())
    original_controls = {path: controls(path) for path in original_paths}
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    statuses: dict[str, Any] = {}
    guard_observations: dict[str, Any] = {}
    executions: dict[str, Any] = {}
    offline: dict[str, Any] = {}
    revocations: dict[str, Any] = {}
    legacy_refusals: dict[str, Any] = {}
    error_prefix = "workspace_restore_case_retention_review_write_guarded_"
    credential_invalid, conflict, revoked = (error_prefix + suffix for suffix in ("credential_invalid", "conflict", "revoked"))

    def originals_unchanged() -> bool:
        return (all(controls(path) == previous for path, previous in original_controls.items())
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == previous
                        for index, previous in old_observations.items()))

    def prepare_arguments(*, first: bool = False, selected_job: Path = job,
                          key: Path = credential, selected_principal: str = principal,
                          selected_operation: str = operation) -> list[str]:
        return ["--plan", str(plan), "--review", str(review), "--job", str(selected_job),
                "--operation-id", selected_operation, "--actor", actor, "--verdict", verdict,
                "--principal", selected_principal, "--credential-out" if first else "--prepared-credential", str(key)]

    def guarded(label: str, action: str, arguments: list[str], *,
                expected_error: str | None = None, discard: bool = False) -> Any:
        before = controls(job) if job.is_dir() else None
        value = cli(label, COMMAND, action, arguments, expected_error=expected_error, discard=discard)
        if expected_error is not None or action in {"show", "status"}:
            _require(before == (controls(job) if job.is_dir() else None), "a refusal or observation changed guarded controls")
        return value

    def step(label: str, state: str, *, key: Path = credential, selected_operation: str | None = None,
             expected_error: str | None = None, discard: bool = False) -> Any:
        return guarded(label, "step", ["--job", str(job), "--operation-id",
                       step_operations[state] if selected_operation is None else selected_operation,
                       "--expected-state", state, "--credential-file", str(key)],
                       expected_error=expected_error, discard=discard)

    def status(label: str, state: str, access: str = "active") -> dict[str, Any]:
        value = guarded(label, "status", ["--job", str(job)])
        _require(value["guard"] == guard and value["access_state"] == access and value["write"]["state"] == state
                 and value["write"]["completed"] is (state == "completed")
                 and value["write"]["intended_review"]["plan"] == plan_value,
                 "guarded status changed its exact scope, expected phase or access")
        statuses[label] = value
        return value

    def revoke(label: str, *, key: Path = credential, selected_operation: str = revoke_operation,
               expected_error: str | None = None) -> Any:
        return guarded(label, "revoke", ["--job", str(job), "--operation-id", selected_operation,
                       "--credential-file", str(key)], expected_error=expected_error)

    plan_arguments = ["--catalog", str(private / "restore-case-catalog-expanded"), "--plan", str(plan),
                      "--retain-seconds", str(prior_plan["policy"]["retain_seconds"]),
                      "--operation-id", PREFIX + "-plan", "--after-index", str(prior_plan["after_index"]),
                      "--limit", str(prior_plan["limit"])]
    plan_value = cli("fresh-retention-plan", RETENTION_COMMAND, "plan", plan_arguments)
    plan_show = cli("fresh-retention-plan-show", RETENTION_COMMAND, "show", ["--plan", str(plan)])
    _require(plan_value == plan_show and plan_value["plan_id"] != prior_plan["plan_id"]
             and plan_value["catalog"] == prior_plan["catalog"] and plan_value["policy"] == prior_plan["policy"]
             and plan_value["after_index"] == prior_plan["after_index"] == 0 and plan_value["limit"] == prior_plan["limit"] == 4,
             "the separate new plan did not preserve the public original minimum parameters")
    guard = guarded("fresh-guarded-prepare", "prepare", prepare_arguments(first=True))
    token = read_credential(credential)
    markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    public(guard)
    prepared_replay = guarded("initial-exact-prepare-replay", "prepare", prepare_arguments())
    active_show = guarded("initial-active-show", "show", ["--job", str(job)])
    guard_observations.update({"prepared": guard, "initial_prepare_replay": prepared_replay, "initial_show": active_show})
    prepared_status = status("prepared-status", "prepared")
    _require(prepared_replay == guard and active_show["guard"] == guard and active_show["access_state"] == "active"
             and active_show["revocation"] is None and guard["principal"] == principal
             and guard["write"]["actor"] == actor and guard["write"]["verdict"] == verdict,
             "fresh prepare changed its fixed scope or initial local access")
    stage("distinct-family-and-local-credential",
          "A fifth retention plan copies only the original minimum plan's public parameters. One distinct guarded job captures its own review and one local credential. Exact prepare and own show retain the same guard; status is observation rather than permission.",
          {"plan": plan_value, "plan_show": plan_show, "guard": guard, "exact_prepare": prepared_replay,
           "access": active_show, "status": prepared_status, "credentials_exported": False,
           "lease_enforced": False, "issuer_policy_enforced": False, "os_isolation_established": False})

    legacy_before = {path: controls(path) for path in (plan, job, review)}
    old_incomplete = "workspace_restore_case_retention_review_write_incomplete"
    legacy_refusals["step"] = cli("legacy-step-refuses-new-family", WRITE_COMMAND, "step", ["--job", str(job)], expected_error=old_incomplete)
    legacy_refusals["status"] = cli("legacy-status-refuses-new-family", WRITE_COMMAND, "status", ["--job", str(job)], expected_error=old_incomplete)
    legacy_refusals["prepare"] = cli("legacy-prepare-refuses-new-family", WRITE_COMMAND, "prepare",
                                    ["--plan", str(plan), "--review", str(review), "--job", str(job),
                                     "--operation-id", operation, "--actor", actor, "--verdict", verdict], expected_error=old_incomplete)
    legacy_refusals["catalog"] = cli("legacy-catalog-refuses-new-family", CATALOG_COMMAND, "create",
                                    ["--catalog", str(failed_catalog), "--job", str(job), "--operation-id", PREFIX + "-legacy-catalog"],
                                    expected_error="workspace_restore_case_retention_review_write_catalog_incomplete")
    legacy_refusals["guarded_prepare_old_job"] = guarded("guarded-prepare-refuses-legacy-job", "prepare",
                                                         prepare_arguments(selected_job=prior_jobs["minimum"], selected_operation=PREFIX + "-legacy-job"),
                                                         expected_error=error_prefix + "incomplete")
    wrong_step = step("wrong-credential-prepared-step", "prepared", key=unrelated_credential, expected_error=credential_invalid)
    wrong_revoke = revoke("wrong-credential-before-revoke", key=unrelated_credential, expected_error=credential_invalid)
    wrong_prepare = guarded("prepare-replay-other-credential", "prepare", prepare_arguments(key=unrelated_credential), expected_error=conflict)
    changed_principal = guarded("prepare-replay-other-principal", "prepare",
                                prepare_arguments(selected_principal="simulated-other-guarded-holder"), expected_error=conflict)
    _require(all(controls(path) == previous for path, previous in legacy_before.items()) and not failed_catalog.exists()
             and originals_unchanged(), "a refused legacy or guarded request changed controls or reserved a catalog")
    stage("legacy-refusals-before-effects",
          "Legacy step, status, prepare and catalog selection refuse the new family because its legacy coordination control is absent. New prepare refuses an existing legacy job. Wrong step/revoke credentials and changed immutable prepare requests leave metadata unchanged.",
          {"legacy_refusals": legacy_refusals, "wrong_step": wrong_step, "wrong_revoke": wrong_revoke,
           "wrong_prepare": wrong_prepare, "changed_principal": changed_principal,
           "legacy_controls_created": False, "catalog_reserved": False, "original_control_metadata_unchanged": True})

    step("prepared-complete-response-discarded", "prepared", discard=True)
    ready_status = status("after-prepared-step", "ready")
    changed_prepared = step("changed-operation-in-prepared-slot", "prepared", selected_operation=PREFIX + "-other-step-prepared", expected_error=conflict)
    cross_phase = step("prepared-operation-used-for-ready", "ready", selected_operation=step_operations["prepared"], expected_error=conflict)
    executions["ready"] = step("ready-protected-step", "ready")
    linked_status = status("after-ready-step", "linked")
    changed_ready = step("changed-operation-in-ready-slot", "ready", selected_operation=PREFIX + "-other-step-ready", expected_error=conflict)
    executions["linked"] = step("linked-protected-step", "linked")
    completed_status = status("completed-status", "completed")
    written_review = cli("completed-old-review-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    _require(written_review == completed_status["write"]["result"] == prepared_status["write"]["intended_review"]
             and all(value["observation"]["outcome"] == "phase_performed" and value["observation"]["effect_performed"] is True
                     for value in executions.values()), "protected phases or the old review reader changed captured history")
    stage("three-protected-phases-and-discarded-reply",
          "Three authenticated steps perform prepared to ready, ready to linked and linked to completed. The actual successful complete prepared response is discarded. Changed IDs in sealed slots and reusing one operation in another phase conflict. The old review reader accepts the completed historical review.",
          {"ready_status": ready_status, "linked_status": linked_status, "completed_status": completed_status,
           "executions": executions, "written_review": written_review, "changed_prepared": changed_prepared,
           "cross_phase": cross_phase, "changed_ready": changed_ready, "complete_response_discarded": True,
           "interrupted_effect_demonstrated": False, "expected_post_without_receipt_demonstrated": False})

    detached: list[tuple[Path, Path]] = []
    completed_before = controls(job)
    try:
        for path, suffix in ((plan, "plan"), (review, "review")):
            hidden = private / (PREFIX + "-offline-" + suffix)
            _require(not hidden.exists(), "a temporary offline sibling already exists")
            path.rename(hidden)
            detached.append((path, hidden))
        offline["prepared"] = step("completed-offline-prepared-reply-recovered", "prepared")
        recovered = (discarded_addresses.pop("prepared-complete-response-discarded")
                     == hashlib.sha256(canonical(offline["prepared"]).encode("utf-8")).hexdigest())
        executions["prepared"] = offline["prepared"]
        offline["ready"] = step("completed-offline-ready-replay", "ready")
        offline["linked"] = step("completed-offline-linked-replay", "linked")
        offline["active_show"] = guarded("completed-offline-active-show", "show", ["--job", str(job)])
        offline["exact_prepare"] = guarded("completed-offline-exact-prepare", "prepare", prepare_arguments())
        offline["wrong_cached_key"] = step("offline-wrong-credential-cached-step", "prepared", key=unrelated_credential, expected_error=credential_invalid)
        offline["changed_prepared"] = step("offline-changed-prepared-operation", "prepared", selected_operation=PREFIX + "-other-step-prepared", expected_error=conflict)
        offline["cross_phase"] = step("offline-cross-phase-operation", "ready", selected_operation=step_operations["prepared"], expected_error=conflict)
        offline["wrong_revoke"] = revoke("offline-wrong-credential-revoke", key=unrelated_credential, expected_error=credential_invalid)
        _require(recovered and offline["ready"] == executions["ready"] and offline["linked"] == executions["linked"]
                 and offline["active_show"] == active_show and offline["exact_prepare"] == guard
                 and controls(job) == completed_before and all(not path.exists() for path, _ in detached),
                 "completed response recovery needed live targets, changed history or added effects")
        stage("completed-reply-recovery-without-live-targets",
              "After completion, the new plan and review target are temporarily absent. The real discarded prepared response is recovered exactly; all three stable saved phase responses remain historical observations. Own show and exact prepare need neither live target. Authentication and operation conflicts are checked without effects.",
              {"offline": offline, "actual_complete_prepared_reply_recovered_exactly": recovered,
               "comparison_digest_exported": False, "maximum_detached_path_count": 2,
               "guarded_control_metadata_unchanged": True, "historical_target_is_current_phase": False})

        revocations["first"] = revoke("offline-holder-revoke")
        revocations["show"] = guarded("offline-revoked-show", "show", ["--job", str(job)])
        revocations["prepare"] = guarded("offline-revoked-exact-prepare", "prepare", prepare_arguments())
        revoked_before = controls(job)
        revocations["replay"] = revoke("offline-exact-revoke-replay")
        revocations["new_step"] = step("revoked-new-operation-step", "prepared", selected_operation=PREFIX + "-after-revoke-step", expected_error=revoked)
        revocations["cached_step"] = step("revoked-cached-protected-step", "prepared", expected_error=revoked)
        revocations["wrong_cached_key"] = step("revoked-wrong-credential-cached-step", "prepared", key=unrelated_credential, expected_error=credential_invalid)
        revocations["changed_revoke"] = revoke("changed-revoke-operation", selected_operation=PREFIX + "-other-revoke", expected_error=conflict)
        revocations["wrong_new_key"] = step("revoked-wrong-credential-new-step", "linked", key=unrelated_credential,
                                             selected_operation=PREFIX + "-after-revoke-linked", expected_error=credential_invalid)
        _require(revocations["first"] == revocations["show"] == revocations["replay"]
                 and revocations["first"]["access_state"] == "revoked" and revocations["first"]["guard"] == guard
                 and revocations["prepare"] == guard and controls(job) == revoked_before
                 and all(not path.exists() for path, _ in detached), "revocation replay changed controls or exact prepare revived access")
        stage("irreversible-holder-revocation",
              "With both live targets still absent, the holder revokes its own local capability. Own show, exact prepare and exact revoke preserve history and never reactivate it. Revoked access blocks new and cached steps; wrong credentials fail authentication before the missing target is observed.",
              {"revocations": revocations, "live_targets_absent": True, "credential_checked_before_live_target": True,
               "revocation_blocks_cached_step": True, "revocation_blocks_new_step": True,
               "exact_prepare_reactivates_guard": False, "cached_result_deletion_performed": False,
               "os_isolation_established": False, "external_fencing_established": False})
    finally:
        for path, hidden in reversed(detached):
            if hidden.exists():
                hidden.rename(path)

    restored_review = cli("restored-old-review-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    restored_status = status("restored", "completed", "revoked")
    guard_observations["restored_show"] = guarded("restored-revoked-show", "show", ["--job", str(job)])
    guard_observations["restored_prepare"] = guarded("restored-exact-prepare", "prepare", prepare_arguments())
    revocations["restored_replay"] = revoke("restored-exact-revoke-replay")
    previous_statuses = {name: cli("original-write-status-" + name, WRITE_COMMAND, "status", ["--job", str(path)])
                         for name, path in prior_jobs.items()}
    original_lease_pages = {}
    for name, artifact_key in (("primary", "primary-final-released-page"), ("secondary", "secondary-final-released-page")):
        path = private / ("restore-case-retention-review-write-leases-" + name)
        value = cli("original-lease-page-" + name, LEASE_COMMAND, "page", ["--ledger", str(path), "--limit", "16"])
        previous = prior_lease_pages[artifact_key]
        _require({key: child for key, child in value.items() if key != "checked_at"}
                 == {key: child for key, child in previous.items() if key != "checked_at"},
                 "an earlier released lease page changed its exact membership or released head")
        original_lease_pages[name] = value
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    final_plan = cli("final-retention-plan-show", RETENTION_COMMAND, "show", ["--plan", str(plan)])
    source_after, live_after = observe(source), observe(live)
    guarded_outcomes = [value for value in cli_outcomes if value["command"] == COMMAND]
    _require(len(stages) == 5 and len(cli_outcomes) == 60 and len(rejections) == 24
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 36
             and len(guarded_outcomes) == 40 and sum(value["returncode"] == 0 for value in guarded_outcomes) == 23
             and len(executions) == 3 and all(value["observation"]["outcome"] == "phase_performed"
                                             and value["observation"]["effect_performed"] is True for value in executions.values())
             and restored_review == written_review == restored_status["write"]["result"]
             and guard_observations["restored_show"] == revocations["first"] == revocations["restored_replay"]
             and guard_observations["restored_prepare"] == guard and final_plan == plan_value
             and previous_statuses == prior_statuses and originals_unchanged()
             and matches(source_before, source_after) and matches(live_before, live_after)
             and len(list(private.glob("*.credential"))) == 33 and not failed_catalog.exists()
             and all(path.exists() and not hidden.exists() for path, hidden in detached) and not discarded_addresses,
             "final guarded, CLI, public history or preserved-control accounting differs from the fixed scenario")
    stage("preserved-history-and-explicit-limits",
          "Both temporary paths are restored. The old review reader still returns the completed record. Six earlier write statuses, two released lease pages, three blocked restore jobs and original lstat control metadata remain unchanged. Source and live public histories retain their original observations; the new job remains completed and revoked.",
          {"restored_status": restored_status, "restored_review": restored_review, "final_plan": final_plan,
           "original_write_statuses": previous_statuses, "original_lease_pages": original_lease_pages,
           "rejections": rejections, "all_detached_paths_restored": True,
           "original_control_metadata_unchanged": True, "original_directory_times_compared": False,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "domain_writes_allowed": False, "archival_performed": False, "deletion_performed": False})

    artifacts = {
        PREFIX + "-plan-observations.json": {"created": plan_value, "initial_show": plan_show, "final_show": final_plan,
                                             "original_minimum_parameters_reused": True, "original_plan_modified": False},
        PREFIX + "-guard-observations.json": guard_observations,
        PREFIX + "-execution-observations.json": {
            "executions": executions, "statuses": statuses, "offline": offline, "written_review": written_review,
            "restored_review": restored_review, "original_write_statuses": previous_statuses,
            "all_detached_paths_restored": True, "maximum_detached_path_count": 2,
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "legacy_direct_step_used_for_new_write": False,
        },
        PREFIX + "-revocation-observations.json": revocations,
        PREFIX + "-legacy-compatibility.json": {"refusals": legacy_refusals, "original_write_statuses": previous_statuses,
                                                 "original_lease_pages": original_lease_pages,
                                                 "original_control_metadata_unchanged": True,
                                                 "old_blocked_job_metadata_unchanged": True,
                                                 "legacy_controls_created": False, "catalog_reserved": False},
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {
            "actual_complete_prepared_step_response_discarded": True, "exact_complete_step_reply_recovered": recovered,
            "recovered_after_completion": True, "recovered_with_plan_and_review_offline": True,
            "cached_replay_checks_current_credential": True, "cached_replay_checks_current_revocation": True,
            "revoked_cached_replay_allowed": False, "comparison_digest_exported": False,
            "expected_post_without_receipt_source_supported": True, "expected_post_without_receipt_demonstrated": False,
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
        "scenario": "Distinct guarded review write family with stable phase receipts and holder revocation",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "guarded_stage_count": len(stages),
            "new_public_file_count": 18, "total_public_file_count": 467,
            "new_guarded_write_job_count": 1, "total_guarded_write_job_count": 1,
            "new_legacy_write_job_count": 0, "total_legacy_write_job_count": 6, "total_write_job_count": 7,
            "new_retention_plan_count": 1, "total_retention_plan_count": 5,
            "new_review_record_count": 1, "total_review_record_count": 9,
            "new_private_credential_file_count": 1, "total_private_credential_file_count": 33, "credentials_exported": False,
            "new_step_result_count": 3, "performed_phase_count": 3, "expected_phase_observation_count": 0,
            "new_guarded_revocation_count": 1, "active_guarded_write_count": 0, "revoked_guarded_write_count": 1,
            "total_write_catalog_count": 5, "new_write_catalog_count": 0,
            "total_lease_ledger_count": 5, "total_lease_operation_count": 20, "new_lease_operation_count": 0,
            "total_action_count": 12, "total_recorded_action_count": 10, "total_pending_action_count": 2,
            "total_native_permit_count": 7, "total_native_permit_revocation_count": 3,
            "total_issuer_count": 2, "total_issuer_revocation_count": 2, "total_issuance_count": 3,
            "new_cli_child_process_count": len(cli_outcomes), "new_successful_cli_count": 36,
            "new_refused_cli_count": len(rejections), "guarded_command_count": len(guarded_outcomes),
            "guarded_successful_command_count": 23, "guarded_refused_command_count": 17,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "discarded_complete_step_response_count": 1, "discarded_complete_step_response_recovered_exactly": recovered,
            "discarded_response_recovered_after_completion": True, "maximum_offline_path_count": 2,
            "all_detached_paths_restored": True, "cached_replay_needs_live_plan": False,
            "cached_replay_needs_live_review": False, "cached_replay_requires_current_credential": True,
            "cached_replay_requires_unrevoked_guard": True, "revocation_blocks_cached_step": True,
            "revocation_blocks_new_step": True, "exact_prepare_reactivates_guard": False,
            "legacy_write_apis_rejected": True, "legacy_write_family_adopted": False,
            "legacy_controls_created": False, "legacy_direct_step_used_for_new_write": False,
            "original_control_metadata_unchanged": True, "old_blocked_job_metadata_unchanged": True,
            "original_blocked_jobs_still_blocked": True, "original_write_statuses_unchanged": True,
            "original_lease_pages_unchanged": True, "old_review_reader_compatible": True,
            "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
            "new_restore_attempt_count": 0, "domain_writes_allowed": False, "archival_performed": False,
            "deletion_performed": False, "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0,
            "injected_partial_write_count": 0, "interruption_recovery_demonstrated": False,
            "expected_post_without_receipt_source_supported": True, "expected_post_without_receipt_demonstrated": False,
            "expiry_path_demonstrated": False, "clock_regression_path_demonstrated": False,
            "partial_result_repair_demonstrated": False, "cross_directory_atomicity_claimed": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "local_capability_only": True, "external_identity_verified": False, "actor_authenticated": False,
            "old_authority_activated": False, "lease_enforced": False, "issuer_policy_enforced": False,
            "os_isolation_established": False, "external_fencing_established": False,
            "external_llm_used": False, "human_review_recorded": False, "proposed_code_executed": False,
            "measured_capacity": False, "process_overlap_measured": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "One distinct guarded family rejects known legacy public step, status, prepare and catalog selection. It neither converts legacy jobs nor creates their old intent or lock. Filesystem owners, direct private helpers, direct syscalls and other copies remain outside the managed gate.",
            "The holder proves possession of one local credential. Principal and actor are self-declared labels; there is no external identity, OS isolation, semantic authority, lease enforcement or issuer policy for this new family.",
            "Each of three immutable result slots binds one operation and expected phase. Exact saved responses report their historical target; they do not describe the current phase or authenticate who performed the historical effect.",
            "The actual complete prepared reply is discarded and recovered exactly only after later phases completed, while the new plan and review are absent. Current credential and revocation checks remain mandatory even for cached protected responses.",
            "Holder revocation is irreversible within supported APIs and blocks both new and cached steps. Exact prepare, own show and exact revoke retain history without reactivation. Revocation waits behind an admitted step; it does not cancel, recall or undo completed work.",
            "An effect and its saved result are not one atomic transaction. The source supports an exact POST observation when a receipt is absent, but this normal scenario does not demonstrate interruption, partial writes, missing-receipt injection, races, power loss, expiry or clock regression. Partial controls are not repaired.",
            "Only the new plan and review are temporarily hidden and both return in finally. Legacy control metadata, released lease heads and public source/live histories remain unchanged; no domain document, code, archival authorization or deletion is produced by a review receipt.",
            "The driver reads public responses, credential markers and lstat metadata. It never reads private control bodies, lease SQL rows or staging contents. No external LLM, semantic quality or agent throughput is measured.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Отдельное семейство защищённой записи review", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала выполнен [предыдущий сценарий issuer](restore-case-retention-review-write-issuer-report.md); его 449 публичных файлов сохранены.",
        "Добавлены один новый план, один guarded job, девятый review и отдельный локальный ключ. Три защищённые фазы завершают запись; затем доступ отзывается.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "Старые публичные step, status, prepare и catalog отказывают при выборе нового семейства до эффекта. Новое prepare отказывает для существующего legacy job. Новые controls не создают старые intent/lock; ключ обязателен для поддерживаемого guarded step.",
        "Настоящий успешный полный ответ prepared отброшен. После ready→linked→completed он точно восстановлен при временно отсутствующих новом плане и review. Три cached ответа сохраняют историческую целевую фазу. Изменённый ID занятого слота и повтор ID в другой фазе конфликтуют.",
        "При тех же отсутствующих двух путях holder отзывает свой локальный доступ. Отозванный ключ блокирует и новый step, и cached protected replay; чужой ключ возвращает credential_invalid. Own show, exact prepare и exact revoke сохраняют историю и не возобновляют доступ. Оба пути восстановлены в finally; старый reader читает завершённый review.",
        "", "## Публичные основания", "",
        f"- [Новый план]({PREFIX}-plan-observations.json), [guard]({PREFIX}-guard-observations.json), [три результата и статусы]({PREFIX}-execution-observations.json).",
        f"- [Отзыв]({PREFIX}-revocation-observations.json), [совместимость]({PREFIX}-legacy-compatibility.json), [восстановление ответа]({PREFIX}-reply-recovery.json).",
        f"- [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json), [источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы", "",
        "Шесть этапов, 60 новых ordinary CLI: 36 успешных и 24 ожидаемых отказа. Из них 40 guarded-команд: 23 успешных и 17 отказов. Всего 821 CLI и 14 прежних interrupt workers, 835 дочерних процессов. Новых fault workers или injected partial writes нет.",
        "В runtime: шесть legacy write jobs и один guarded job, девять reviews, пять retention plans, пять write catalogs, пять lease DB и 20 прежних lease событий. Двенадцать прежних action intents сохраняют десять результатов и два pending; семь native permits сохраняют три отзыва; два issuer сохраняют два отзыва и три issuance receipts. Три новых saved step results и один guarded revoke относятся только к новому job.",
        "Bearer подтверждает локальное владение ключом. Владелец файловой системы, private helpers, прямые syscalls и другие копии находятся за пределами gate. Actor/principal не удостоверяют человека или модель; leases и issuer policy для нового семейства не применяются. ОС изоляция и внешнее fencing не установлены.",
        "Effect и запись результата не атомарны. Ветка exact POST без receipt поддерживается исходным кодом, но в данном обычном сценарии не демонстрируется. Revocation не отменяет уже выполненный эффект; исторический review не подтверждает семантическую достоверность содержания или право архивировать/удалять.",
        "Private credentials/hashes, управляющие тела, pins и digests не экспортируются. Драйвер читает только публичные ответы, маркеры ключей и lstat. Ёмкость агентов, одновременность процессов и качество содержания не измерялись.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
