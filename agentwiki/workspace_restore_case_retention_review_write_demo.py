"""Demonstrate bounded publication of two exact advisory review records.

Only ordinary public commands, validated responses and lstat metadata are used.
Two step processes are launched before either response is collected. No new
fault worker, injected partial write, forced interruption or private body read
is introduced. Temporarily detached live inputs always return in finally.
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
from .workspace_restore_case_catalog_cli import _public_response as _catalog_response
from .workspace_restore_case_retention_cli import _public_response as _retention_response
from .workspace_restore_case_retention_review_cli import _public_response as _review_response
from .workspace_restore_case_retention_review_demo import run_workspace_restore_case_retention_review_demo
from .workspace_restore_case_retention_review_write_cli import _public_response as _write_response
from .workspace_restore_cases_cli import _public_response as _case_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-1"
COMMAND = "workspace-restore-case-retention-review-write"
PREFIX = "restore-case-retention-review-write"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_demo(workspace: str | Path,
                                                           workers: int = 3) -> dict[str, Any]:
    """Prepare and publish two reviews through supported ordinary step states."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 341, "the preceding scenario did not produce 341 public files")
    plans = json.loads((workspace / "restore-case-retention-plans.json").read_text())
    baseline_reviews = json.loads((workspace / "restore-case-retention-review-records.json").read_text())
    baseline_cases = json.loads((workspace / "restore-cases-cases.json").read_text())
    baseline_details = [value["completed_show"] for value in baseline_cases]
    baseline_pages = json.loads((workspace / "restore-case-catalog-pages.json").read_text())
    plan_directories = {
        "minimum": private / "restore-case-retention-minimum",
        "long": private / "restore-case-retention-long",
        "missing_case": private / "restore-case-retention-missing-case",
        "missing_attempt": private / "restore-case-retention-missing-attempt",
    }
    review_directories = {name: private / ("restore-case-retention-review-written-" + name)
                          for name in ("minimum", "long")}
    job_directories = {name: private / (PREFIX + "-" + name) for name in review_directories}
    old_review_directories = {
        "minimum": private / "restore-case-retention-review-minimum",
        "missing_attempt": private / "restore-case-retention-review-missing-attempt",
    }
    actors = {"minimum": "simulated-write-operator-a", "long": "simulated-write-operator-b"}
    verdicts = {"minimum": "reviewed", "long": "defer"}
    operations = {name: PREFIX + "-" + name for name in review_directories}
    catalog_a, catalog_b = private / "restore-case-catalog-main", private / "restore-case-catalog-expanded"
    case_directories = [private / f"restore-case-{index:02d}" for index in range(1, 4)]
    case_directories.append(private / "restore-case-catalog-late")
    attempt_jobs = [private / f"case-attempt-job-{index:02d}" for index in range(1, 4)]
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").lstat().st_size
    markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "job_directory", "source_bundle", "original_manifest", "restore_manifest",
        "owner", "staging_path", "sha256", "schema_fingerprint", "request_digest", "plan_path",
        "plan_directory", "catalog_path", "case_path", "case_directory", "catalog_directory", "parent", "pins",
        "review_path", "review_directory", "plan_anchor", "plan_source", "review_record", "review_lock", "stage_pin",
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

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(), "access": store.snapshot_access()}

    def matches(before: dict[str, Any], after: dict[str, Any]) -> bool:
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {key: child for key, child in value["governance"].items() if key != "observed_at"}}
        return canonical(comparable(before)) == canonical(comparable(after))

    def controls(path: Path) -> dict[str, Any]:
        directory = path.lstat()
        return {"directory": (directory.st_dev, directory.st_ino, directory.st_uid, stat.S_IMODE(directory.st_mode)),
                "files": {entry.name: (details.st_dev, details.st_ino, details.st_uid, details.st_mode,
                                        details.st_nlink, details.st_size, details.st_mtime_ns, details.st_ctime_ns)
                          for entry in sorted(path.iterdir()) for details in [entry.lstat()]}}

    def write_controls(name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        return controls(job_directories[name]), controls(review_directories[name])

    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    statuses: dict[str, Any] = {}
    intended: dict[str, Any] = {}
    recovered: dict[str, bool] = {}
    discarded_addresses: dict[str, str] = {}

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"{PREFIX}-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def launch(command: str, action: str, arguments: list[str]) -> subprocess.Popen[str]:
        _require(command in {COMMAND, "workspace-restore-case-retention-review", "workspace-restore-case-retention",
                             "workspace-restore-case-catalog", "workspace-restore-case", "workspace-restore"},
                 "unsupported ordinary command")
        return subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                                shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    def collect(process: subprocess.Popen[str], label: str, command: str, action: str, *,
                expected_error: str | None = None, discard: bool = False) -> Any:
        try:
            stdout, stderr = process.communicate(timeout=90)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate(timeout=15)
            raise RuntimeError("Restore case retention review write demonstration stopped: ordinary CLI timed out") from error
        code_match = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
        code = code_match.group(1) if code_match else None
        cli_outcomes.append({"label": label, "command": command, "action": action,
                             "child_process_id": process.pid, "returncode": process.returncode,
                             "error_code": code, "stderr_empty": not stderr,
                             "completed_child_process": True, "shell": False,
                             "business_response_discarded": discard})
        if expected_error is not None:
            _require(not discard and process.returncode != 0 and code == expected_error and not stdout,
                     "an ordinary command did not return its expected safe refusal")
            refusal = {"label": label, "command": command, "action": action, "code": code, "rejected": True}
            rejections.append(refusal)
            return refusal
        _require(process.returncode == 0 and not stderr, "an ordinary CLI command failed")
        try:
            value = json.loads(stdout)
            if command == COMMAND:
                value = _write_response(value, action)
            elif command == "workspace-restore-case-retention-review":
                value = _review_response(value, action)
            elif command == "workspace-restore-case-retention":
                value = _retention_response(value, action)
            elif command == "workspace-restore-case-catalog":
                value = _catalog_response(value, action)
            elif command == "workspace-restore-case":
                value = _case_response(value, action)
            else:
                value = _public_status(value)
            value = public(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Restore case retention review write demonstration stopped: invalid public CLI response") from error
        if discard:
            discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        if command == COMMAND:
            statuses[label] = value
        return value

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> Any:
        with launch(command, action, arguments) as process:
            return collect(process, label, command, action, expected_error=expected_error, discard=discard)

    def prepare_args(name: str, *, plan: Path | None = None, review: Path | None = None,
                     job: Path | None = None, operation: str | None = None,
                     actor: str | None = None, verdict: str | None = None) -> list[str]:
        return ["--plan", str(plan if plan is not None else plan_directories[name]),
                "--review", str(review if review is not None else review_directories[name]),
                "--job", str(job if job is not None else job_directories[name]),
                "--operation-id", operations[name] if operation is None else operation,
                "--actor", actors[name] if actor is None else actor,
                "--verdict", verdicts[name] if verdict is None else verdict]

    def old_record_args(name: str) -> list[str]:
        return ["--plan", str(plan_directories[name]), "--review", str(review_directories[name]),
                "--operation-id", operations[name], "--actor", actors[name], "--verdict", verdicts[name]]

    def write_status(label: str, name: str, state: str, *, action: str = "status") -> dict[str, Any]:
        value = cli(label, COMMAND, action, ["--job", str(job_directories[name])])
        _require(value["state"] == state and value["completed"] is (state == "completed")
                 and value["intended_review"] == intended[name]
                 and value["result"] == (intended[name] if state == "completed" else None),
                 "a write state changed its fixed review or publication boundary")
        return value

    def historical_show(label: str, name: str) -> dict[str, Any]:
        value = cli(label, "workspace-restore-case-retention-review", "show", ["--review", str(review_directories[name])])
        _require(value == intended[name], "the compatible reader did not return the fixed intended review")
        return value

    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    observed_controls = [*plan_directories.values(), catalog_a, catalog_b, *case_directories,
                         *attempt_jobs, *old_review_directories.values()]
    original_controls = [controls(path) for path in observed_controls]

    def originals_unchanged() -> bool:
        return (all(controls(path) == before for path, before in zip(observed_controls, original_controls))
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == before
                        for index, before in old_observations.items()))

    minimum_args = prepare_args("minimum")
    cli("minimum-prepare-response-discarded", COMMAND, "prepare", minimum_args, discard=True)
    minimum_prepared = cli("minimum-prepare-exact-replay", COMMAND, "prepare", minimum_args)
    intended["minimum"] = minimum_prepared["intended_review"]
    recovered["prepare"] = discarded_addresses.pop("minimum-prepare-response-discarded") == hashlib.sha256(canonical(minimum_prepared).encode("utf-8")).hexdigest()
    prepared_controls = write_controls("minimum")
    minimum_prepared_status = write_status("minimum-prepared-status", "minimum", "prepared")
    _require(minimum_prepared_status == minimum_prepared and write_controls("minimum") == prepared_controls and recovered["prepare"],
             "prepare replay or read-only status changed its durable intended review")
    incomplete = "workspace_restore_case_retention_review_incomplete"
    private_path = "workspace_restore_case_retention_review_private_path_required"
    prepared_refusal = cli("minimum-not-yet-published-show", "workspace-restore-case-retention-review", "show",
                           ["--review", str(review_directories["minimum"])], expected_error=incomplete)
    long_prepared = cli("long-prepare", COMMAND, "prepare", prepare_args("long"))
    intended["long"] = long_prepared["intended_review"]
    long_prepared_status = write_status("long-prepared-status", "long", "prepared")
    _require(long_prepared == long_prepared_status and minimum_prepared["state"] == "prepared"
             and len({minimum_prepared["write_id"], long_prepared["write_id"]}) == 2
             and len({value["review_id"] for value in intended.values()} | {value["review_id"] for value in baseline_reviews.values()}) == 4,
             "prepare did not keep two new independent write identities")
    for name, value in intended.items():
        _require(value["plan"] == plans[name] and value["actor"] == actors[name]
                 and value["verdict"] == verdicts[name] and value["operation_id"] == operations[name],
                 "an intended review lost its exact full plan or self-declared request")
    stage("durable-intent-fixes-a-whole-review",
          "Two ordinary prepare requests fix complete advisory reviews; exact replay recovers a discarded response, status preserves controls, and the old reader refuses the unpublished target.",
          {"minimum": minimum_prepared, "minimum_status": minimum_prepared_status, "long": long_prepared,
           "long_status": long_prepared_status, "old_reader_refusal": prepared_refusal,
           "actual_discarded_prepare_response_recovered_exactly": recovered["prepare"]})

    with launch(COMMAND, "step", ["--job", str(job_directories["minimum"])]) as first:
        with launch(COMMAND, "step", ["--job", str(job_directories["minimum"])]) as second:
            pair = [collect(first, "minimum-launched-step-01", COMMAND, "step"),
                    collect(second, "minimum-launched-step-02", COMMAND, "step")]
    _require({value["state"] for value in pair} == {"ready", "linked"}
             and all(value["write_id"] == minimum_prepared["write_id"] and value["intended_review"] == intended["minimum"]
                     and value["completed"] is False and value["result"] is None for value in pair),
             "two launched callers did not observe distinct successive states for one fixed write")
    linked_refusal = cli("minimum-linked-old-reader-refusal", "workspace-restore-case-retention-review", "show",
                         ["--review", str(review_directories["minimum"])], expected_error=private_path)
    linked_controls = write_controls("minimum")
    minimum_linked = write_status("minimum-linked-status", "minimum", "linked")
    _require(minimum_linked in pair and write_controls("minimum") == linked_controls,
             "linked status changed stage/final metadata")
    minimum_completed = write_status("minimum-finalize", "minimum", "completed", action="step")
    minimum_completed_status = write_status("minimum-completed-status", "minimum", "completed")
    before_replay = write_controls("minimum")
    minimum_completed_replay = write_status("minimum-completed-step-replay", "minimum", "completed", action="step")
    minimum_show = historical_show("minimum-compatible-old-show", "minimum")
    minimum_old_replay = cli("minimum-compatible-old-record-replay", "workspace-restore-case-retention-review", "record", old_record_args("minimum"))
    _require(minimum_completed == minimum_completed_status == minimum_completed_replay
             and minimum_old_replay == minimum_show == intended["minimum"] and write_controls("minimum") == before_replay,
             "completed replay or compatible readers changed publication controls")
    concurrency = {"caller_count": 2, "launched_before_any_response_collected": True,
                   "responses": pair, "returned_states": ["ready", "linked"],
                   "distinct_successive_states_observed": True, "same_write_id": True,
                   "temporal_process_overlap_measured": False, "measured_capacity": False}
    stage("two-launched-callers-advance-one-fixed-write",
          "Two step processes start before reply collection and return unordered ready/linked states. The old reader refuses the two-link state; a third step completes publication and historical readers become compatible.",
          {"callers": concurrency, "linked_status": minimum_linked, "old_reader_refusal": linked_refusal,
           "completed": minimum_completed, "completed_status": minimum_completed_status,
           "completed_step_replay": minimum_completed_replay, "old_show": minimum_show,
           "old_record_replay": minimum_old_replay, "completed_control_metadata_unchanged_by_replays": True})

    detach_originals = [*plan_directories.values(), catalog_a, catalog_b, *case_directories, *attempt_jobs, source_bundle]
    _require(len(detach_originals) == 14, "offline continuation did not select fourteen live paths")
    detached: list[tuple[Path, Path]] = []
    try:
        for index, original in enumerate(detach_originals, 1):
            held = private / f"{PREFIX}-offline-held-{index:02d}"
            original.rename(held)
            detached.append((original, held))
        long_offline_prepared = write_status("long-offline-prepared-status", "long", "prepared")
        long_offline_prepare = cli("long-offline-prepare-replay", COMMAND, "prepare", prepare_args("long"))
        _require(long_offline_prepare == long_offline_prepared == long_prepared,
                 "prepared replay observed an unavailable live source")
        cli("long-ready-step-response-discarded", COMMAND, "step", ["--job", str(job_directories["long"])], discard=True)
        long_ready = write_status("long-ready-status-recovers-response", "long", "ready")
        recovered["step"] = discarded_addresses.pop("long-ready-step-response-discarded") == hashlib.sha256(canonical(long_ready).encode("utf-8")).hexdigest()
        _require(recovered["step"], "status did not recover the actual discarded ready response")
        long_ready_refusal = cli("long-ready-old-reader-refusal", "workspace-restore-case-retention-review", "show",
                                 ["--review", str(review_directories["long"])], expected_error=incomplete)
        long_linked = write_status("long-offline-link", "long", "linked", action="step")
        long_linked_status = write_status("long-offline-linked-status", "long", "linked")
        _require(long_linked == long_linked_status, "linked status changed the fixed long review")
        long_linked_refusal = cli("long-linked-old-reader-refusal", "workspace-restore-case-retention-review", "show",
                                  ["--review", str(review_directories["long"])], expected_error=private_path)
        long_completed = write_status("long-offline-finalize", "long", "completed", action="step")
        long_completed_status = write_status("long-offline-completed-status", "long", "completed")
        long_completed_replay = write_status("long-offline-completed-step-replay", "long", "completed", action="step")
        long_show = historical_show("long-offline-compatible-old-show", "long")
        long_old_replay = cli("long-offline-compatible-old-record-replay", "workspace-restore-case-retention-review", "record", old_record_args("long"))
        minimum_offline_show = historical_show("minimum-offline-compatible-old-show", "minimum")
        minimum_offline_status = write_status("minimum-offline-completed-status", "minimum", "completed")
        minimum_offline_prepare = cli("minimum-offline-completed-prepare-replay", COMMAND, "prepare", minimum_args)
        _require(long_completed == long_completed_status == long_completed_replay
                 and long_old_replay == long_show == intended["long"]
                 and minimum_offline_show == intended["minimum"]
                 and minimum_offline_status == minimum_offline_prepare == minimum_completed,
                 "source-free continuation changed intended reviews or completed statuses")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    _require(originals_unchanged(), "source-free continuation changed original controls")
    completed_controls = {name: write_controls(name) for name in intended}
    stage("prepared-write-completes-without-live-inputs",
          "With fourteen old live paths absent, a prepared job advances ready/linked/completed from its durable intent. Status recovers a discarded ready reply; old show/record and completed prepare replay remain source-free. Every path returns in finally.",
          {"prepared_status": long_offline_prepared, "prepare_replay": long_offline_prepare, "ready_status": long_ready,
           "linked": long_linked, "linked_status": long_linked_status, "completed": long_completed,
           "completed_status": long_completed_status, "completed_step_replay": long_completed_replay,
           "old_show": long_show, "old_record_replay": long_old_replay,
           "minimum_show": minimum_offline_show, "minimum_status": minimum_offline_status,
           "minimum_prepare_replay": minimum_offline_prepare, "old_reader_refusals": [long_ready_refusal, long_linked_refusal],
           "detached_live_path_count": len(detach_originals), "all_detached_paths_restored": True,
           "actual_discarded_step_response_recovered_exactly": recovered["step"], "old_blocked_jobs_moved": False})

    long_restored_show = historical_show("long-compatible-show-after-live-inputs-restored", "long")
    current_basis = {}
    for name in intended:
        value = cli(name + "-current-basis-after-publication", "workspace-restore-case-retention-review", "revalidate",
                    ["--review", str(review_directories[name])])
        _require(value["review"] == intended[name] and value["observed_basis_unchanged"] is True
                 and value["plan_binding"]["state"] == value["catalog_binding"]["state"] == "unchanged"
                 and all(entry["current"] == previous and entry["observations_equal"]
                         and entry["classification_equal"] and entry["observations_verifiable"]
                         for entry, previous in zip(value["entries"], plans[name]["entries"])),
                 "compatible revalidation changed history or treated publication as current authorization")
        current_basis[name] = value
    stage("compatible-review-keeps-old-assessment",
          "Completed files are ordinary review records: old show and separate revalidation retain the full original plans. Minimum keeps two unknown-identity holds and long keeps four holds; matching basis authorizes no domain action.",
          {"long_show_after_restore": long_restored_show, "current_basis": current_basis,
           "minimum_held_count": 2, "long_held_count": 4, "actor_authenticated": False,
           "archival_performed": False, "deletion_performed": False})

    invalid = "workspace_restore_case_retention_review_write_invalid_input"
    conflict = "workspace_restore_case_retention_review_write_conflict"
    invalid_job = private / (PREFIX + "-invalid")
    invalid_review = private / "restore-case-retention-review-written-invalid"
    invalid_arguments = {"job": invalid_job, "review": invalid_review}
    for label, arguments in (
        ("write-actor-too-long", prepare_args("minimum", **invalid_arguments, actor="a" * 102)),
        ("write-operation-too-long", prepare_args("minimum", **invalid_arguments, operation="o" * 102)),
        ("write-actor-malformed", prepare_args("minimum", **invalid_arguments, actor="invalid/actor")),
        ("write-job-aliases-review", prepare_args("minimum", job=invalid_job, review=invalid_job)),
        ("write-review-aliases-plan", prepare_args("minimum", job=invalid_job, review=plan_directories["minimum"])),
    ):
        cli(label, COMMAND, "prepare", arguments, expected_error=invalid)
    for label, arguments in (
        ("write-operation-conflict", prepare_args("minimum", operation="different-write-operation")),
        ("write-actor-conflict", prepare_args("minimum", actor="different-simulated-write-actor")),
        ("write-verdict-conflict", prepare_args("minimum", verdict="defer")),
        ("write-plan-conflict", prepare_args("minimum", plan=plan_directories["long"])),
        ("write-target-review-conflict", prepare_args("minimum", review=review_directories["long"])),
    ):
        cli(label, COMMAND, "prepare", arguments, expected_error=conflict)
    _require(not invalid_job.exists() and not invalid_review.exists()
             and all(write_controls(name) == completed_controls[name] for name in intended),
             "invalid or conflicting prepare changed completed controls or reserved unsupported paths")
    stage("different-requests-do-not-rebind-an-intent",
          "Five malformed inputs and five different exact requests refuse without creating unsupported targets or changing either completed job. The immutable operation, actor, verdict, plan and target remain fixed.",
          {"write_rejections": rejections[-10:], "invalid_paths_created": False,
           "completed_control_metadata_unchanged": True, "private_control_contents_read_by_demo": False})

    final_statuses = {name: write_status("final-" + name + "-write-status", name, "completed") for name in intended}
    final_old_reviews = {}
    for name, path in old_review_directories.items():
        value = cli("final-previous-review-show-" + name.replace("_", "-"), "workspace-restore-case-retention-review", "show",
                    ["--review", str(path)])
        _require(value == baseline_reviews[name], "publication changed a previous immutable review")
        final_old_reviews[name] = value
    final_plans = {}
    for name in intended:
        value = cli("final-original-plan-show-" + name, "workspace-restore-case-retention", "show",
                    ["--plan", str(plan_directories[name])])
        _require(value == plans[name], "publication changed a saved retention plan")
        final_plans[name] = value
    final_catalog_a = cli("final-original-catalog-page", "workspace-restore-case-catalog", "page", ["--catalog", str(catalog_a), "--limit", "16"])
    final_catalog_b = cli("final-expanded-catalog-page", "workspace-restore-case-catalog", "page", ["--catalog", str(catalog_b), "--limit", "16"])
    _require(final_catalog_a == baseline_pages["final-original-catalog"] and final_catalog_b == baseline_pages["final-expanded-catalog"],
             "publication changed a current catalog observation")
    final_details = []
    for index, path in enumerate(case_directories[:3], 1):
        value = cli(f"final-original-case-detail-{index:02d}", "workspace-restore-case", "show", ["--case", str(path)])
        _require(value == baseline_details[index - 1], "publication changed a current original case detail")
        final_details.append(value)
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    source_after, live_after = observe(source), observe(live)
    _require(originals_unchanged() and matches(source_before, source_after) and matches(live_before, live_after)
             and all(write_controls(name) == completed_controls[name] for name in intended)
             and len(cli_outcomes) == 58 and len(rejections) == 17
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 41
             and not discarded_addresses,
             "final controls, history or ordinary process accounting differ from the fixed scenario")
    stage("publication-preserves-prior-domain-history",
          "Both completed statuses, previous reviews, source plans, catalog pages, case details and blocked jobs remain consistent. Source/live history is unchanged, and no domain action, new fault worker or injected partial write occurs.",
          {"completed_statuses": final_statuses, "previous_reviews": final_old_reviews, "plans": final_plans,
           "catalogs": {"original": final_catalog_a, "expanded": final_catalog_b}, "original_case_details": final_details,
           "original_control_metadata_unchanged": True, "completed_write_control_metadata_unchanged": True,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "archival_performed": False, "deletion_performed": False, "new_fault_worker_count": 0})

    compatibility = {"intended_reviews": intended, "minimum_show": minimum_show, "minimum_record_replay": minimum_old_replay,
                     "long_offline_show": long_show, "long_offline_record_replay": long_old_replay,
                     "long_show_after_restore": long_restored_show, "previous_reviews": final_old_reviews,
                     "final_statuses": final_statuses, "all_original_plans_unchanged": True}
    artifacts = {
        PREFIX + "-states.json": statuses,
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {"actual_prepare_response_discarded": True, "prepare_exact_replay_equal": recovered["prepare"],
                                           "actual_ready_step_response_discarded": True, "status_recovers_ready_response_exactly": recovered["step"],
                                           "comparison_digests_exported": False},
        PREFIX + "-launched-callers.json": concurrency,
        PREFIX + "-review-compatibility.json": compatibility,
        PREFIX + "-control-observations.json": {"original_control_metadata_unchanged": True, "original_directory_times_compared": False,
                                                 "old_blocked_job_metadata_unchanged": True, "completed_write_control_metadata_unchanged": True,
                                                 "all_detached_paths_restored": True, "old_blocked_jobs_moved": False,
                                                 "private_control_contents_read_by_demo": False, "private_stage_contents_read_by_demo": False,
                                                 "new_fault_worker_count": 0, "injected_partial_write_count": 0,
                                                 "archival_performed": False, "deletion_performed": False},
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
        PREFIX + "-current-basis-observations.json": current_basis,
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "public artifact accounting differs from the fixed scenario")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Ordinary staged publication of exact advisory review records without new fault injection",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "review_write_stage_count": len(stages),
            "private_review_write_job_count": 2, "new_private_review_record_count": 2, "total_private_review_record_count": 4,
            "selected_case_count_per_review": 4, "simulated_self_declared_actor_count": 2,
            "review_write_cli_child_process_count": len(cli_outcomes), "review_write_successful_cli_count": 41,
            "review_write_refused_cli_count": len(rejections),
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "launched_step_caller_count": 2, "step_callers_launched_before_response_collection": True,
            "temporal_process_overlap_measured": False, "new_interrupt_worker_process_count": 0,
            "new_fault_worker_count": 0, "injected_partial_write_count": 0, "partial_write_recovery_exercised": False,
            "discarded_public_response_count": 2, "discarded_prepare_response_recovered_exactly": recovered["prepare"],
            "discarded_step_response_recovered_by_status_exactly": recovered["step"],
            "operator_rejection_count": len(rejections), "offline_live_path_count": len(detach_originals),
            "prepared_write_completed_without_live_inputs": True, "old_review_reader_compatible_after_completion": True,
            "old_review_reader_refused_unpublished_or_linked_states": True, "completed_replays_preserved_control_metadata": True,
            "all_detached_paths_restored": True, "original_control_metadata_unchanged": True,
            "old_blocked_job_metadata_unchanged": True, "original_blocked_jobs_still_blocked": True,
            "completed_write_control_metadata_unchanged": True, "current_basis_revalidation_count": len(current_basis),
            "minimum_held_count": 2, "long_held_count": 4,
            "original_identity_reconstructed": False, "request_equivalence_verified": False,
            "new_restore_attempt_count": 0, "archival_performed": False, "deletion_performed": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_stage_contents_read_by_demo": False,
            "private_fingerprints_exported": False, "actor_authenticated": False, "old_authority_activated": False,
            "external_fencing_established": False, "external_llm_used": False, "human_review_recorded": False,
            "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "This ordinary product workflow introduces no fault callback, injected partial write, forced interruption or new fault worker. Staging-prefix recovery is implemented separately and is not exercised here.",
            "Two ordinary step processes are launched before either reply is collected and return successive ready/linked states. Temporal overlap, lock wait duration, throughput and production capacity are not measured.",
            "Each durable intent fixes one complete self-declared advisory review. Completion preserves old review format, identity, operation, clock, actor, verdict, plan and all original reasons.",
            "Prepared, ready and linked states do not provide an old-reader result. The old reader refuses until the proven staging alias is removed and the final record has one link.",
            "Before a complete durable intent exists, bootstrap directories, empty staging and partial intent remain outside supported continuation; this scenario does not construct or repair them.",
            "After preparation, status, step and exact prepare replay use captured job controls without live plan, catalog, case, attempt or source bundle reads. Fourteen original paths return in finally; old blocked jobs never move.",
            "An actual prepare reply is discarded and recovered by exact replay. An actual ready step reply is discarded and recovered through status without repeating a state-changing step. Comparison digests remain unpublished.",
            "Source/live public Store comparison excludes only top-level governance observed_at. New write-job and review metadata are separate from domain history; no new restoration, grant, credential, archival or deletion occurs beyond baseline.",
            "Actors and verdicts do not establish authentication, independent expertise, semantic truth or authorization. Matching basis revalidation is separate from publication and authorizes no action.",
            "No power failure, storage failure, new SIGKILL schedule, arbitrary partial control repair, noncooperating file owner or full concurrency schedule is demonstrated.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Поэтапная публикация точного рассмотрения плана", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий рассмотрения и актуальности](restore-case-retention-review-report.md); его 341 публичный файл сохранён.",
        "Два новых write jobs сохраняют точные intended reviews, затем отдельными обычными step публикуют совместимые review records.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Зафиксированное намерение и текущая фаза", "",
        "Prepare фиксирует полный intended review: ID, operation, clock, самозаявленные actor/verdict и весь прежний plan. Status показывает фазу, сохраняя result=null до completed.",
        "Три обычных step проходят ready, linked и completed. Старый reader отказывает для незавершённого target и для linked с двумя hard links; после удаления доказанного staging alias он читает обычный review16.",
        "Для minimum два CLI-процесса запущены до сбора первого ответа. Получены ready и linked в неопределённом порядке, затем третий step завершил публикацию. Перекрытие времени процессов и производительность не измерялись.",
        "Для long все 14 прежних live paths временно отсутствуют. Exact prepare replay, status и три шага завершают сохранённое намерение без них. Пути возвращены в finally, старые blocked jobs не перемещались.",
        "Настоящий ответ prepare отброшен и восстановлен точным повтором. Настоящий ответ ready-step отброшен и восстановлен read-only status; повторяющий step мог бы продвинуть следующую фазу, поэтому для восстановления ответа использован status.",
        "После публикации старые show и record replay возвращают ровно intended review. Две отдельные revalidations сохраняют minimum с двумя held и long с четырьмя held; совпавшее основание не разрешает действий.", "",
        "## Публичные основания", "",
        f"- [Состояния]({PREFIX}-states.json), [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json).",
        f"- [Восстановление ответов]({PREFIX}-reply-recovery.json), [два запущенных caller]({PREFIX}-launched-callers.json), [совместимость review]({PREFIX}-review-compatibility.json).",
        f"- [Controls]({PREFIX}-control-observations.json), [текущее основание]({PREFIX}-current-basis-observations.json).",
        f"- [Источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы результата", "",
        "Шесть этапов, 58 новых обычных CLI: 41 успешный и 17 ожидаемых отказов. С повторённым основанием это 371 CLI и 14 прежних interrupt workers, всего 385 различных дочерних процессов.",
        "Сохранены два новых write jobs и два новых review records; прежние два reviews, plans, catalogs, cases, blocked jobs и история R4/36 и R5/59 сохранены.",
        "Демонстратор не читает private control bodies или staging contents. Публикуются безопасные API/Store ответы, счётчики и булевы metadata-наблюдения.",
        "Новых fault workers, SIGKILL, injected partial writes и отказов хранения нет. Восстановление partial staging и неподдержанное окно до durable intent этим обычным сценарием не воспроизводились.",
        "Самозаявленные actor/verdict и completed не удостоверяют личность, независимость рассмотрения, смысловую истинность документации либо полномочия архивации, удаления и доменных записей.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
