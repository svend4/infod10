"""Demonstrate one sealed publication record for review write and issuance.

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
from .workspace_restore_case_retention_review_write_guarded_issuer_demo import run_workspace_restore_case_retention_review_write_guarded_issuer_demo
from .workspace_restore_case_retention_review_write_guarded_issuer_cli import _public_response as _managed_response
from .workspace_restore_case_retention_review_write_sealed_cli import _public_response as _sealed_response
from .workspace_restore_case_retention_review_write_leases_cli import _public_response as _lease_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-sealed-1"
COMMAND = "workspace-restore-case-retention-review-write-sealed"
MANAGED_COMMAND = "workspace-restore-case-retention-review-write-guarded-issuer"
GUARDED_COMMAND = "workspace-restore-case-retention-review-write-guarded"
PREFIX = "restore-case-retention-review-write-sealed"
RETENTION_COMMAND = "workspace-restore-case-retention"
REVIEW_COMMAND = "workspace-restore-case-retention-review"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"
CATALOG_COMMAND = "workspace-restore-case-retention-review-write-catalog"
LEASE_COMMAND = "workspace-restore-case-retention-review-write-leases"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write sealed demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_sealed_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Publish one complete sealed job and retain its immutable issuance evidence."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_guarded_issuer_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 485, "the preceding scenario did not produce 485 public files")
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").lstat().st_size
    markers: list[str] = []
    old_credentials = sorted(private.glob("*.credential"))
    _require(len(old_credentials) == 35, "the preceding scenario did not retain 35 credentials")
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
        "permit_record", "permit_lock", "guard_record", "guard_lock", "guard_directory", "guard_anchor", "seal_digest", "seal_record", "seal_lock",
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
        _require(command in {COMMAND, MANAGED_COMMAND, GUARDED_COMMAND, RETENTION_COMMAND, REVIEW_COMMAND, WRITE_COMMAND,
                             CATALOG_COMMAND, LEASE_COMMAND, "workspace-restore"}, "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore case retention review write sealed demonstration stopped: ordinary CLI timed out") from error
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
        validators = {COMMAND: _sealed_response, MANAGED_COMMAND: _managed_response, GUARDED_COMMAND: _guarded_response, RETENTION_COMMAND: _retention_response,
                      REVIEW_COMMAND: _review_response, WRITE_COMMAND: _write_response,
                      CATALOG_COMMAND: _catalog_response, LEASE_COMMAND: _lease_response}
        try:
            value = json.loads(stdout)
            value = validators[command](value, action) if command in validators else _public_status(value)
            value = public(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Restore case retention review write sealed demonstration stopped: invalid public CLI response") from error
        if discard:
            discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        return value

    plan, job, review = (private / (PREFIX + suffix) for suffix in ("-plan", "-job", "-review"))
    issuer = private / (PREFIX + "-parent")
    admin_credential, holder_credential = (private / (PREFIX + suffix) for suffix in ("-admin.credential", "-holder.credential"))
    unrelated_credential, other_credential = old_credentials[:2]
    unused_issuance = private / (PREFIX + "-unused-managed-issuance")
    unused_job, unused_review, missing_plan = (private / (PREFIX + suffix)
                                              for suffix in ("-unused-job", "-unused-review", "-unavailable-plan"))
    administrator = "simulated-sealed-parent-administrator"
    actor, principal, verdict = "simulated-sealed-reviewer", "simulated-sealed-holder", "defer"
    create_operation, issue_operation = PREFIX + "-create", PREFIX + "-issue"
    issuer_revoke_operation, holder_revoke_operation = PREFIX + "-parent-revoke", PREFIX + "-holder-revoke"
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
    prior_guarded_statuses = {
        "basic": json.loads((workspace / "restore-case-retention-review-write-guarded-execution-observations.json").read_text())["statuses"]["restored"],
        "managed": json.loads((workspace / "restore-case-retention-review-write-guarded-issuer-execution-observations.json").read_text())["statuses"]["revoked-child-status"],
    }
    prior_guarded_jobs = {"basic": private / "restore-case-retention-review-write-guarded-job",
                          "managed": private / "restore-case-retention-review-write-guarded-issuer-job"}
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
    seal_observations: dict[str, Any] = {}
    statuses: dict[str, Any] = {}
    executions: dict[str, Any] = {}
    offline: dict[str, Any] = {}
    policy_refusals: dict[str, Any] = {}
    legacy_refusals: dict[str, Any] = {}
    issuer_revocations: dict[str, Any] = {}
    holder_revocations: dict[str, Any] = {}
    error_prefix = "workspace_restore_case_retention_review_write_sealed_"
    credential_invalid, conflict, policy_denied, revoked = (error_prefix + suffix for suffix in
                                                           ("credential_invalid", "conflict", "policy_denied", "revoked"))
    managed_error = "workspace_restore_case_retention_review_write_guarded_issuer_"
    guarded_error = "workspace_restore_case_retention_review_write_guarded_"

    def originals_unchanged() -> bool:
        return (all(controls(path) == previous for path, previous in original_controls.items())
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == previous
                        for index, previous in old_observations.items()))

    def sealed(label: str, action: str, arguments: list[str], *,
               expected_error: str | None = None, discard: bool = False) -> Any:
        before = {path: controls(path) for path in (issuer, plan, job, review) if path.is_dir()}
        value = cli(label, COMMAND, action, arguments, expected_error=expected_error, discard=discard)
        if expected_error is not None or action in {"show", "receipt", "status"}:
            _require(before == {path: controls(path) for path in (issuer, plan, job, review) if path.is_dir()},
                     "a sealed refusal or observation changed owned controls")
        return value

    def managed(label: str, action: str, arguments: list[str], *, expected_error: str | None = None) -> Any:
        before = {path: controls(path) for path in (issuer, plan, job, review) if path.is_dir()}
        value = cli(label, MANAGED_COMMAND, action, arguments, expected_error=expected_error)
        if expected_error is not None or action == "show":
            _require(before == {path: controls(path) for path in (issuer, plan, job, review) if path.is_dir()},
                     "a reused issuer refusal or observation changed owned controls")
        return value

    def create_arguments(*, first: bool = False) -> list[str]:
        arguments = ["--issuer", str(issuer), "--operation-id", create_operation, "--administrator", administrator]
        for row in policy:
            arguments.extend(["--rule", row["rule_id"], row["principal"], row["actor"], row["verdict"], row["plan_id"]])
        return [*arguments, "--credential-out" if first else "--prepared-credential", str(admin_credential)]

    def issue_arguments(*, first: bool = False, selected_plan: Path = plan, selected_job: Path = job,
                        selected_review: Path = review, selected_principal: str = principal,
                        selected_actor: str = actor, selected_verdict: str = verdict,
                        selected_operation: str = issue_operation, admin: Path = admin_credential,
                        holder: Path = holder_credential) -> list[str]:
        return ["--issuer", str(issuer), "--plan", str(selected_plan), "--review", str(selected_review),
                "--job", str(selected_job), "--operation-id", selected_operation,
                "--principal", selected_principal, "--actor", selected_actor, "--verdict", selected_verdict,
                "--issuer-credential-file", str(admin),
                "--holder-credential-out" if first else "--prepared-holder-credential", str(holder)]

    def parent_revoke(label: str, *, key: Path = admin_credential, expected_error: str | None = None) -> Any:
        return managed(label, "revoke", ["--issuer", str(issuer), "--operation-id", issuer_revoke_operation,
                       "--credential-file", str(key)], expected_error=expected_error)

    def status(label: str, state: str, access: str = "active") -> dict[str, Any]:
        value = sealed(label, "status", ["--job", str(job)])
        _require(value["seal"] == receipt and value["access_state"] == access and value["write"]["state"] == state
                 and value["write"]["completed"] is (state == "completed")
                 and value["write"]["intended_review"]["plan"] == plan_value,
                 "sealed status changed its exact receipt, historical plan or expected phase")
        statuses[label] = value
        return value

    def step(label: str, state: str, *, key: Path = holder_credential, selected_operation: str | None = None,
             expected_error: str | None = None) -> Any:
        return sealed(label, "step", ["--job", str(job), "--operation-id",
                      step_operations[state] if selected_operation is None else selected_operation,
                      "--expected-state", state, "--credential-file", str(key)], expected_error=expected_error)

    def holder_revoke(label: str, *, key: Path = holder_credential,
                      selected_operation: str = holder_revoke_operation, expected_error: str | None = None) -> Any:
        return sealed(label, "revoke", ["--job", str(job), "--operation-id", selected_operation,
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
             "the new plan did not reuse only the original minimum public parameters")
    policy = [{"rule_id": "allow-sealed-review", "principal": principal, "actor": actor,
               "verdict": verdict, "plan_id": plan_value["plan_id"]}]
    issuer_value = managed("fresh-parent-issuer-create", "create", create_arguments(first=True))
    token = read_credential(admin_credential)
    markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    public(issuer_value)
    issuer_observations["initial_exact_create"] = managed("initial-exact-parent-create-replay", "create", create_arguments())
    issuer_observations["initial_show"] = managed("initial-active-parent-show", "show", ["--issuer", str(issuer)])
    policy_refusals["wrong_administrator_issue"] = sealed("wrong-administrator-before-sealed-issue", "issue",
                                                          issue_arguments(admin=unrelated_credential, holder=other_credential),
                                                          expected_error=credential_invalid)
    policy_refusals["principal"] = sealed("unlisted-principal-sealed-issue", "issue",
                                            issue_arguments(selected_principal="simulated-other-sealed-holder", holder=unrelated_credential),
                                            expected_error=policy_denied)
    policy_refusals["actor"] = sealed("nonmatching-actor-sealed-issue", "issue",
                                        issue_arguments(selected_actor="simulated-other-sealed-reviewer", holder=unrelated_credential),
                                        expected_error=policy_denied)
    policy_refusals["verdict"] = sealed("nonmatching-verdict-sealed-issue", "issue",
                                          issue_arguments(selected_verdict="reviewed", holder=unrelated_credential),
                                          expected_error=policy_denied)
    policy_refusals["plan_scope"] = sealed("nonmatching-plan-sealed-issue", "issue",
                                             issue_arguments(selected_plan=old_plan, holder=unrelated_credential), expected_error=policy_denied)
    policy_refusals["wrong_administrator_revoke"] = parent_revoke("wrong-administrator-before-parent-revoke", key=unrelated_credential,
                                                                 expected_error=managed_error + "credential_invalid")
    _require(issuer_observations["initial_exact_create"] == issuer_value and issuer_value["policy"] == policy
             and issuer_observations["initial_show"]["issuer"] == issuer_value
             and issuer_observations["initial_show"]["state"] == "active"
             and all(not path.exists() for path in (job, review, unused_issuance)) and originals_unchanged(),
             "a denied sealed issuance reserved targets or changed the exact parent policy")
    stage("exact-parent-policy",
          "A fresh retention plan supplies one exact rule for the reused parent issuer. Wrong administrator and four mismatching scopes refuse before any sealed job or review reservation. Parent policy remains local and owner-trusted.",
          {"retention_plan": plan_value, "plan_show": plan_show, "issuer": issuer_value, "policy": policy,
           "issuer_observations": issuer_observations, "policy_refusals": policy_refusals,
           "sealed_targets_reserved_by_refusal": False, "credentials_exported": False})

    sealed("complete-sealed-issue-response-discarded", "issue", issue_arguments(first=True), discard=True)
    token = read_credential(holder_credential)
    markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    seal_observations["issued_access"] = sealed("issued-seal-own-show", "show", ["--job", str(job)])
    receipt = seal_observations["issued_access"]["seal"]
    guard = receipt["guard"]
    prepared_status = status("issued-seal-prepared-status", "prepared")
    legacy_incomplete = "workspace_restore_case_retention_review_write_incomplete"
    legacy_refusals["legacy_step"] = cli("legacy-step-refuses-sealed-family", WRITE_COMMAND, "step", ["--job", str(job)], expected_error=legacy_incomplete)
    legacy_refusals["legacy_status"] = cli("legacy-status-refuses-sealed-family", WRITE_COMMAND, "status", ["--job", str(job)], expected_error=legacy_incomplete)
    legacy_refusals["legacy_prepare"] = cli("legacy-prepare-refuses-sealed-family", WRITE_COMMAND, "prepare",
                                            ["--plan", str(plan), "--review", str(review), "--job", str(job),
                                             "--operation-id", issue_operation, "--actor", actor, "--verdict", verdict], expected_error=legacy_incomplete)
    legacy_refusals["guarded_step"] = cli("native-guarded-step-refuses-sealed-family", GUARDED_COMMAND, "step",
                                          ["--job", str(job), "--operation-id", PREFIX + "-old-guarded-step",
                                           "--expected-state", "prepared", "--credential-file", str(holder_credential)], expected_error=guarded_error + "incomplete")
    legacy_refusals["guarded_status"] = cli("native-guarded-status-refuses-sealed-family", GUARDED_COMMAND, "status",
                                            ["--job", str(job)], expected_error=guarded_error + "incomplete")
    legacy_refusals["guarded_prepare"] = cli("native-guarded-prepare-refuses-sealed-family", GUARDED_COMMAND, "prepare",
                                             ["--plan", str(plan), "--review", str(review), "--job", str(job),
                                              "--operation-id", issue_operation, "--actor", actor, "--verdict", verdict,
                                              "--principal", principal, "--prepared-credential", str(holder_credential)], expected_error=guarded_error + "incomplete")
    legacy_refusals["managed_adoption"] = managed("managed-issue-cannot-adopt-sealed-job", "issue",
                                                  [*issue_arguments(), "--issuance", str(unused_issuance)], expected_error=managed_error + "conflict")
    legacy_refusals["sealed_adoption"] = sealed("sealed-issue-cannot-adopt-old-guarded-job", "issue",
                                                 issue_arguments(selected_job=prior_guarded_jobs["managed"]), expected_error=error_prefix + "incomplete")
    legacy_refusals["administrator_step"] = step("parent-admin-is-not-sealed-holder", "prepared", key=admin_credential,
                                                  expected_error=credential_invalid)
    legacy_refusals["unrelated_step"] = step("unrelated-key-is-not-sealed-holder", "prepared", key=unrelated_credential,
                                              expected_error=credential_invalid)
    _require(seal_observations["issued_access"]["access_state"] == "active" and receipt["issuer"] == issuer_value
             and receipt["rule"] == policy[0] and receipt["plan_id"] == plan_value["plan_id"]
             and receipt["operation_id"] == issue_operation and receipt["policy_authorized"] is True
             and guard["principal"] == principal and guard["write"]["actor"] == actor and guard["write"]["verdict"] == verdict
             and receipt["sealed_issuance_required_for_step"] is True and receipt["native_guarded_apis_rejected"] is True
             and not unused_issuance.exists() and originals_unchanged(),
             "sealed publication changed its exact scope or allowed adoption through an older public API")
    stage("sealed-family-and-legacy-refusals",
          "The complete successful sealed issue response is discarded. Own show exposes one receipt and its captured guard. Legacy write and native guarded APIs refuse this family; old managed issue cannot adopt it and sealed issue cannot adopt an old native job. Administrator and unrelated keys cannot step.",
          {"seal": receipt, "access": seal_observations["issued_access"], "status": prepared_status,
           "refusals": legacy_refusals, "actual_complete_issue_reply_discarded": True,
           "standalone_native_guard_written": False, "separate_issuance_directory_created": False,
           "precommit_partial_seal_path_demonstrated": False, "oldgap_protocols_upgraded": False})

    issuer_revocations["holder_not_administrator"] = parent_revoke("holder-is-not-parent-administrator-revoke", key=holder_credential,
                                                                  expected_error=managed_error + "credential_invalid")
    issuer_revocations["first"] = parent_revoke("parent-administrator-revoke")
    issuer_observations["revoked_show"] = managed("revoked-parent-show", "show", ["--issuer", str(issuer)])
    issuer_revocations["exact_replay"] = parent_revoke("exact-parent-revoke-replay")
    issuer_observations["revoked_exact_create"] = managed("revoked-parent-exact-create-replay", "create", create_arguments())
    issuer_revocations["new_issue"] = sealed("revoked-new-sealed-issue-before-targets", "issue",
                                              issue_arguments(selected_job=unused_job, selected_review=unused_review, selected_plan=missing_plan), expected_error=revoked)
    issuer_revocations["wrong_administrator"] = sealed("wrong-administrator-after-parent-revoke", "issue",
                                                        issue_arguments(admin=unrelated_credential), expected_error=credential_invalid)
    seal_observations["active_after_parent_revoke"] = sealed("sealed-child-active-after-parent-revoke", "show", ["--job", str(job)])
    executions["prepared"] = step("prepared-holder-step-after-parent-revoke", "prepared")
    ready_status = status("after-prepared-step", "ready")
    changed_prepared = step("changed-operation-in-prepared-slot", "prepared", selected_operation=PREFIX + "-other-step-prepared", expected_error=conflict)
    cross_phase = step("prepared-operation-used-for-ready-slot", "ready", selected_operation=step_operations["prepared"], expected_error=conflict)
    executions["ready"] = step("ready-holder-step-after-parent-revoke", "ready")
    linked_status = status("after-ready-step", "linked")
    changed_ready = step("changed-operation-in-ready-slot", "ready", selected_operation=PREFIX + "-other-step-ready", expected_error=conflict)
    executions["linked"] = step("linked-holder-step-after-parent-revoke", "linked")
    completed_status = status("completed-status-after-parent-revoke", "completed")
    written_review = cli("completed-old-review-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    _require(issuer_revocations["first"] == issuer_observations["revoked_show"] == issuer_revocations["exact_replay"]
             and issuer_revocations["first"]["state"] == "revoked" and issuer_observations["revoked_exact_create"] == issuer_value
             and seal_observations["active_after_parent_revoke"] == seal_observations["issued_access"]
             and all(value["observation"]["outcome"] == "phase_performed" and value["observation"]["effect_performed"] is True
                     for value in executions.values()) and written_review == completed_status["write"]["result"]
             and all(not path.exists() for path in (unused_issuance, unused_job, unused_review, missing_plan)),
             "parent revocation changed a sealed child or an old slot identity admitted another phase")
    stage("parent-revoked-sealed-holder-completes",
          "Parent administrator revocation blocks new sealed issuance before missing target reads. The independent holder completes all three sealed phases after that revocation. Sealed result slots reject changed IDs and cross-phase reuse, and the old review reader accepts completed history.",
          {"issuer_revocations": issuer_revocations, "parent_show": issuer_observations["revoked_show"],
           "exact_parent_create": issuer_observations["revoked_exact_create"],
           "active_child_access": seal_observations["active_after_parent_revoke"], "executions": executions,
           "ready_status": ready_status, "linked_status": linked_status, "completed_status": completed_status,
           "written_review": written_review, "changed_prepared": changed_prepared, "cross_phase": cross_phase,
           "changed_ready": changed_ready, "parent_revocation_revokes_existing_sealed_job": False,
           "sealed_phases_after_parent_revoke": 3})

    detached: list[tuple[Path, Path]] = []
    sealed_before = controls(job)
    try:
        for path, suffix in ((plan, "plan"), (review, "review")):
            hidden = private / (PREFIX + "-offline-" + suffix)
            _require(not hidden.exists(), "a temporary sealed target sibling already exists")
            path.rename(hidden)
            detached.append((path, hidden))
        offline["recovered"] = sealed("completed-offline-exact-sealed-issue-recovered", "issue", issue_arguments())
        recovered = (discarded_addresses.pop("complete-sealed-issue-response-discarded")
                     == hashlib.sha256(canonical(offline["recovered"]).encode("utf-8")).hexdigest())
        issue_observations["recovered"] = offline["recovered"]
        offline["own_receipt"] = sealed("completed-offline-own-seal-receipt", "receipt", ["--job", str(job)])
        offline["other_holder"] = sealed("offline-replay-other-holder-credential", "issue", issue_arguments(holder=unrelated_credential), expected_error=conflict)
        offline["other_principal"] = sealed("offline-replay-other-principal", "issue", issue_arguments(selected_principal="simulated-other-sealed-holder"), expected_error=conflict)
        offline["other_actor"] = sealed("offline-replay-other-actor", "issue", issue_arguments(selected_actor="simulated-other-sealed-reviewer"), expected_error=conflict)
        offline["other_verdict"] = sealed("offline-replay-other-verdict", "issue", issue_arguments(selected_verdict="reviewed"), expected_error=conflict)
        offline["other_operation"] = sealed("offline-replay-other-operation", "issue", issue_arguments(selected_operation=PREFIX + "-other-issue"), expected_error=conflict)
        offline["other_plan"] = sealed("offline-replay-other-plan", "issue", issue_arguments(selected_plan=old_plan), expected_error=conflict)
        offline["other_review"] = sealed("offline-replay-other-review", "issue", issue_arguments(selected_review=unused_review), expected_error=conflict)
        offline["wrong_administrator"] = sealed("offline-wrong-administrator-sealed-issue", "issue", issue_arguments(admin=unrelated_credential), expected_error=credential_invalid)
        offline["new_issue_after_parent_revoke"] = sealed("offline-new-sealed-issue-after-parent-revoke", "issue",
                                                          issue_arguments(selected_job=unused_job, selected_review=unused_review, selected_plan=missing_plan), expected_error=revoked)
        offline["active_show"] = sealed("completed-offline-active-seal-show", "show", ["--job", str(job)])
        offline["prepared"] = step("completed-offline-prepared-step-replay", "prepared")
        offline["ready"] = step("completed-offline-ready-step-replay", "ready")
        offline["linked"] = step("completed-offline-linked-step-replay", "linked")
        hidden_issuer = private / (PREFIX + "-offline-parent")
        _require(not hidden_issuer.exists(), "a temporary parent sibling already exists")
        issuer.rename(hidden_issuer)
        detached.append((issuer, hidden_issuer))
        offline["receipt_without_parent"] = sealed("own-seal-receipt-with-parent-also-offline", "receipt", ["--job", str(job)])
        offline["show_without_parent"] = sealed("own-seal-show-with-parent-also-offline", "show", ["--job", str(job)])
        offline["prepared_without_parent"] = step("own-prepared-replay-with-parent-also-offline", "prepared")
        offline["ready_without_parent"] = step("own-ready-replay-with-parent-also-offline", "ready")
        offline["linked_without_parent"] = step("own-linked-replay-with-parent-also-offline", "linked")
        offline["wrong_holder_cached_step"] = step("offline-wrong-holder-cached-sealed-step", "prepared", key=unrelated_credential, expected_error=credential_invalid)
        offline["wrong_holder_revoke"] = holder_revoke("offline-wrong-holder-sealed-revoke", key=unrelated_credential, expected_error=credential_invalid)
        _require(recovered and offline["recovered"] == offline["own_receipt"] == offline["receipt_without_parent"] == receipt
                 and offline["active_show"] == offline["show_without_parent"] == seal_observations["issued_access"]
                 and all(offline[state] == offline[state + "_without_parent"] == executions[state] for state in step_operations)
                 and controls(job) == sealed_before and all(not path.exists() for path, _ in detached) and job.exists(),
                 "own seal replay needed live targets, changed controls or lost a historical phase identity")
        stage("own-seal-recovery-without-live-targets",
              "After completion and parent revocation, exact issue recovers the complete discarded response with plan and review offline. The parent is then also absent; own receipt, own show and all three cached steps work from the still present sealed job. Wrong holder keys fail before live reads.",
              {"offline": offline, "actual_complete_issue_reply_recovered_exactly": recovered,
               "maximum_detached_path_count": 3, "own_job_online": True, "sealed_control_metadata_unchanged": True,
               "comparison_digest_exported": False, "precommit_partial_seal_path_demonstrated": False})

        holder_revocations["first"] = holder_revoke("offline-holder-sealed-revoke")
        holder_revocations["show"] = sealed("offline-revoked-seal-own-show", "show", ["--job", str(job)])
        revoked_before = controls(job)
        holder_revocations["exact_replay"] = holder_revoke("offline-exact-sealed-revoke-replay")
        holder_revocations["new_step"] = step("holder-revoked-new-sealed-step", "prepared", selected_operation=PREFIX + "-after-holder-revoke", expected_error=revoked)
        holder_revocations["cached_step"] = step("holder-revoked-cached-sealed-step", "prepared", expected_error=revoked)
        holder_revocations["wrong_cached_key"] = step("revoked-wrong-holder-cached-sealed-step", "prepared", key=unrelated_credential, expected_error=credential_invalid)
        holder_revocations["changed_operation"] = holder_revoke("changed-sealed-revoke-operation", selected_operation=PREFIX + "-other-holder-revoke", expected_error=conflict)
        holder_revocations["own_receipt"] = sealed("offline-own-seal-receipt-after-holder-revoke", "receipt", ["--job", str(job)])
        _require(holder_revocations["first"] == holder_revocations["show"] == holder_revocations["exact_replay"]
                 and holder_revocations["first"]["access_state"] == "revoked" and holder_revocations["first"]["seal"] == receipt
                 and holder_revocations["own_receipt"] == receipt and controls(job) == revoked_before
                 and all(not path.exists() for path, _ in detached) and job.exists(),
                 "holder revocation changed the immutable receipt or required an absent issuer, plan or review")
        stage("holder-revocation-with-offline-issuer",
              "With the parent, plan and review absent, the holder separately revokes this sealed job. New and cached protected steps refuse, wrong keys fail authentication, and own historical receipt stays unchanged. Exact revoke does not rewrite either record.",
              {"holder_revocations": holder_revocations, "all_three_targets_absent": True, "own_job_online": True,
               "revocation_blocks_new_step": True, "revocation_blocks_cached_step": True,
               "holder_revocation_rewrites_sealed_issuance": False, "sealed_control_metadata_unchanged_after_replay": True})
    finally:
        for path, hidden in reversed(detached):
            if hidden.exists():
                hidden.rename(path)

    final_status = status("restored-completed-revoked-seal-status", "completed", "revoked")
    restored_review = cli("restored-old-review-reader", REVIEW_COMMAND, "show", ["--review", str(review)])
    issue_observations["restored_own_receipt"] = sealed("restored-own-seal-receipt", "receipt", ["--job", str(job)])
    issue_observations["exact_issue_after_holder_revoke"] = sealed("exact-sealed-issue-after-holder-revoke", "issue", issue_arguments())
    issuer_observations["restored_revoked_show"] = managed("restored-revoked-parent-show", "show", ["--issuer", str(issuer)])
    holder_revocations["restored_exact_replay"] = holder_revoke("restored-exact-sealed-revoke-replay")
    previous_statuses = {name: cli("original-write-status-" + name, WRITE_COMMAND, "status", ["--job", str(path)])
                         for name, path in prior_jobs.items()}
    previous_guarded_statuses = {name: cli("original-guarded-status-" + name, GUARDED_COMMAND, "status", ["--job", str(path)])
                                for name, path in prior_guarded_jobs.items()}
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
    sealed_outcomes = [value for value in cli_outcomes if value["command"] == COMMAND]
    parent_outcomes = [value for value in cli_outcomes if value["command"] == MANAGED_COMMAND]
    _require(len(stages) == 5 and len(cli_outcomes) == 92 and len(rejections) == 40
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 52
             and len(sealed_outcomes) == 57 and sum(value["returncode"] == 0 for value in sealed_outcomes) == 29
             and len(parent_outcomes) == 11 and sum(value["returncode"] == 0 for value in parent_outcomes) == 8
             and previous_statuses == prior_statuses and previous_guarded_statuses == prior_guarded_statuses
             and final_plan == plan_value and final_status["write"]["result"] == written_review == restored_review
             and issuer_observations["restored_revoked_show"] == issuer_revocations["first"]
             and holder_revocations["restored_exact_replay"] == holder_revocations["first"]
             and all(value == receipt for value in issue_observations.values())
             and originals_unchanged() and matches(source_before, source_after) and matches(live_before, live_after)
             and len(list(private.glob("*.credential"))) == 37
             and all(not path.exists() for path in (unused_issuance, unused_job, unused_review, missing_plan))
             and all(path.exists() and not hidden.exists() for path, hidden in detached) and not discarded_addresses,
             "final sealed, CLI, historical receipt or original-control accounting differs from the fixed scenario")
    stage("restored-history-and-one-record-boundary",
          "All three temporary paths return. Six legacy write statuses, both old native guarded jobs, released lease pages, blocked restore jobs and original lstat metadata remain unchanged. The new sealed review is completed, parent and holder are separately revoked, and every own receipt remains the same historical publication.",
          {"final_status": final_status, "restored_review": restored_review, "final_plan": final_plan,
           "original_write_statuses": previous_statuses, "original_guarded_statuses": previous_guarded_statuses,
           "original_lease_pages": original_lease_pages, "rejections": rejections,
           "all_detached_paths_restored": True, "original_control_metadata_unchanged": True,
           "original_directory_times_compared": False, "source_public_observations_unchanged": True,
           "live_public_observations_unchanged": True, "domain_writes_allowed": False,
           "archival_performed": False, "deletion_performed": False,
           "precommit_partial_seal_path_demonstrated": False, "postcommit_response_loss_demonstrated": True,
           "oldgap_protocols_upgraded": False, "all_power_loss_schedules_covered": False})

    artifacts = {
        PREFIX + "-plan-observations.json": {"created": plan_value, "initial_show": plan_show, "final_show": final_plan,
                                             "original_minimum_parameters_reused": True, "original_plan_modified": False},
        PREFIX + "-issuer-observations.json": {"created": issuer_value, "policy": policy, "observations": issuer_observations,
                                               "policy_refusals": policy_refusals, "reused_old_managed_issuer_api": True},
        PREFIX + "-seal-observations.json": {"observations": issue_observations, "access_observations": seal_observations,
                                             "offline": offline, "seal": receipt, "guard": guard,
                                             "plan_id": plan_value["plan_id"], "all_detached_paths_restored": True,
                                             "maximum_detached_path_count": 3, "own_job_online": True,
                                             "standalone_native_guard_written": False,
                                             "separate_issuance_directory_created": False,
                                             "private_control_contents_read_by_demo": False},
        PREFIX + "-execution-observations.json": {"executions": executions, "statuses": statuses,
                                                  "written_review": written_review, "restored_review": restored_review,
                                                  "legacy_refusals": legacy_refusals,
                                                  "original_write_statuses": previous_statuses,
                                                  "original_guarded_statuses": previous_guarded_statuses,
                                                  "original_lease_pages": original_lease_pages,
                                                  "private_control_contents_read_by_demo": False,
                                                  "private_lease_database_read_by_demo": False,
                                                  "private_stage_contents_read_by_demo": False,
                                                  "legacy_direct_step_used_for_new_write": False},
        PREFIX + "-revocation-observations.json": {"issuer": issuer_revocations, "holder": holder_revocations,
                                                   "issuer_revocation_revokes_existing_sealed_job": False,
                                                   "holder_revocation_rewrites_sealed_issuance": False},
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {"actual_complete_issue_response_discarded": True,
                                          "exact_complete_issue_reply_recovered": recovered,
                                          "recovered_after_sealed_completion": True,
                                          "recovered_after_parent_revoke": True,
                                          "recovered_with_plan_review_offline": True,
                                          "own_receipt_read_with_parent_also_offline": True,
                                          "cached_three_steps_with_parent_plan_review_offline": True,
                                          "comparison_digest_exported": False,
                                          "maximum_detached_path_count": 3, "own_job_online": True,
                                          "precommit_partial_seal_path_demonstrated": False,
                                          "postcommit_response_loss_demonstrated": True,
                                          "oldgap_protocols_upgraded": False,
                                          "cross_directory_atomicity_claimed": False},
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "six stages and ten public data files were not retained")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "One sealed publication record for guarded review write and exact parent issuance evidence",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "sealed_stage_count": len(stages),
            "new_public_file_count": 18, "total_public_file_count": 503,
            "total_public_json_file_count": 481, "total_public_markdown_file_count": 22,
            "new_parent_issuer_count": 1, "new_parent_policy_rule_count": 1,
            "new_parent_issuer_revocation_count": 1, "total_managed_issuer_count": 2,
            "total_managed_issuer_revocation_count": 2, "total_separate_managed_issuance_count": 1,
            "new_separate_issuance_directory_count": 0, "new_standalone_native_guard_count": 0,
            "new_sealed_write_job_count": 1, "total_sealed_write_job_count": 1,
            "total_native_guarded_write_job_count": 2, "total_logical_guard_count": 3,
            "new_legacy_write_job_count": 0, "total_legacy_write_job_count": 6,
            "total_logical_write_job_count": 9, "new_retention_plan_count": 1,
            "total_retention_plan_count": 7, "new_review_record_count": 1, "total_review_record_count": 11,
            "new_private_credential_file_count": 2, "total_private_credential_file_count": 37,
            "administrator_and_holder_credentials_separate": True, "credentials_exported": False,
            "new_sealed_step_result_count": 3, "total_native_guarded_step_result_count": 6,
            "performed_sealed_phase_count": 3, "expected_phase_observation_count": 0,
            "new_sealed_holder_revocation_count": 1, "total_native_guarded_revocation_count": 2,
            "active_sealed_write_count": 0, "revoked_sealed_write_count": 1,
            "total_write_catalog_count": 5, "new_write_catalog_count": 0,
            "total_lease_ledger_count": 5, "total_lease_operation_count": 20, "new_lease_operation_count": 0,
            "total_action_count": 12, "total_recorded_action_count": 10, "total_pending_action_count": 2,
            "total_native_permit_count": 7, "total_native_permit_revocation_count": 3,
            "total_old_issuer_count": 2, "total_old_issuer_revocation_count": 2, "total_old_issuance_count": 3,
            "new_cli_child_process_count": len(cli_outcomes), "new_successful_cli_count": 52,
            "new_refused_cli_count": len(rejections), "sealed_command_count": len(sealed_outcomes),
            "sealed_successful_command_count": 29, "sealed_refused_command_count": 28,
            "reused_parent_command_count": len(parent_outcomes),
            "reused_parent_successful_command_count": 8, "reused_parent_refused_command_count": 3,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "discarded_complete_issue_response_count": 1,
            "discarded_complete_issue_response_recovered_exactly": recovered,
            "recovered_after_sealed_completion": True, "recovered_after_parent_revoke": True,
            "recovered_with_plan_review_offline": True, "own_receipt_with_parent_also_offline": True,
            "cached_three_steps_with_parent_plan_review_offline": True,
            "maximum_detached_path_count": 3, "all_detached_paths_restored": True, "own_job_online": True,
            "own_job_offline_recovery_demonstrated": False, "comparison_digest_exported": False,
            "one_seal_contains_full_logical_guard_and_issuance_evidence": True,
            "sealed_issuance_required_for_step": True, "standalone_native_guard_written": False,
            "separate_issuance_directory_created": False, "parent_revocation_blocks_new_issuance": True,
            "parent_revocation_blocks_exact_issue_replay": False,
            "parent_revocation_revokes_existing_sealed_job": False,
            "sealed_phases_completed_after_parent_revoke": 3, "holder_revocation_blocks_cached_step": True,
            "holder_revocation_blocks_new_step": True, "holder_revocation_rewrites_sealed_issuance": False,
            "exact_issue_replay_reactivates_sealed_job": False,
            "old_managed_issue_adopts_sealed_job": False, "new_sealed_issue_adopts_old_guarded_job": False,
            "legacy_write_api_rejected": True, "native_guarded_api_rejected": True,
            "legacy_controls_created": False, "native_guarded_controls_created": False,
            "legacy_direct_step_used_for_new_write": False, "original_control_metadata_unchanged": True,
            "original_directory_times_compared": False, "old_blocked_job_metadata_unchanged": True,
            "original_blocked_jobs_still_blocked": True, "original_write_statuses_unchanged": True,
            "original_guarded_statuses_unchanged": True, "original_lease_pages_unchanged": True,
            "lease_page_fresh_checked_at_compared": False, "old_review_reader_compatible": True,
            "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
            "new_restore_attempt_count": 0, "domain_writes_allowed": False,
            "archival_performed": False, "deletion_performed": False,
            "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0,
            "injected_partial_write_count": 0, "interruption_recovery_demonstrated": False,
            "precommit_partial_seal_path_demonstrated": False, "postcommit_response_loss_demonstrated": True,
            "postcommit_issue_error_implies_no_active_sealed_job": False, "oldgap_protocols_upgraded": False,
            "cross_directory_atomicity_claimed": False, "all_power_loss_schedules_covered": False,
            "expiry_path_demonstrated": False, "clock_regression_path_demonstrated": False,
            "partial_result_repair_demonstrated": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "local_capability_only": True, "external_identity_verified": False, "actor_authenticated": False,
            "old_authority_activated": False, "lease_enforced": False, "live_issuer_policy_enforced": False,
            "os_isolation_established": False, "external_fencing_established": False,
            "external_llm_used": False, "human_review_recorded": False, "proposed_code_executed": False,
            "measured_capacity": False, "process_overlap_measured": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "The reused parent issuer matches principal, actor, verdict and public plan ID exactly. Administrator and holder use separate local bearer keys and self-declared labels. External identity, semantic approval and domain authorization are not established.",
            "The new job stores the full logical Guard11 and exact captured issuance evidence in one immutable Seal14. No old legacy/native guarded intent or lock and no separate issuance directory are created. Supported old public APIs refuse this family; old managed issue cannot adopt it and new sealed issue cannot adopt an old native guard.",
            "A new step validates the complete seal before holder authentication, revocation checks and protected phase work. Incomplete or absent seal controls are not repaired or adopted. Precommit partial publication failure closure is supported by source inspection only; this normal scenario does not inject that path.",
            "One canonical seal publication removes the old standalone active guard versus later issuance-record dependency for this new family. A postcommit issue error may still leave a complete active sealed job with issuance evidence. O_EXCL publication and fsync do not prove rollback, atomicity across directories or every power-loss schedule. Older native and managed formats retain their earlier gap and behavior.",
            "Parent revocation blocks future new issue while exact authenticated historical issue replay and existing sealed holders continue. All three new phases complete after parent revocation. Holder revocation separately blocks new and cached protected steps; neither kind of revocation recalls an admitted effect or rewrites historical receipts.",
            "The actual complete successful issue response is discarded, then recovered exactly after completion and parent revocation with plan and review offline. With the parent also absent, own receipt, own show and all three cached step responses use the still present own sealed job. Exact issue replay still requires the live parent and administrator key; no own-job-offline recovery is claimed.",
            "At most three new referenced paths are temporarily hidden and all return in finally. Holder revocation works while all three are absent. Earlier control metadata, old write/guarded statuses and source/live public observations remain unchanged; only fresh checked_at is excluded when comparing old lease pages. Store initialization and initial public observations precede metadata capture.",
            "The driver reads only validated public responses, credential markers and lstat metadata. Private controls, lease SQL and staging bodies are not read or exported. No new restore, archival, deletion, fault worker, partial write, expiry wait or pending lifecycle is introduced. External LLM, semantic quality, independent agent identity, overlap, throughput, maximum capacity and OS isolation are not demonstrated.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Одна sealed запись для review write и основания выдачи", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала выполнен [предыдущий сценарий managed issuer](restore-case-retention-review-write-guarded-issuer-report.md); его 485 публичных файлов сохранены.",
        "Добавлены один родительский issuer с точным правилом, новый retention plan и sealed job/review, отдельные admin и holder credentials. Полный логический Guard11 и основания выдачи находятся в одной immutable Seal14; отдельные native guard и issuance directory не создаются.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Наблюдаемый результат", "",
        "Неверный admin и четыре несовпадающих scope отклонены до резервирования job/review. Прежние legacy и native guarded prepare/status/step отвергают sealed family. Старый managed issue не усыновляет sealed job и не резервирует отдельную issuance directory; sealed issue не усыновляет прежний native guarded job.",
        "Полный успешный issue-response действительно отброшен. Родительский issuer отозван до всех трёх новых шагов; holder своим отдельным ключом доводит sealed review до completed. Отзыв родителя закрывает будущую выдачу, сохраняя уже выданное право holder.",
        "При недоступных plan и review exact admin issue replay восстанавливает целый прежний ответ после completed и parent revoke. Затем скрывается также parent: own receipt, own show и три cached step возвращают прежнюю историю из доступного собственного sealed job. Неверные holder keys отвергаются.",
        "Пока все три referenced пути отсутствуют, holder отзывает sealed доступ: новые и cached steps отказывают, exact revoke повторяет прежний ответ, own receipt сохраняет исторические основания. Все три пути восстанавливаются в finally. Exact issue после holder revoke не возобновляет доступ.",
        "", "## Публичные основания", "",
        f"- [План]({PREFIX}-plan-observations.json), [родитель и policy]({PREFIX}-issuer-observations.json), [seal и восстановление]({PREFIX}-seal-observations.json).",
        f"- [Исполнение и прежние состояния]({PREFIX}-execution-observations.json), [два отзыва]({PREFIX}-revocation-observations.json), [восстановление целого ответа]({PREFIX}-reply-recovery.json).",
        f"- [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json), [источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы", "",
        "Шесть этапов, 92 новых ordinary CLI: 52 успешных и 40 ожидаемых отказов. Среди них 57 sealed команд (29/28) и 11 команд прежнего parent issuer (8/3). Итого 993 CLI и 14 прежних interrupt workers, 1007 различных PID за последовательность запусков; одновременность и ёмкость не измерены. Публичный набор: 503 файла, 481 JSON и 22 Markdown.",
        "Runtime содержит 6 legacy write jobs, 2 прежних native guarded jobs и 1 sealed job; всего 9 логических write jobs, 11 reviews, 7 retention plans. Прежние 5 write catalogs, 5 lease DB/20 операций, 12 actions/10 results/2 pending, 7 native permits/3 revokes и 2 old issuers/2 revokes/3 issuances сохранены. Managed parent issuers теперь 2 с 2 отзывами; отдельная прежняя issuance остаётся одна. Новый seal содержит третий логический guard, 3 новых phase results и 1 holder revoke; прежние native guards сохраняют 6 results и 2 revokes. Credential-файлов 37, в экспорт они не включены.",
        "В новом семействе отсутствует промежуточный отдельно опубликованный native guard без issuance evidence. Partial/absent seal закрыт для supported step; этот путь проверен по исходникам и искусственно не воспроизводился. Ошибка после полного commit может оставить действующий sealed job с основаниями выдачи. O_EXCL и fsync не доказывают rollback, междиректорную атомарность и все power-loss сценарии. Прежние форматы и их известный промежуток не обновлены.",
        "Exact issue replay требует доступного parent и admin key; own receipt/show/cached steps требуют доступного собственного job. Parent, plan и review могут отсутствовать в проверенных own-only операциях. FS owner, private helpers и прямые syscalls остаются вне gate; внешняя identity, смысловое принятие, OS isolation, lease enforcement и fencing отсутствуют.",
        "Driver читает публичные ответы, credential markers и lstat; private controls, lease SQL и staging bodies не читает. Credentials, pins и digests не экспортируются. Новые fault workers, partial writes, expiry waits, pending lifecycle, внешние LLM и исполнение предложенного кода не добавлялись.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
