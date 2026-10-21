"""Demonstrate historical advisory reviews and separate current-basis checks.

The driver consumes validated public API responses and lstat metadata. It does
not inspect private control bodies or staging data. Temporary directory moves
are reversed in finally, and original blocked job directories never move.
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
from .workspace_restore_case_retention_demo import run_workspace_restore_case_retention_demo
from .workspace_restore_case_retention_review_cli import _public_response as _review_response
from .workspace_restore_cases_cli import _public_response as _case_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save two simulated self-declared reviews and observe their current basis."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 323, "the preceding scenario did not produce 323 public files")
    plans = json.loads((workspace / "restore-case-retention-plans.json").read_text())
    baseline_cases = json.loads((workspace / "restore-cases-cases.json").read_text())
    baseline_details = [value["completed_show"] for value in baseline_cases]
    baseline_pages = json.loads((workspace / "restore-case-catalog-pages.json").read_text())
    catalog_a, catalog_b = private / "restore-case-catalog-main", private / "restore-case-catalog-expanded"
    case_directories = [private / f"restore-case-{index:02d}" for index in range(1, 4)]
    case_directories.append(private / "restore-case-catalog-late")
    attempt_jobs = [private / f"case-attempt-job-{index:02d}" for index in range(1, 4)]
    source_bundle = private / "full-backup"
    source_size = (source_bundle / "state.sqlite3").lstat().st_size
    plan_directories = {
        "minimum": private / "restore-case-retention-minimum",
        "long": private / "restore-case-retention-long",
        "missing_case": private / "restore-case-retention-missing-case",
        "missing_attempt": private / "restore-case-retention-missing-attempt",
    }
    review_directories = {name: private / ("restore-case-retention-review-" + name.replace("_", "-"))
                          for name in ("minimum", "missing_attempt")}
    actors = {"minimum": "simulated-operator-a", "missing_attempt": "simulated-operator-b"}
    verdicts = {"minimum": "reviewed", "missing_attempt": "request_fresh_plan"}
    operations = {name: "restore-case-retention-review-" + name.replace("_", "-") for name in review_directories}
    markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "source_bundle", "original_manifest", "restore_manifest",
        "owner", "staging_path", "sha256", "schema_fingerprint", "request_digest", "plan_path",
        "plan_directory", "catalog_path", "case_path", "case_directory", "catalog_directory", "parent", "pins",
        "review_path", "review_directory", "plan_anchor", "plan_source",
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
        """Compare full file metadata and static directory identity after moves."""
        directory = path.lstat()
        return {"directory": (directory.st_dev, directory.st_ino, directory.st_uid, stat.S_IMODE(directory.st_mode)),
                "files": {entry.name: (details.st_dev, details.st_ino, details.st_uid, details.st_mode,
                                        details.st_nlink, details.st_size, details.st_mtime_ns, details.st_ctime_ns)
                          for entry in sorted(path.iterdir()) for details in [entry.lstat()]}}

    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    reviews: dict[str, Any] = {}
    revalidations: dict[str, Any] = {}
    discarded_addresses: dict[str, str] = {}

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"restore-case-retention-review-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> Any:
        _require(command in {"workspace-restore-case-retention-review", "workspace-restore-case-retention",
                             "workspace-restore-case-catalog", "workspace-restore-case", "workspace-restore"},
                 "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore case retention review demonstration stopped: ordinary CLI timed out") from error
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
                if command == "workspace-restore-case-retention-review":
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
                raise RuntimeError("Restore case retention review demonstration stopped: invalid public CLI response") from error
            if discard:
                discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
                return None
            return value

    def record_args(name: str, *, plan: Path | None = None, review: Path | None = None,
                    actor: str | None = None, verdict: str | None = None,
                    operation: str | None = None) -> list[str]:
        return ["--plan", str(plan if plan is not None else plan_directories[name]),
                "--review", str(review if review is not None else review_directories[name]),
                "--actor", actors[name] if actor is None else actor,
                "--verdict", verdicts[name] if verdict is None else verdict,
                "--operation-id", operations[name] if operation is None else operation]

    def show(label: str, name: str = "minimum") -> dict[str, Any]:
        value = cli(label, "workspace-restore-case-retention-review", "show", ["--review", str(review_directories[name])])
        _require(value == reviews[name], "historical show changed the saved review")
        return value

    def require_review(value: dict[str, Any], name: str) -> None:
        _require(value["plan"] == plans[name] and value["actor"] == actors[name]
                 and value["verdict"] == verdicts[name] and value["operation_id"] == operations[name]
                 and value["created_at"] >= plans[name]["created_at"],
                 "a review lost its whole historical plan or self-declared request")

    def expected_rows(kind: str | None) -> list[dict[str, Any]]:
        rows = []
        for previous in plans["minimum"]["entries"]:
            current = previous
            if kind == "all" or (kind == "case" and previous["index"] == 3):
                current = {**previous, "observation": {"state": "unavailable", "detail": None,
                           "error_code": "workspace_restore_case_retention_case_unavailable"},
                           "disposition": "held", "reasons": [*previous["reasons"], "case_unavailable"]}
            elif kind == "attempt" and previous["index"] == 3:
                detail = {**previous["observation"]["detail"], "current_attempt": {
                    "state": "unavailable", "status": None, "error_code": "workspace_restore_case_attempt_unavailable"}}
                current = {**previous, "observation": {"state": "available", "detail": detail, "error_code": None},
                           "disposition": "held", "reasons": [*previous["reasons"], "attempt_unavailable"]}
            rows.append(current)
        return rows

    def revalidate(label: str, name: str = "minimum", *, plan_state: str = "unchanged",
                   catalog_state: str = "unchanged", kind: str | None = None) -> dict[str, Any]:
        value = cli(label, "workspace-restore-case-retention-review", "revalidate",
                    ["--review", str(review_directories[name])])
        _require(value["review"] == reviews[name] and value["checked_at"] >= reviews[name]["created_at"],
                 "current observation replaced history or lost its fresh clock")
        for binding, state in (("plan", plan_state), ("catalog", catalog_state)):
            error = None if state == "unchanged" else "workspace_restore_case_retention_review_" + binding + "_" + state
            _require(value[binding + "_binding"] == {"state": state, "error_code": error},
                     "a source binding was silently treated as current")
        changed = {1, 2, 3, 4} if kind == "all" else ({3} if kind in {"case", "attempt"} or name == "missing_attempt" else set())
        for entry, current in zip(value["entries"], expected_rows(kind)):
            same = current["index"] not in changed
            _require(entry == {"current": current, "observations_equal": same,
                               "classification_equal": same, "observations_verifiable": same},
                     "a current row or its historical comparison obscured unavailable evidence")
        unchanged = plan_state == catalog_state == "unchanged" and not changed
        _require(value["observed_basis_unchanged"] is unchanged,
                 "the aggregate observation confused matching classification with verifiable basis")
        revalidations[label] = value
        return value

    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}
    observed_controls = [*case_directories, catalog_a, catalog_b, *attempt_jobs, *plan_directories.values()]
    original_controls = [controls(path) for path in observed_controls]

    def originals_unchanged() -> bool:
        return (all(controls(path) == before for path, before in zip(observed_controls, original_controls))
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == before
                        for index, before in old_observations.items()))

    minimum_args = record_args("minimum")
    cli("minimum-review-response-discarded", "workspace-restore-case-retention-review", "record", minimum_args, discard=True)
    reviews["minimum"] = cli("minimum-review-exact-replay", "workspace-restore-case-retention-review", "record", minimum_args)
    require_review(reviews["minimum"], "minimum")
    recovered = discarded_addresses.pop("minimum-review-response-discarded") == hashlib.sha256(canonical(reviews["minimum"]).encode("utf-8")).hexdigest()
    minimum_show = show("minimum-review-show")
    minimum_baseline = revalidate("minimum-baseline")
    _require(recovered, "exact replay did not recover the actual discarded response")
    stage("review-preserves-history-without-authority",
          "A simulated self-declared actor records reviewed for the whole minimum plan; exact replay recovers a discarded response, while separate revalidation observes unchanged basis.",
          {"review": reviews["minimum"], "show": minimum_show, "revalidation": minimum_baseline,
           "actual_discarded_response_recovered_exactly": recovered, "actor_authenticated": False})

    reviews["missing_attempt"] = cli("missing-attempt-review-record", "workspace-restore-case-retention-review", "record", record_args("missing_attempt"))
    require_review(reviews["missing_attempt"], "missing_attempt")
    missing_show = show("missing-attempt-review-show", "missing_attempt")
    missing_baseline = revalidate("missing-attempt-baseline", "missing_attempt")
    stage("restored-path-does-not-rewrite-earlier-basis",
          "A second simulated actor requests a fresh plan: a historically unavailable attempt is currently available and potentially a candidate, but the earlier observed basis remains unverifiable.",
          {"review": reviews["missing_attempt"], "show": missing_show, "revalidation": missing_baseline,
           "historical_candidate_indices": [1], "current_candidate_indices": [1, 3],
           "restoration_proves_historical_availability": False})

    review_controls = {name: controls(path) for name, path in review_directories.items()}
    attempt_held = private / "restore-case-retention-review-held-attempt"
    moved = False
    try:
        attempt_jobs[1].rename(attempt_held)
        moved = True
        attempt_missing = revalidate("attempt-path-unavailable", kind="attempt")
        show("historical-show-with-attempt-unavailable")
    finally:
        if moved:
            attempt_held.rename(attempt_jobs[1])
    attempt_restored = revalidate("attempt-path-restored")
    case_held = private / "restore-case-retention-review-held-case"
    moved = False
    try:
        case_directories[1].rename(case_held)
        moved = True
        case_missing = revalidate("case-path-unavailable", kind="case")
    finally:
        if moved:
            case_held.rename(case_directories[1])
    case_restored = revalidate("case-path-restored")
    _require(originals_unchanged(), "temporary member observations changed original controls")
    stage("current-member-availability-is-separate",
          "Unavailable attempt and unavailable case produce distinct current holds and false comparison booleans; every path is restored and the historical review stays fixed.",
          {"attempt_unavailable": attempt_missing, "attempt_restored": attempt_restored,
           "case_unavailable": case_missing, "case_restored": case_restored,
           "all_detached_paths_restored": True, "old_blocked_jobs_moved": False})

    catalog_held = private / "restore-case-retention-review-held-catalog"
    moved = False
    try:
        catalog_b.rename(catalog_held)
        moved = True
        catalog_missing = revalidate("catalog-path-unavailable", catalog_state="unavailable")
    finally:
        if moved:
            catalog_held.rename(catalog_b)
    catalog_restored = revalidate("catalog-path-restored")
    plan_held = private / "restore-case-retention-review-held-plan"
    moved = False
    try:
        plan_directories["minimum"].rename(plan_held)
        moved = True
        plan_missing = revalidate("plan-path-unavailable", plan_state="unavailable")
        detached_replay = cli("historical-replay-with-plan-unavailable", "workspace-restore-case-retention-review", "record", minimum_args)
        _require(detached_replay == reviews["minimum"], "historical replay depended on a currently available plan")
        show("historical-show-with-plan-unavailable")
    finally:
        if moved:
            plan_held.rename(plan_directories["minimum"])
    plan_restored = revalidate("plan-path-restored")
    original_moved, replacement_moved = False, False
    try:
        plan_directories["minimum"].rename(plan_held)
        original_moved = True
        plan_directories["long"].rename(plan_directories["minimum"])
        replacement_moved = True
        plan_changed = revalidate("plan-binding-changed", plan_state="changed")
    finally:
        try:
            if replacement_moved:
                plan_directories["minimum"].rename(plan_directories["long"])
        finally:
            if original_moved:
                plan_held.rename(plan_directories["minimum"])
    plan_binding_restored = revalidate("plan-binding-restored")
    _require(originals_unchanged(), "temporary source binding observations changed original controls")
    stage("binding-checks-survive-cached-member-observation",
          "Missing catalog, missing plan and a different valid plan directory give explicit binding failures while cached members remain independently observable; historical show/replay still return the saved review.",
          {"catalog_unavailable": catalog_missing, "catalog_restored": catalog_restored,
           "plan_unavailable": plan_missing, "plan_restored": plan_restored,
           "plan_changed": plan_changed, "plan_binding_restored": plan_binding_restored,
           "historical_replay": detached_replay, "all_detached_paths_restored": True})

    detach_originals = [*plan_directories.values(), catalog_a, catalog_b, *case_directories, *attempt_jobs, source_bundle]
    _require(len(detach_originals) == 14, "offline scenario did not select fourteen live paths")
    detached: list[tuple[Path, Path]] = []
    offline_shows: dict[str, Any] = {}
    try:
        for index, original in enumerate(detach_originals, 1):
            held = private / f"restore-case-retention-review-offline-held-{index:02d}"
            original.rename(held)
            detached.append((original, held))
        for name in reviews:
            offline_shows[name] = show("historical-offline-show-" + name.replace("_", "-"), name)
        offline_replay = cli("historical-offline-exact-review-replay", "workspace-restore-case-retention-review", "record", minimum_args)
        _require(offline_replay == reviews["minimum"], "historical review replay accessed unavailable live inputs")
        all_missing = revalidate("all-live-paths-unavailable", plan_state="unavailable", catalog_state="unavailable", kind="all")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    all_restored = revalidate("all-live-paths-restored")
    _require(originals_unchanged() and all(controls(path) == review_controls[name] for name, path in review_directories.items()),
             "offline historical reads or current observations changed saved records")
    stage("historical-review-outlives-all-live-inputs",
          "With fourteen live paths temporarily absent, both shows and exact replay remain historical; separate revalidation reports missing bindings, unavailable cases and false comparisons. Every path returns in finally.",
          {"shows": offline_shows, "minimum_replay": offline_replay, "revalidation": all_missing,
           "after_restore": all_restored, "detached_live_path_count": len(detach_originals),
           "all_detached_paths_restored": True, "old_blocked_jobs_moved": False})

    invalid_code = "workspace_restore_case_retention_review_invalid_input"
    conflict_code = "workspace_restore_case_retention_review_conflict"
    invalid_review = private / "restore-case-retention-review-invalid-request"
    for label, arguments in (
        ("actor-too-long", record_args("minimum", review=invalid_review, actor="a" * 102)),
        ("operation-too-long", record_args("minimum", review=invalid_review, operation="o" * 102)),
        ("actor-malformed", record_args("minimum", review=invalid_review, actor="invalid/actor")),
        ("review-aliases-source-plan", record_args("minimum", review=plan_directories["minimum"])),
    ):
        cli(label, "workspace-restore-case-retention-review", "record", arguments, expected_error=invalid_code)
    for label, arguments in (
        ("review-operation-conflict", record_args("minimum", operation="different-review-operation")),
        ("review-actor-conflict", record_args("minimum", actor="different-simulated-actor")),
        ("review-verdict-conflict", record_args("minimum", verdict="defer")),
        ("review-plan-conflict", record_args("minimum", plan=plan_directories["long"])),
    ):
        cli(label, "workspace-restore-case-retention-review", "record", arguments, expected_error=conflict_code)
    final_shows = {name: show("final-review-show-" + name.replace("_", "-"), name) for name in reviews}
    final_minimum = cli("final-minimum-plan-show", "workspace-restore-case-retention", "show", ["--plan", str(plan_directories["minimum"])])
    _require(final_minimum == plans["minimum"], "a review rewrote its source retention plan")
    final_catalog_a = cli("final-original-catalog-page", "workspace-restore-case-catalog", "page", ["--catalog", str(catalog_a), "--limit", "16"])
    final_catalog_b = cli("final-expanded-catalog-page", "workspace-restore-case-catalog", "page", ["--catalog", str(catalog_b), "--limit", "16"])
    _require(final_catalog_a == baseline_pages["final-original-catalog"] and final_catalog_b == baseline_pages["final-expanded-catalog"],
             "a review changed current catalog observations")
    final_details = []
    for index, path in enumerate(case_directories[:3], 1):
        detail = cli(f"final-original-case-detail-{index:02d}", "workspace-restore-case", "show", ["--case", str(path)])
        _require(detail == baseline_details[index - 1], "a review changed current original case detail")
        final_details.append(detail)
    final_revalidation = revalidate("final-minimum-basis")
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    source_after, live_after = observe(source), observe(live)
    _require(originals_unchanged() and matches(source_before, source_after) and matches(live_before, live_after)
             and all(controls(path) == review_controls[name] for name, path in review_directories.items())
             and len(cli_outcomes) == 45 and len(rejections) == 11 and len(revalidations) == 15
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 34,
             "final controls, domain observations or ordinary command accounting differ from the scenario")
    stage("reviews-do-not-authorize-domain-actions",
          "Invalid or conflicting requests refuse safely; saved reviews, source plans, catalog pages, case details, blocked jobs and source/live history remain unchanged, with no archive or deletion.",
          {"reviews": final_shows, "minimum_plan": final_minimum, "revalidation": final_revalidation,
           "catalogs": {"original": final_catalog_a, "expanded": final_catalog_b}, "original_case_details": final_details,
           "original_control_metadata_unchanged": True, "review_control_metadata_unchanged": True,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "archival_performed": False, "deletion_performed": False})

    comparisons = {label: {"checked_at": value["checked_at"], "review_id": value["review"]["review_id"],
                          "plan_binding": value["plan_binding"], "catalog_binding": value["catalog_binding"],
                          "observed_basis_unchanged": value["observed_basis_unchanged"],
                          "candidate_indices": [entry["current"]["index"] for entry in value["entries"]
                                                if entry["current"]["disposition"] == "review_candidate"],
                          "entries": [{"index": entry["current"]["index"], "observations_equal": entry["observations_equal"],
                                       "classification_equal": entry["classification_equal"],
                                       "observations_verifiable": entry["observations_verifiable"]} for entry in value["entries"]]}
                   for label, value in revalidations.items()}
    artifacts = {
        "restore-case-retention-review-records.json": reviews,
        "restore-case-retention-review-revalidations.json": revalidations,
        "restore-case-retention-review-cli-outcomes.json": cli_outcomes,
        "restore-case-retention-review-rejections.json": rejections,
        "restore-case-retention-review-reply-recovery.json": {"actual_public_response_discarded": True,
                                                              "exact_replay_equal": recovered, "comparison_digest_exported": False},
        "restore-case-retention-review-comparisons.json": comparisons,
        "restore-case-retention-review-control-observations.json": {"original_control_metadata_unchanged": True,
                                                                    "original_directory_times_compared": False,
                                                                    "old_blocked_job_metadata_unchanged": True,
                                                                    "review_control_metadata_unchanged": True,
                                                                    "all_detached_paths_restored": True,
                                                                    "old_blocked_jobs_moved": False,
                                                                    "archival_performed": False, "deletion_performed": False},
        "restore-case-retention-review-source-observations.json": {"before": source_before, "after": source_after},
        "restore-case-retention-review-live-observations.json": {"before": live_before, "after": live_after},
        "restore-case-retention-review-restored-detail-observations.json": {"before": baseline_details, "after": final_details,
                                                                           "current_after_historical_missing_attempt": missing_baseline["entries"][2]["current"]},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "public artifact accounting differs from the fixed scenario")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Immutable self-declared advisory reviews and separate current-basis revalidation",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "retention_review_stage_count": len(stages),
            "private_retention_review_count": len(reviews), "revalidation_count": len(revalidations),
            "selected_case_count_per_review": 4, "simulated_self_declared_actor_count": 2,
            "retention_review_cli_child_process_count": len(cli_outcomes),
            "retention_review_successful_cli_count": 34, "retention_review_refused_cli_count": len(rejections),
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "new_interrupt_worker_process_count": 0, "operator_rejection_count": len(rejections),
            "discarded_review_response_count": 1, "discarded_review_response_recovered_exactly": recovered,
            "minimum_current_basis_unchanged": True, "historical_missing_attempt_current_basis_unchanged": False,
            "current_candidate_implies_historical_basis_verified": False,
            "show_and_replay_with_live_paths_unavailable": True, "offline_live_path_count": len(detach_originals),
            "all_detached_paths_restored": True, "original_control_metadata_unchanged": True,
            "old_blocked_job_metadata_unchanged": True, "original_blocked_jobs_still_blocked": True,
            "plan_control_metadata_unchanged": True, "review_control_metadata_unchanged": True,
            "original_identity_reconstructed": False, "request_equivalence_verified": False,
            "new_restore_attempt_count": 0, "archival_performed": False, "deletion_performed": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_stage_contents_read_by_demo": False,
            "private_fingerprints_exported": False, "actor_authenticated": False, "old_authority_activated": False,
            "external_fencing_established": False, "external_llm_used": False,
            "human_review_recorded": False, "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "Actors and verdicts are simulated self-declarations over one whole saved plan. They establish no authentication, independent expertise, semantic truth or authorization.",
            "Show and exact record replay return an immutable historical review without observing current source paths. Revalidation is a separate dated sequential observation, not a common filesystem transaction or continuing guarantee.",
            "Revalidation independently checks source plan/catalog bindings, cached member detail, fresh age classification and three comparison booleans. Matching unavailable observations do not establish a verifiable basis.",
            "Restoring a historically unavailable attempt can yield a current candidate while the earlier observation and classification differ and its historical basis remains unverifiable.",
            "Current candidates, reviewed, defer, request_fresh_plan and observed_basis_unchanged never authorize archival, deletion, domain writes, old authority activation or external fencing.",
            "A real validated successful record response is discarded and recovered by exact replay; the ephemeral comparison digest is unpublished.",
            "Fourteen live paths are moved temporarily and restored in finally. Static directory identities and full contained file metadata are compared, excluding directory times changed by rename. Original blocked job directories never move.",
            "No new jobs, credentials, grants or domain decisions occur beyond the repeated baseline. Public Store comparison excludes only the top-level governance observed_at.",
            "No new interruption, partial review write, changed case evidence, changed catalog binding, concurrency schedule, storage failure, all fault schedules or production capacity is exercised.",
        ],
    }
    write_json("restore-case-retention-review-report.json", report)
    lines = [
        "# Историческое рассмотрение плана и отдельная проверка текущего основания", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий retention](restore-case-retention-report.md); его 323 публичных файла сохранены.",
        "Два моделируемых участника записывают самозаявленные verdict для целого исторического плана. Аутентификация и разрешение действий отсутствуют.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## История и текущее основание", "",
        "Show и точный replay возвращают сохранённое рассмотрение, включая весь прежний plan. Настоящий успешный ответ record был проверен, отброшен и восстановлен точным повтором.",
        "Revalidate отдельно получает checked_at, проверяет plan/catalog bindings, наблюдает сохранённый состав случаев и пересчитывает текущие причины по новому времени.",
        "Для minimum исходные строки и текущие observations совпадают; неизвестные исходные identity сохраняют held. Совпадение основания не разрешает действий.",
        "Для исторического missing_attempt восстановленная попытка теперь доступна, а строка становится кандидатом. Прежнее unavailable не становится проверенным задним числом: все три сравнения этой строки ложны.", "",
        "Отдельно воспроизведены отсутствие attempt, case, catalog и plan, а также подмена привязки plan другим существующим каталогом без редактирования его файлов.",
        "Когда отсутствуют только plan/catalog, сохранённый состав случаев всё ещё наблюдается; общий observed_basis_unchanged остаётся false.",
        "При отсутствии всех 14 live paths исторические show/replay доступны, а отдельный revalidate сообщает unavailable для обеих привязок и всех случаев.",
        "Все пути возвращены в finally. Старые blocked jobs не перемещались; исходные plans, reviews, catalog pages, case details и R4/R5 история сохранены.", "",
        "## Публичные основания", "",
        "- [Рассмотрения](restore-case-retention-review-records.json), [15 текущих наблюдений](restore-case-retention-review-revalidations.json), [сравнения](restore-case-retention-review-comparisons.json).",
        "- [CLI](restore-case-retention-review-cli-outcomes.json), [отказы](restore-case-retention-review-rejections.json), [восстановление ответа](restore-case-retention-review-reply-recovery.json).",
        "- [Сохранность controls](restore-case-retention-review-control-observations.json), [case details](restore-case-retention-review-restored-detail-observations.json).",
        "- [Источник](restore-case-retention-review-source-observations.json), [живая среда](restore-case-retention-review-live-observations.json), [машинный отчёт](restore-case-retention-review-report.json).", "",
        "## Границы результата", "",
        "Шесть этапов, 45 новых CLI: 34 успешных и 11 ожидаемых отказов. Всего с повторённым baseline: 313 CLI и 14 прежних interrupt workers, 327 дочерних процессов.",
        "Новых прерываний, restore attempts, credentials, domain decisions, операций архивации и удаления нет.",
        "Частные control records и staging демонстратор не разбирает. Публикуются безопасные API/Store ответы, счётчики и булевы наблюдения.",
        "Самозаявленные actor/verdict не устанавливают личность, человеческое рассмотрение, независимость участников или смысловую истинность содержания.",
        "Последовательный revalidate не является общей транзакцией и не обещает будущей неизменности. Частичные записи, новые отказы хранения, все расписания и предел нагрузки не воспроизводились.", "",
    ]
    with (workspace / "restore-case-retention-review-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
