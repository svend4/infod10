"""Demonstrate exact-policy issuance of a fresh guarded review write.

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
from .workspace_restore_case_retention_review_write_guarded_demo import run_workspace_restore_case_retention_review_write_guarded_demo
from .workspace_restore_case_retention_review_write_guarded_issuer_cli import _public_response as _managed_response
from .workspace_restore_case_retention_review_write_leases_cli import _public_response as _lease_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-guarded-issuer-1"
COMMAND = "workspace-restore-case-retention-review-write-guarded-issuer"
GUARDED_COMMAND = "workspace-restore-case-retention-review-write-guarded"
PREFIX = "restore-case-retention-review-write-guarded-issuer"
RETENTION_COMMAND = "workspace-restore-case-retention"
REVIEW_COMMAND = "workspace-restore-case-retention-review"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"
CATALOG_COMMAND = "workspace-restore-case-retention-review-write-catalog"
LEASE_COMMAND = "workspace-restore-case-retention-review-write-leases"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write guarded issuer demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_guarded_issuer_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Issue one fresh native guard under policy and preserve historical receipts."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_guarded_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 467, "the preceding scenario did not produce 467 public files")
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").lstat().st_size
    markers: list[str] = []
    old_credentials = sorted(private.glob("*.credential"))
    _require(len(old_credentials) == 33, "the preceding scenario did not retain 33 credentials")
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
        "permit_record", "permit_lock", "guard_record", "guard_lock", "guard_directory", "guard_anchor",
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
        _require(command in {COMMAND, GUARDED_COMMAND, RETENTION_COMMAND, REVIEW_COMMAND, WRITE_COMMAND,
                             CATALOG_COMMAND, LEASE_COMMAND, "workspace-restore"}, "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore case retention review write guarded issuer demonstration stopped: ordinary CLI timed out") from error
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
        validators = {COMMAND: _managed_response, GUARDED_COMMAND: _guarded_response, RETENTION_COMMAND: _retention_response,
                      REVIEW_COMMAND: _review_response, WRITE_COMMAND: _write_response,
                      CATALOG_COMMAND: _catalog_response, LEASE_COMMAND: _lease_response}
        try:
            value = json.loads(stdout)
            value = validators[command](value, action) if command in validators else _public_status(value)
            value = public(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Restore case retention review write guarded issuer demonstration stopped: invalid public CLI response") from error
        if discard:
            discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        return value

    plan, job, review = (private / (PREFIX + suffix) for suffix in ("-plan", "-job", "-review"))
    issuer, issuance = (private / (PREFIX + suffix) for suffix in ("-main", "-issuance"))
    admin_credential, holder_credential = (private / (PREFIX + suffix) for suffix in ("-admin.credential", "-holder.credential"))
    unrelated_credential, other_credential = old_credentials[:2]
    unused_issuance = private / (PREFIX + "-unissued")
    unused_job, unused_review, missing_plan = (private / (PREFIX + suffix)
                                              for suffix in ("-unused-job", "-unused-review", "-unavailable-plan"))
    invalid_issuer = private / (PREFIX + "-invalid-issuer")
    administrator = "simulated-guarded-issuer-administrator"
    actor, principal, verdict = "simulated-managed-guarded-reviewer", "simulated-managed-guarded-holder", "defer"
    create_operation, issue_operation = PREFIX + "-create", PREFIX + "-issue"
    issuer_revoke_operation, holder_revoke_operation = PREFIX + "-revoke", PREFIX + "-holder-revoke"
    step_operations = {state: PREFIX + "-step-" + state for state in ("prepared", "ready", "linked")}
    prior_plan = json.loads((workspace / "restore-case-retention-plans.json").read_text())["minimum"]
    old_plan = private / "restore-case-retention-minimum"
    prior_statuses = json.loads((workspace / "restore-case-retention-review-write-catalog-write-observations.json").read_text())["final_statuses"]
    for name in ("action", "permit", "issuer"):
        prior_statuses[name] = json.loads((workspace / f"restore-case-retention-review-write-{name}-execution-observations.json").read_text())["statuses"]["restored"]
    prior_jobs = {name: private / ("restore-case-retention-review-write-" + name) for name in ("minimum", "long")}
    prior_jobs.update({"late": private / "restore-case-retention-review-write-catalog-late-job",
                       **{name: private / ("restore-case-retention-review-write-" + name + "-job")
                          for name in ("action", "permit", "issuer")}})
    prior_guarded = json.loads((workspace / "restore-case-retention-review-write-guarded-execution-observations.json").read_text())["statuses"]["restored"]
    prior_lease_pages = json.loads((workspace / "restore-case-retention-review-write-leases-pages.json").read_text())
    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    original_paths = sorted(path for path in private.iterdir() if path.is_dir())
    original_controls = {path: controls(path) for path in original_paths}
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    issuer_observations: dict[str, Any] = {}
    issue_observations: dict[str, Any] = {}
    guard_observations: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    executions: dict[str, Any] = {}
    offline: dict[str, Any] = {}
    policy_refusals: dict[str, Any] = {}
    native_refusals: dict[str, Any] = {}
    issuer_revocations: dict[str, Any] = {}
    holder_revocations: dict[str, Any] = {}
    error_prefix = "workspace_restore_case_retention_review_write_guarded_issuer_"
    credential_invalid, conflict, policy_denied, revoked = (error_prefix + suffix for suffix in
                                                           ("credential_invalid", "conflict", "policy_denied", "revoked"))
    guarded_error = "workspace_restore_case_retention_review_write_guarded_"

    def originals_unchanged() -> bool:
        return (all(controls(path) == previous for path, previous in original_controls.items())
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == previous
                        for index, previous in old_observations.items()))

    def managed(label: str, action: str, arguments: list[str], *,
                expected_error: str | None = None, discard: bool = False) -> Any:
        before = {path: controls(path) for path in (issuer, issuance, plan, job, review) if path.is_dir()}
        value = cli(label, COMMAND, action, arguments, expected_error=expected_error, discard=discard)
        if expected_error is not None or action in {"show", "receipt"}:
            _require(before == {path: controls(path) for path in (issuer, issuance, plan, job, review) if path.is_dir()},
                     "a managed refusal or observation changed owned controls")
        return value

    def create_arguments(*, first: bool = False, selected_issuer: Path = issuer,
                         selected_policy: list[dict[str, Any]] | None = None) -> list[str]:
        rows = policy if selected_policy is None else selected_policy
        arguments = ["--issuer", str(selected_issuer), "--operation-id", create_operation,
                     "--administrator", administrator]
        for row in rows:
            arguments.extend(["--rule", row["rule_id"], row["principal"], row["actor"], row["verdict"], row["plan_id"]])
        return [*arguments, "--credential-out" if first else "--prepared-credential", str(admin_credential)]

    def issue_arguments(*, first: bool = False, selected_issuance: Path = issuance,
                        selected_plan: Path = plan, selected_job: Path = job, selected_review: Path = review,
                        selected_principal: str = principal, selected_actor: str = actor,
                        selected_verdict: str = verdict, selected_operation: str = issue_operation,
                        admin: Path = admin_credential, holder: Path = holder_credential) -> list[str]:
        return ["--issuer", str(issuer), "--issuance", str(selected_issuance), "--plan", str(selected_plan),
                "--review", str(selected_review), "--job", str(selected_job), "--operation-id", selected_operation,
                "--principal", selected_principal, "--actor", selected_actor, "--verdict", selected_verdict,
                "--issuer-credential-file", str(admin),
                "--holder-credential-out" if first else "--prepared-holder-credential", str(holder)]

    def issuer_revoke(label: str, *, key: Path = admin_credential,
                      selected_operation: str = issuer_revoke_operation, expected_error: str | None = None) -> Any:
        return managed(label, "revoke", ["--issuer", str(issuer), "--operation-id", selected_operation,
                       "--credential-file", str(key)], expected_error=expected_error)

    def guarded(label: str, action: str, arguments: list[str], *, expected_error: str | None = None) -> Any:
        before = controls(job) if job.is_dir() else None
        value = cli(label, GUARDED_COMMAND, action, arguments, expected_error=expected_error)
        if expected_error is not None or action in {"show", "status"}:
            _require(before == (controls(job) if job.is_dir() else None), "a native refusal or observation changed guarded controls")
        return value

    def status(label: str, state: str, access: str = "active") -> dict[str, Any]:
        value = guarded(label, "status", ["--job", str(job)])
        _require(value["guard"] == guard and value["access_state"] == access and value["write"]["state"] == state
                 and value["write"]["completed"] is (state == "completed")
                 and value["write"]["intended_review"]["plan"] == plan_value,
                 "managed native status changed its exact guard, historical plan or expected phase")
        statuses[label] = value
        return value

    def step(label: str, state: str, *, key: Path = holder_credential, selected_operation: str | None = None,
             expected_error: str | None = None) -> Any:
        return guarded(label, "step", ["--job", str(job), "--operation-id",
                       step_operations[state] if selected_operation is None else selected_operation,
                       "--expected-state", state, "--credential-file", str(key)], expected_error=expected_error)

    def native_prepare(label: str) -> dict[str, Any]:
        return guarded(label, "prepare", ["--plan", str(plan), "--review", str(review), "--job", str(job),
                       "--operation-id", issue_operation, "--actor", actor, "--verdict", verdict,
                       "--principal", principal, "--prepared-credential", str(holder_credential)])

    def holder_revoke(label: str, *, key: Path = holder_credential, expected_error: str | None = None) -> Any:
        return guarded(label, "revoke", ["--job", str(job), "--operation-id", holder_revoke_operation,
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
             "the separate plan did not preserve only the original minimum public parameters")
    policy = [{"rule_id": "allow-managed-review", "principal": principal, "actor": actor,
               "verdict": verdict, "plan_id": plan_value["plan_id"]}]
    issuer_value = managed("fresh-issuer-create", "create", create_arguments(first=True))
    token = read_credential(admin_credential)
    markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    public(issuer_value)
    issuer_observations["initial_exact_create"] = managed("initial-exact-issuer-create-replay", "create", create_arguments())
    issuer_observations["initial_show"] = managed("initial-active-issuer-show", "show", ["--issuer", str(issuer)])
    duplicate_id_policy = [policy[0], {**policy[0], "actor": "simulated-other-managed-reviewer"}]
    duplicate_scope_policy = [policy[0], {**policy[0], "rule_id": "duplicate-scope"}]
    policy_refusals["duplicate_rule_id"] = managed("duplicate-rule-id-create", "create",
                                                   create_arguments(selected_issuer=invalid_issuer, selected_policy=duplicate_id_policy),
                                                   expected_error=error_prefix + "invalid_input")
    policy_refusals["duplicate_rule_scope"] = managed("duplicate-rule-scope-create", "create",
                                                      create_arguments(selected_issuer=invalid_issuer, selected_policy=duplicate_scope_policy),
                                                      expected_error=error_prefix + "invalid_input")
    policy_refusals["wrong_administrator_issue"] = managed("wrong-administrator-before-issue", "issue",
                                                            issue_arguments(admin=unrelated_credential, holder=other_credential),
                                                            expected_error=credential_invalid)
    policy_refusals["wrong_administrator_revoke"] = issuer_revoke("wrong-administrator-before-revoke", key=unrelated_credential,
                                                                 expected_error=credential_invalid)
    policy_refusals["principal"] = managed("unlisted-principal-issue", "issue",
                                            issue_arguments(selected_principal="simulated-other-managed-holder", holder=unrelated_credential),
                                            expected_error=policy_denied)
    policy_refusals["actor"] = managed("nonmatching-actor-issue", "issue",
                                        issue_arguments(selected_actor="simulated-other-managed-reviewer", holder=unrelated_credential),
                                        expected_error=policy_denied)
    policy_refusals["verdict"] = managed("nonmatching-verdict-issue", "issue",
                                          issue_arguments(selected_verdict="reviewed", holder=unrelated_credential),
                                          expected_error=policy_denied)
    policy_refusals["plan_scope"] = managed("nonmatching-plan-issue", "issue",
                                       issue_arguments(selected_plan=old_plan, holder=unrelated_credential), expected_error=policy_denied)
    _require(issuer_observations["initial_exact_create"] == issuer_value
             and issuer_observations["initial_show"]["issuer"] == issuer_value
             and issuer_observations["initial_show"]["state"] == "active" and issuer_value["policy"] == policy
             and all(not path.exists() for path in (issuance, job, review, invalid_issuer)) and originals_unchanged(),
             "a denied issuance reserved targets or changed the fixed issuer policy")
    stage("exact-policy-and-local-administrator",
          "A fresh retention plan is named by one exact rule for principal, actor and verdict. One separate administrator key creates the issuer. Duplicate rules, wrong administrator and four mismatching scopes fail before target reservation.",
          {"retention_plan": plan_value, "plan_show": plan_show, "issuer": issuer_value, "policy": policy,
           "exact_create": issuer_observations["initial_exact_create"], "access": issuer_observations["initial_show"],
           "policy_refusals": policy_refusals, "new_targets_reserved_by_refusal": False,
           "credentials_exported": False, "os_isolation_established": False})

    managed("complete-managed-issue-response-discarded", "issue", issue_arguments(first=True), discard=True)
    token = read_credential(holder_credential)
    markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    guard_observations["issued_access"] = guarded("issued-guard-own-show", "show", ["--job", str(job)])
    guard = guard_observations["issued_access"]["guard"]
    prepared_status = status("issued-guard-prepared-status", "prepared")
    guard_observations["initial_exact_prepare"] = native_prepare("issued-native-exact-prepare")
    native_refusals["administrator_not_holder"] = step("issuer-admin-is-not-holder-step", "prepared", key=admin_credential,
                                                       expected_error=guarded_error + "credential_invalid")
    native_refusals["unrelated_not_holder"] = step("unrelated-key-is-not-holder-step", "prepared", key=unrelated_credential,
                                                   expected_error=guarded_error + "credential_invalid")
    native_refusals["holder_not_administrator_issue"] = managed("holder-is-not-administrator-issue", "issue",
                                                                 issue_arguments(admin=holder_credential, holder=unrelated_credential),
                                                                 expected_error=credential_invalid)
    native_refusals["existing_native_not_adopted"] = managed("new-request-cannot-adopt-native-guard", "issue",
                                                              issue_arguments(selected_issuance=unused_issuance), expected_error=conflict)
    native_refusals["legacy_step"] = cli("legacy-step-refuses-issued-guard", WRITE_COMMAND, "step", ["--job", str(job)],
                                          expected_error="workspace_restore_case_retention_review_write_incomplete")
    _require(guard_observations["issued_access"]["access_state"] == "active"
             and guard_observations["initial_exact_prepare"] == guard and guard["operation_id"] == issue_operation
             and guard["principal"] == principal and guard["write"]["actor"] == actor and guard["write"]["verdict"] == verdict
             and guard["issuer_policy_enforced"] is False and guard["lease_enforced"] is False
             and not unused_issuance.exists() and originals_unchanged(),
             "native issuance changed its scope, key separation or independent step-admission rules")
    stage("admitted-native-guard-and-discarded-reply",
          "The complete successful managed-issue response is discarded. Own native show and status identify a fresh guarded job and separate holder key. Exact native prepare keeps its old owner path. Administrator and unrelated keys cannot step, a holder cannot issue, and a new request cannot adopt the existing job.",
          {"guard": guard, "access": guard_observations["issued_access"], "status": prepared_status,
           "exact_prepare": guard_observations["initial_exact_prepare"], "refusals": native_refusals,
           "actual_complete_issuance_reply_discarded": True, "issuer_policy_enforced_during_native_step": False,
           "managed_failure_implies_no_native_guard": False, "partial_issuance_gap_injected": False})

    issuer_revocations["holder_not_administrator"] = issuer_revoke("holder-is-not-administrator-revoke", key=holder_credential,
                                                                  expected_error=credential_invalid)
    issuer_revocations["first"] = issuer_revoke("issuer-administrator-revoke")
    issuer_observations["revoked_show"] = managed("revoked-issuer-show", "show", ["--issuer", str(issuer)])
    issuer_revocations["exact_replay"] = issuer_revoke("exact-issuer-revoke-replay")
    issuer_observations["revoked_exact_create"] = managed("revoked-issuer-exact-create-replay", "create", create_arguments())
    issuer_revocations["new_issue"] = managed("revoked-new-issuance-before-targets", "issue",
                                               issue_arguments(selected_issuance=unused_issuance, selected_job=unused_job,
                                                               selected_review=unused_review, selected_plan=missing_plan),
                                               expected_error=revoked)
    issuer_revocations["changed_operation"] = issuer_revoke("changed-issuer-revoke-operation",
                                                             selected_operation=PREFIX + "-other-revoke", expected_error=conflict)
    issuer_revocations["wrong_administrator"] = managed("wrong-administrator-after-revoke", "issue",
                                                         issue_arguments(admin=unrelated_credential), expected_error=credential_invalid)
    guard_observations["active_after_issuer_revoke"] = guarded("child-active-after-issuer-revoke", "show", ["--job", str(job)])
    executions["prepared"] = step("prepared-holder-step-after-issuer-revoke", "prepared")
    ready_status = status("after-prepared-step", "ready")
    executions["ready"] = step("ready-holder-step-after-issuer-revoke", "ready")
    linked_status = status("after-ready-step", "linked")
    executions["linked"] = step("linked-holder-step-after-issuer-revoke", "linked")
    completed_status = status("completed-status-after-issuer-revoke", "completed")
    written_review = cli("completed-old-review-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    _require(issuer_revocations["first"] == issuer_observations["revoked_show"] == issuer_revocations["exact_replay"]
             and issuer_revocations["first"]["state"] == "revoked"
             and issuer_observations["revoked_exact_create"] == issuer_value
             and guard_observations["active_after_issuer_revoke"] == guard_observations["issued_access"]
             and all(value["observation"]["outcome"] == "phase_performed" and value["observation"]["effect_performed"] is True
                     for value in executions.values()) and written_review == completed_status["write"]["result"]
             and all(not path.exists() for path in (unused_issuance, unused_job, unused_review, missing_plan)),
             "issuer revocation changed an existing child capability or allowed a new managed reservation")
    stage("issuer-revoked-holder-completes",
          "Administrator revocation blocks a new issuance before missing target reads. The native holder remains active and completes all three credential-required phases after that revocation. Exact issuer create and revoke retain historical responses; an issuer key does not revoke its child.",
          {"issuer_revocations": issuer_revocations, "issuer_show": issuer_observations["revoked_show"],
           "exact_create": issuer_observations["revoked_exact_create"],
           "active_child_access": guard_observations["active_after_issuer_revoke"], "executions": executions,
           "ready_status": ready_status, "linked_status": linked_status, "completed_status": completed_status,
           "written_review": written_review, "issuer_revocation_revokes_existing_guard": False,
           "native_phase_count_after_issuer_revoke": 3})

    detached: list[tuple[Path, Path]] = []
    sealed_before = controls(issuance)
    try:
        for path, suffix in ((plan, "plan"), (review, "review"), (job, "job")):
            hidden = private / (PREFIX + "-offline-" + suffix)
            _require(not hidden.exists(), "a temporary offline sibling already exists")
            path.rename(hidden)
            detached.append((path, hidden))
        offline["recovered"] = managed("completed-offline-exact-managed-issue-recovered", "issue", issue_arguments())
        recovered = (discarded_addresses.pop("complete-managed-issue-response-discarded")
                     == hashlib.sha256(canonical(offline["recovered"]).encode("utf-8")).hexdigest())
        issue_observations["recovered"] = offline["recovered"]
        offline["own_receipt"] = managed("completed-offline-own-receipt", "receipt", ["--issuance", str(issuance)])
        offline["other_holder"] = managed("offline-replay-other-holder-credential", "issue",
                                           issue_arguments(holder=unrelated_credential), expected_error=conflict)
        offline["other_principal"] = managed("offline-replay-other-principal", "issue",
                                              issue_arguments(selected_principal="simulated-other-managed-holder"), expected_error=conflict)
        offline["other_actor"] = managed("offline-replay-other-actor", "issue",
                                          issue_arguments(selected_actor="simulated-other-managed-reviewer"), expected_error=conflict)
        offline["other_verdict"] = managed("offline-replay-other-verdict", "issue",
                                            issue_arguments(selected_verdict="reviewed"), expected_error=conflict)
        offline["other_operation"] = managed("offline-replay-other-operation", "issue",
                                              issue_arguments(selected_operation=PREFIX + "-other-issue"), expected_error=conflict)
        offline["other_plan"] = managed("offline-replay-other-plan", "issue", issue_arguments(selected_plan=old_plan), expected_error=conflict)
        offline["other_job"] = managed("offline-replay-other-job", "issue", issue_arguments(selected_job=unused_job), expected_error=conflict)
        offline["other_review"] = managed("offline-replay-other-review", "issue", issue_arguments(selected_review=unused_review), expected_error=conflict)
        offline["wrong_administrator"] = managed("offline-wrong-administrator-issue", "issue",
                                                  issue_arguments(admin=unrelated_credential), expected_error=credential_invalid)
        offline["new_issue_after_revoke"] = managed("offline-new-issuance-after-revoke", "issue",
                                                     issue_arguments(selected_issuance=unused_issuance, selected_job=unused_job,
                                                                     selected_review=unused_review, selected_plan=missing_plan),
                                                     expected_error=revoked)
        offline["revoked_issuer_show"] = managed("offline-revoked-issuer-show", "show", ["--issuer", str(issuer)])
        offline["exact_issuer_revoke"] = issuer_revoke("offline-exact-issuer-revoke-replay")
        hidden_issuer = private / (PREFIX + "-offline-issuer")
        _require(not hidden_issuer.exists(), "a temporary issuer sibling already exists")
        issuer.rename(hidden_issuer)
        detached.append((issuer, hidden_issuer))
        offline["own_receipt_without_issuer"] = managed("own-receipt-with-issuer-also-offline", "receipt", ["--issuance", str(issuance)])
        _require(recovered and offline["recovered"] == offline["own_receipt"] == offline["own_receipt_without_issuer"]
                 and offline["recovered"]["issuer"] == issuer_value and offline["recovered"]["guard"] == guard
                 and offline["recovered"]["rule"] == policy[0] and offline["recovered"]["plan_id"] == plan_value["plan_id"]
                 and offline["recovered"]["policy_authorized"] is True
                 and offline["revoked_issuer_show"] == issuer_revocations["first"]
                 and offline["exact_issuer_revoke"] == issuer_revocations["first"]
                 and controls(issuance) == sealed_before and all(not path.exists() for path, _ in detached),
                 "historical receipt recovery needed missing targets, changed the saved issuance or revived capabilities")
        stage("historical-issuance-without-live-targets",
              "After native completion and issuer revocation, the actual complete discarded issue response is recovered exactly with the new plan, review and job offline. Changed requests conflict without live reads. Own receipt lookup also works while the issuer is offline; its historical receipt is unchanged.",
              {"offline": offline, "actual_complete_issuance_reply_recovered_exactly": recovered,
               "maximum_detached_path_count": 4, "issuance_control_metadata_unchanged": True,
               "comparison_digest_exported": False, "issuer_revocation_blocks_exact_issue_replay": False,
               "own_receipt_requires_live_issuer": False, "historical_receipt_is_current_guard_access": False})
    finally:
        for path, hidden in reversed(detached):
            if hidden.exists():
                hidden.rename(path)

    restored_active_status = status("restored-completed-status", "completed")
    restored_review = cli("restored-old-review-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    issue_observations["restored_own_receipt"] = managed("restored-own-issuance-receipt", "receipt", ["--issuance", str(issuance)])
    issuer_observations["restored_revoked_show"] = managed("restored-revoked-issuer-show", "show", ["--issuer", str(issuer)])
    holder_revocations["wrong_key"] = holder_revoke("wrong-holder-before-native-revoke", key=admin_credential,
                                                    expected_error=guarded_error + "credential_invalid")
    holder_revocations["first"] = holder_revoke("holder-native-revoke")
    holder_revocations["show"] = guarded("revoked-child-own-show", "show", ["--job", str(job)])
    holder_revocations["exact_replay"] = holder_revoke("exact-native-revoke-replay")
    holder_revocations["cached_step"] = step("holder-revoked-cached-step", "prepared", expected_error=guarded_error + "revoked")
    holder_revocations["new_step"] = step("holder-revoked-new-step", "prepared", selected_operation=PREFIX + "-after-holder-revoke",
                                           expected_error=guarded_error + "revoked")
    issue_observations["exact_issue_after_holder_revoke"] = managed("exact-managed-issue-after-native-revoke", "issue", issue_arguments())
    issue_observations["own_receipt_after_holder_revoke"] = managed("own-issuance-after-native-revoke", "receipt", ["--issuance", str(issuance)])
    guard_observations["exact_prepare_after_revoke"] = native_prepare("native-exact-prepare-after-revoke")
    final_status = status("revoked-child-status", "completed", "revoked")
    _require(restored_active_status["access_state"] == "active" and restored_review == written_review
             and issuer_observations["restored_revoked_show"] == issuer_revocations["first"]
             and holder_revocations["first"] == holder_revocations["show"] == holder_revocations["exact_replay"]
             and holder_revocations["first"]["access_state"] == "revoked"
             and guard_observations["exact_prepare_after_revoke"] == guard
             and all(value == offline["recovered"] for value in issue_observations.values())
             and controls(issuance) == sealed_before and all(path.exists() and not hidden.exists() for path, hidden in detached),
             "separate holder revocation changed historical issuance or exact prepare revived the native guard")
    stage("separate-holder-revocation",
          "After all paths return, the child is still completed and active despite issuer revocation. Its own holder then revokes native access, blocking new and cached protected steps. Exact managed issue, own receipt and exact native prepare remain historical and do not reactivate either authority.",
          {"restored_active_status": restored_active_status, "restored_review": restored_review,
           "restored_issuer_show": issuer_observations["restored_revoked_show"], "holder_revocations": holder_revocations,
           "issuance_observations": issue_observations, "exact_native_prepare": guard_observations["exact_prepare_after_revoke"],
           "final_status": final_status, "all_detached_paths_restored": True,
           "issuer_revocation_revokes_existing_guard": False, "holder_revocation_rewrites_issuance": False,
           "exact_prepare_reactivates_guard": False})

    previous_statuses = {name: cli("original-write-status-" + name, WRITE_COMMAND, "status", ["--job", str(path)])
                         for name, path in prior_jobs.items()}
    previous_guarded_status = cli("original-guarded-status", GUARDED_COMMAND, "status",
                                  ["--job", str(private / "restore-case-retention-review-write-guarded-job")])
    original_lease_pages = {}
    for name, artifact_key in (("primary", "primary-final-released-page"), ("secondary", "secondary-final-released-page")):
        value = cli("original-lease-page-" + name, LEASE_COMMAND, "page",
                    ["--ledger", str(private / ("restore-case-retention-review-write-leases-" + name)), "--limit", "16"])
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
    managed_outcomes = [value for value in cli_outcomes if value["command"] == COMMAND]
    guarded_outcomes = [value for value in cli_outcomes if value["command"] == GUARDED_COMMAND]
    _require(len(stages) == 5 and len(cli_outcomes) == 80 and len(rejections) == 33
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 47
             and len(managed_outcomes) == 41 and sum(value["returncode"] == 0 for value in managed_outcomes) == 17
             and len(guarded_outcomes) == 22 and sum(value["returncode"] == 0 for value in guarded_outcomes) == 17
             and previous_statuses == prior_statuses and previous_guarded_status == prior_guarded
             and final_plan == plan_value and final_status["write"]["result"] == written_review == restored_review
             and originals_unchanged() and matches(source_before, source_after) and matches(live_before, live_after)
             and len(list(private.glob("*.credential"))) == 35 and controls(issuance) == sealed_before
             and all(not path.exists() for path in (invalid_issuer, unused_issuance, unused_job, unused_review, missing_plan))
             and all(path.exists() and not hidden.exists() for path, hidden in detached) and not discarded_addresses,
             "final managed, native, CLI, public history or preserved-control accounting differs from the fixed scenario")
    stage("preserved-history-and-explicit-limits",
          "All four new paths return. Earlier six legacy write statuses, the completed revoked guarded job, released lease heads, blocked restore jobs and original lstat metadata remain unchanged. The new review is completed, its holder and issuer are separately revoked, and the immutable issuance remains historical.",
          {"final_status": final_status, "restored_review": restored_review, "final_plan": final_plan,
           "original_write_statuses": previous_statuses, "original_guarded_status": previous_guarded_status,
           "original_lease_pages": original_lease_pages, "rejections": rejections,
           "all_detached_paths_restored": True, "original_control_metadata_unchanged": True,
           "original_directory_times_compared": False, "source_public_observations_unchanged": True,
           "live_public_observations_unchanged": True, "domain_writes_allowed": False,
           "archival_performed": False, "deletion_performed": False})

    artifacts = {
        PREFIX + "-plan-observations.json": {"created": plan_value, "initial_show": plan_show, "final_show": final_plan,
                                             "original_minimum_parameters_reused": True, "original_plan_modified": False},
        PREFIX + "-issuer-observations.json": {"created": issuer_value, "policy": policy, "observations": issuer_observations,
                                               "policy_refusals": policy_refusals},
        PREFIX + "-issuance-observations.json": {"observations": issue_observations, "offline": offline,
                                                 "guard": guard, "plan_id": plan_value["plan_id"],
                                                 "all_detached_paths_restored": True, "maximum_detached_path_count": 4,
                                                 "private_control_contents_read_by_demo": False,
                                                 "managed_failure_implies_no_native_guard": False},
        PREFIX + "-execution-observations.json": {"guard_observations": guard_observations, "executions": executions,
                                                  "statuses": statuses, "written_review": written_review, "restored_review": restored_review,
                                                  "native_refusals": native_refusals, "original_write_statuses": previous_statuses,
                                                  "original_guarded_status": previous_guarded_status,
                                                  "original_lease_pages": original_lease_pages,
                                                  "private_control_contents_read_by_demo": False,
                                                  "private_lease_database_read_by_demo": False,
                                                  "private_stage_contents_read_by_demo": False,
                                                  "legacy_direct_step_used_for_new_write": False},
        PREFIX + "-revocation-observations.json": {"issuer": issuer_revocations, "holder": holder_revocations,
                                                   "issuer_revocation_revokes_existing_guard": False,
                                                   "holder_revocation_rewrites_issuance": False},
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {"actual_complete_issuance_response_discarded": True,
                                          "exact_complete_issuance_reply_recovered": recovered,
                                          "recovered_after_native_completion": True, "recovered_after_issuer_revoke": True,
                                          "recovered_with_plan_review_job_offline": True,
                                          "own_receipt_read_with_issuer_also_offline": True,
                                          "comparison_digest_exported": False, "maximum_detached_path_count": 4,
                                          "partial_issuance_gap_injected": False, "cross_directory_atomicity_claimed": False},
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "six stages and ten public data files were not retained")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Exact-policy issuer for fresh guarded review writes with historical issuance recovery",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "managed_stage_count": len(stages),
            "new_public_file_count": 18, "total_public_file_count": 485,
            "new_managed_issuer_count": 1, "new_managed_issuer_policy_rule_count": 1,
            "new_managed_issuer_revocation_count": 1, "new_managed_issuance_count": 1,
            "active_managed_issuer_count": 0, "revoked_managed_issuer_count": 1,
            "new_guarded_write_job_count": 1, "total_guarded_write_job_count": 2,
            "new_legacy_write_job_count": 0, "total_legacy_write_job_count": 6, "total_write_job_count": 8,
            "new_retention_plan_count": 1, "total_retention_plan_count": 6,
            "new_review_record_count": 1, "total_review_record_count": 10,
            "new_private_credential_file_count": 2, "total_private_credential_file_count": 35,
            "administrator_and_holder_credentials_separate": True, "credentials_exported": False,
            "new_step_result_count": 3, "total_guarded_step_result_count": 6,
            "performed_phase_count": 3, "expected_phase_observation_count": 0,
            "new_guarded_revocation_count": 1, "total_guarded_revocation_count": 2,
            "active_guarded_write_count": 0, "revoked_guarded_write_count": 2,
            "total_write_catalog_count": 5, "new_write_catalog_count": 0,
            "total_lease_ledger_count": 5, "total_lease_operation_count": 20, "new_lease_operation_count": 0,
            "total_action_count": 12, "total_recorded_action_count": 10, "total_pending_action_count": 2,
            "total_native_permit_count": 7, "total_native_permit_revocation_count": 3,
            "total_old_issuer_count": 2, "total_old_issuer_revocation_count": 2, "total_old_issuance_count": 3,
            "new_cli_child_process_count": len(cli_outcomes), "new_successful_cli_count": 47,
            "new_refused_cli_count": len(rejections), "managed_command_count": len(managed_outcomes),
            "managed_successful_command_count": 17, "managed_refused_command_count": 24,
            "native_guarded_command_count": len(guarded_outcomes),
            "native_guarded_successful_command_count": 17, "native_guarded_refused_command_count": 5,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "discarded_complete_issuance_response_count": 1, "discarded_complete_issuance_response_recovered_exactly": recovered,
            "discarded_response_recovered_after_native_completion": True,
            "discarded_response_recovered_after_issuer_revoke": True, "maximum_offline_path_count": 4,
            "all_detached_paths_restored": True, "exact_issue_replay_needs_live_plan": False,
            "exact_issue_replay_needs_live_review": False, "exact_issue_replay_needs_live_job": False,
            "exact_issue_replay_requires_live_issuer": True, "exact_issue_replay_requires_administrator_credential": True,
            "own_receipt_requires_live_issuer": False, "issuer_revocation_blocks_new_issuance": True,
            "issuer_revocation_blocks_exact_issue_replay": False, "issuer_revocation_revokes_existing_guard": False,
            "native_phases_completed_after_issuer_revoke": 3, "holder_revocation_blocks_cached_step": True,
            "holder_revocation_blocks_new_step": True, "holder_revocation_rewrites_issuance": False,
            "exact_prepare_reactivates_guard": False, "existing_native_guard_adopted": False,
            "legacy_write_api_rejected": True, "legacy_controls_created": False,
            "legacy_direct_step_used_for_new_write": False, "original_control_metadata_unchanged": True,
            "old_blocked_job_metadata_unchanged": True, "original_blocked_jobs_still_blocked": True,
            "original_write_statuses_unchanged": True, "original_guarded_status_unchanged": True,
            "original_lease_pages_unchanged": True, "old_review_reader_compatible": True,
            "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
            "new_restore_attempt_count": 0, "domain_writes_allowed": False, "archival_performed": False,
            "deletion_performed": False, "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0,
            "injected_partial_write_count": 0, "interruption_recovery_demonstrated": False,
            "partial_issuance_gap_injected": False, "managed_failure_implies_no_native_guard": False,
            "cross_directory_atomicity_claimed": False, "expiry_path_demonstrated": False,
            "clock_regression_path_demonstrated": False, "partial_result_repair_demonstrated": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "local_capability_only": True, "external_identity_verified": False, "actor_authenticated": False,
            "old_authority_activated": False, "lease_enforced": False, "issuer_policy_enforced_during_native_step": False,
            "os_isolation_established": False, "external_fencing_established": False,
            "external_llm_used": False, "human_review_recorded": False, "proposed_code_executed": False,
            "measured_capacity": False, "process_overlap_measured": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "Exact issuance policy matches the public plan ID, principal, actor and verdict. Administrator and holder are self-declared labels and local bearer keys; external identity, semantic approval and domain authorization are not established.",
            "The managed path creates a fresh native guarded job. Existing legacy or guarded jobs and incomplete receipts are never adopted or repaired. Native guarded prepare remains an independently supported owner path; native step still has issuer_policy_enforced=false and lease_enforced=false.",
            "The issuer, issuance, job and review locks stay held until receipt synchronization under one cooperative deadline; plan capture briefly holds its own tail lock. This does not create an atomic transaction across directories.",
            "A native Guard11 is durable before its separate managed issuance record. If receipt sealing fails, a fully valid active native guard and its already saved holder key may remain usable after unlock. An outer issue error therefore does not prove no capability or effect exists. This gap is documented, not injected in this normal scenario.",
            "Issuer revocation blocks new managed issuance, preserving exact authenticated historical issue replay and old child guards. The holder completes three native phases after issuer revocation, then separately revokes its own guard. Neither revocation cancels already admitted effects or rewrites historical receipts.",
            "The actual complete successful issue response is discarded and recovered exactly after child completion and issuer revocation with new plan, review and job offline. Own receipt lookup also works while the issuer is offline. Compact anchors do not reconstruct absent full native control bodies.",
            "Four temporary new paths return in finally. Earlier private controls and public source/live histories remain unchanged. No archival, deletion, new restore, injected partial write, expiry wait or new fault worker is introduced.",
            "Only public responses, credential markers and lstat metadata are read by this driver. Private controls, lease SQL and staging bodies are not read or exported. External LLM, semantic quality, process overlap, throughput and maximum agent capacity are not measured.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Явная политика выдачи нового guarded review job", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала выполнен [предыдущий guarded сценарий](restore-case-retention-review-write-guarded-report.md); его 467 публичных файлов сохранены.",
        "Добавлены один issuer с точным правилом, одна квитанция выдачи, новый retention plan и guarded job/review, отдельные admin и holder credentials.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "Точная policy сопоставляет principal, actor, verdict и plan_id. Неверный admin, несовпадающие scopes и повторяющиеся rules отклонены до резервирования новых целей. Новая выдача не усыновляет существующий native guard.",
        "Полный успешный issuance-response действительно отброшен. Issuer отозван, после чего holder своим отдельным ключом выполняет три native фазы до completed. Отзыв издателя закрывает будущую выдачу, сохраняя правила уже созданного guard.",
        "При недоступных новых plan, review и job exact admin issue replay восстанавливает прежний ответ после completed и issuer revoke. Own receipt lookup возвращает ту же историю даже при недоступном issuer. Все четыре пути восстановлены в finally.",
        "Затем holder отдельно отзывает native доступ: новый и cached step отказывают. Exact managed issue, own receipt и exact native prepare сохраняют исторические значения и не возобновляют доступ.",
        "", "## Публичные основания", "",
        f"- [План]({PREFIX}-plan-observations.json), [issuer и policy]({PREFIX}-issuer-observations.json), [issuance]({PREFIX}-issuance-observations.json).",
        f"- [Native исполнение и прежние состояния]({PREFIX}-execution-observations.json), [два отдельных отзыва]({PREFIX}-revocation-observations.json), [восстановление ответа]({PREFIX}-reply-recovery.json).",
        f"- [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json), [источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы", "",
        "Шесть этапов, 80 новых ordinary CLI:47 успешных и33 ожидаемых отказа. Из них41 managed-команда:17 успешных и24 отказа;22 native guarded-команды:17 успешных и5 отказов. Всего901CLI и14 прежних interrupt workers,915 различных PID за последовательность запусков; одновременность и ёмкость не измерены.",
        "Runtime содержит6legacy write jobs и2guarded jobs,10reviews,6retention plans,5write catalogs,5lease DB/20операций. Прежние12actions/10results/2pending,7native permits/3revokes и2oldissuers/2revokes/3issuances сохранены. Новый managed issuer имеет1policy rule,1revoke и1issuance; nativeguarded jobs вместе имеют6phase results и2собственных отзыва. Credential-файлов35, в экспорт они не включены.",
        "Сохранение native Guard11 и issuance16 не атомарно. При отказе второй записи может остаться действующий guard с ранее сохранённым holder key. Outer issue error не доказывает отсутствие capability или эффекта. Этот промежуток не воспроизводился искусственно, усыновление и ремонт не реализованы.",
        "Issuer policy относится к выбранному пути выдачи. Native step не читает её: issuer_policy_enforced=false и lease_enforced=false сохраняются. Владелец файловой системы, private helpers и прямые syscalls остаются вне gate; OS isolation, внешняя identity и fencing отсутствуют.",
        "Credentials, private controls, pins и digests не экспортируются. Структурная корректность квитанций не подтверждает независимость агентов, истинность документации или право публикации/архивирования/удаления. Новые fault workers, partial writes, expiry waits, внешние LLM и исполнение предложенного кода не добавлялись.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
