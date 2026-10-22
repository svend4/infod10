"""Demonstrate explicit write-job membership and separate bounded observations.

The driver reads validated public responses and lstat metadata. One ordinary
write job is added, completed through its public steps, and observed by a new
catalog. Temporary moves are reversed in finally; blocked restore jobs never
move. No new fault worker, injected partial write or private control read occurs.
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
from .workspace_restore_case_catalog_cli import _public_response as _case_catalog_response
from .workspace_restore_case_retention_review_cli import _public_response as _review_response
from .workspace_restore_case_retention_review_write_cli import _public_response as _write_response
from .workspace_restore_case_retention_review_write_demo import run_workspace_restore_case_retention_review_write_demo
from .workspace_restore_case_retention_review_write_catalog_cli import _public_response as _catalog_response
from .workspace_restore_cases_cli import _public_response as _case_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-catalog-1"
COMMAND = "workspace-restore-case-retention-review-write-catalog"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"
PREFIX = "restore-case-retention-review-write-catalog"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write catalog demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_catalog_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Freeze two explicit catalogs and observe one ordinary job's later phases."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 359, "the preceding scenario did not produce 359 public files")
    previous_compatibility = json.loads((workspace / "restore-case-retention-review-write-review-compatibility.json").read_text())
    previous_reviews = json.loads((workspace / "restore-case-retention-review-records.json").read_text())
    previous_cases = json.loads((workspace / "restore-cases-cases.json").read_text())
    previous_case_pages = json.loads((workspace / "restore-case-catalog-pages.json").read_text())
    previous_details = [value["completed_show"] for value in previous_cases]
    statuses = dict(previous_compatibility["final_statuses"])
    job_directories = {name: private / ("restore-case-retention-review-write-" + name) for name in ("minimum", "long")}
    job_directories["late"] = private / (PREFIX + "-late-job")
    review_directories = {name: private / ("restore-case-retention-review-written-" + name) for name in ("minimum", "long")}
    review_directories["late"] = private / (PREFIX + "-late-review")
    catalog_directories = {name: private / (PREFIX + "-" + name) for name in ("initial", "expanded")}
    orders = {"initial": ["long", "minimum"], "expanded": ["long", "late", "minimum"]}
    operations = {name: PREFIX + "-" + name for name in catalog_directories}
    late_operation = PREFIX + "-late-write"
    late_actor = "simulated-catalog-operator-c"
    plan_directories = {name: private / ("restore-case-retention-" + name.replace("_", "-"))
                        for name in ("minimum", "long", "missing_case", "missing_attempt")}
    case_catalog_a, case_catalog_b = private / "restore-case-catalog-main", private / "restore-case-catalog-expanded"
    case_directories = [private / f"restore-case-{index:02d}" for index in range(1, 4)]
    case_directories.append(private / "restore-case-catalog-late")
    attempt_jobs = [private / f"case-attempt-job-{index:02d}" for index in range(1, 4)]
    prior_review_directories = {"minimum": private / "restore-case-retention-review-minimum",
                                "missing_attempt": private / "restore-case-retention-review-missing-attempt"}
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
        "request_digest", "plan_path", "plan_directory", "catalog_path", "case_path", "case_directory",
        "catalog_directory", "parent", "pins", "review_path", "review_directory", "plan_anchor", "plan_source",
        "review_record", "review_lock", "stage_pin", "case_record", "case_lock",
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

    def descriptor(value: dict[str, Any]) -> dict[str, Any]:
        intended = value["intended_review"]
        return {"rules_version": value["rules_version"], "write_id": value["write_id"],
                "operation_id": value["operation_id"], "created_at": value["created_at"],
                "review_id": intended["review_id"], "actor": intended["actor"], "verdict": intended["verdict"]}

    descriptors = {name: descriptor(value) for name, value in statuses.items()}
    receipts: dict[str, Any] = {}
    pages: dict[str, Any] = {}
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
        _require(command in {COMMAND, WRITE_COMMAND, "workspace-restore-case-retention-review",
                             "workspace-restore-case-catalog", "workspace-restore-case", "workspace-restore"},
                 "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore case retention review write catalog demonstration stopped: ordinary CLI timed out") from error
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
            try:
                value = json.loads(stdout)
                if command == COMMAND:
                    value = _catalog_response(value, action)
                elif command == WRITE_COMMAND:
                    value = _write_response(value, action)
                elif command == "workspace-restore-case-retention-review":
                    value = _review_response(value, action)
                elif command == "workspace-restore-case-catalog":
                    value = _case_catalog_response(value, action)
                elif command == "workspace-restore-case":
                    value = _case_response(value, action)
                else:
                    value = _public_status(value)
                value = public(value)
            except (TypeError, ValueError) as error:
                raise RuntimeError("Restore case retention review write catalog demonstration stopped: invalid public CLI response") from error
            if discard:
                discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
                return None
            return value

    def create_args(name: str, *, jobs: list[Path] | None = None, catalog: Path | None = None,
                    operation: str | None = None) -> list[str]:
        selected = [job_directories[key] for key in orders[name]] if jobs is None else jobs
        arguments = ["--catalog", str(catalog_directories[name] if catalog is None else catalog),
                     "--operation-id", operations[name] if operation is None else operation]
        for path in selected:
            arguments += ["--job", str(path)]
        return arguments

    original_paths = [*plan_directories.values(), case_catalog_a, case_catalog_b, *case_directories,
                      *attempt_jobs, *prior_review_directories.values(),
                      job_directories["minimum"], job_directories["long"],
                      review_directories["minimum"], review_directories["long"]]
    original_controls = [controls(path) for path in original_paths]
    page_guard_paths = [*original_paths, job_directories["late"], review_directories["late"], *catalog_directories.values()]

    def page_controls() -> dict[str, Any]:
        return {str(path): controls(path) if path.exists() else None for path in page_guard_paths}

    def page(label: str, name: str, *, limit: int = 16, after: int = 0,
             observations: dict[str, str] | None = None) -> dict[str, Any]:
        before = page_controls()
        arguments = ["--catalog", str(catalog_directories[name]), "--limit", str(limit)]
        if after:
            arguments += ["--catalog-id", receipts[name]["catalog_id"], "--after-index", str(after)]
        value = cli(label, COMMAND, "page", arguments)
        selection = orders[name][after:after + limit]
        expected = []
        for index, key in enumerate(selection, after + 1):
            state = "available" if observations is None else observations.get(key, "available")
            current = {"state": state, "status": statuses[key] if state == "available" else None,
                       "error_code": None if state == "available" else "workspace_restore_case_retention_review_write_catalog_entry_" + state}
            expected.append({"index": index, "write": descriptors[key], "current_write": current})
        end = after + len(selection)
        cursor = {"catalog_id": receipts[name]["catalog_id"], "after_index": end} if end < len(orders[name]) else None
        _require(value["catalog"] == receipts[name] and value["after_index"] == after and value["limit"] == limit
                 and value["entries"] == expected and value["next_cursor"] == cursor
                 and value["source_inputs_checked"] is False and value["jobs_advanced"] is False
                 and page_controls() == before,
                 "a page changed controls, membership, current status or cursor arithmetic")
        pages[label] = value
        return value

    source, live = Store(source_bundle / "state.sqlite3"), Store(private / "next-workspace" / "state.sqlite3")
    source_before, live_before = observe(source), observe(live)
    old_definitions = (("01", "workspace_restore_job_manifest_invalid"),
                       ("03", "workspace_restore_job_conflict"), ("04", "workspace_restore_job_manifest_invalid"))
    old_observations = {index: _metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1]
                        for index, _ in old_definitions}

    def originals_unchanged() -> bool:
        return (all(controls(path) == before for path, before in zip(original_paths, original_controls))
                and all(_metadata(private / ("prepare-job-" + index), private / ("prepare-restore-" + index), source_size)[1] == before
                        for index, before in old_observations.items()))

    initial_args = create_args("initial")
    cli("initial-create-response-discarded", COMMAND, "create", initial_args, discard=True)
    receipts["initial"] = cli("initial-create-exact-replay", COMMAND, "create", initial_args)
    recovered = discarded_addresses.pop("initial-create-response-discarded") == hashlib.sha256(canonical(receipts["initial"]).encode("utf-8")).hexdigest()
    initial_show = cli("initial-catalog-show", COMMAND, "show", ["--catalog", str(catalog_directories["initial"])])
    initial_first = page("initial-first-page", "initial", limit=1)
    initial_last = page("initial-last-page", "initial", limit=1, after=1)
    initial_terminal = page("initial-terminal-empty", "initial", limit=1, after=2)
    _require(recovered and initial_show == receipts["initial"] and receipts["initial"]["write_count"] == 2,
             "historical catalog replay lost its exact discarded receipt")
    stage("explicit-membership-freezes-order",
          "A catalog explicitly pins long then minimum; its actual discarded create response is recovered by exact replay, and bounded pages preserve order and terminal emptiness.",
          {"catalog": receipts["initial"], "show": initial_show, "first_page": initial_first,
           "last_page": initial_last, "terminal_page": initial_terminal,
           "actual_discarded_create_response_recovered_exactly": recovered})

    late_prepare_args = ["--plan", str(plan_directories["minimum"]), "--review", str(review_directories["late"]),
                         "--job", str(job_directories["late"]), "--operation-id", late_operation,
                         "--actor", late_actor, "--verdict", "request_fresh_plan"]
    statuses["late"] = cli("late-write-prepare", WRITE_COMMAND, "prepare", late_prepare_args)
    descriptors["late"] = descriptor(statuses["late"])
    late_states = {"prepared": statuses["late"]}
    _require(statuses["late"]["state"] == "prepared"
             and len({value["write_id"] for value in descriptors.values()}) == 3,
             "the later job did not have a distinct prepared identity")
    initial_after_late = page("initial-membership-after-late-job", "initial")
    held_target = private / (PREFIX + "-held-target")
    moved = False
    try:
        review_directories["late"].rename(held_target)
        moved = True
        receipts["expanded"] = cli("expanded-create-with-target-unavailable", COMMAND, "create", create_args("expanded"))
        expanded_show = cli("expanded-show-with-target-unavailable", COMMAND, "show", ["--catalog", str(catalog_directories["expanded"])])
        target_unavailable = page("expanded-target-unavailable", "expanded", observations={"late": "unavailable"})
    finally:
        if moved:
            held_target.rename(review_directories["late"])
    prepared_restored = page("expanded-prepared-target-restored", "expanded")
    expanded_first = page("expanded-first-two-prepared", "expanded", limit=2)
    expanded_last = page("expanded-last-one-prepared", "expanded", limit=2, after=2)
    _require(receipts["initial"]["write_count"] == 2 and receipts["expanded"]["write_count"] == 3
             and expanded_show == receipts["expanded"] and initial_after_late["entries"][0]["write"] == descriptors["long"],
             "new membership or absent target silently changed the original catalog")
    catalog_controls = {name: controls(path) for name, path in catalog_directories.items()}
    stage("later-job-needs-an-explicit-new-catalog",
          "A later prepared job is not automatically added to the first catalog. The second pins long, late and minimum from intent metadata even while the late target is unavailable; pages retain that row and recover after the target returns.",
          {"late_prepared": late_states["prepared"], "initial_after_late_job": initial_after_late,
           "expanded_catalog": receipts["expanded"], "expanded_show": expanded_show,
           "target_unavailable": target_unavailable, "target_restored": prepared_restored,
           "first_two": expanded_first, "last_one": expanded_last, "later_job_automatically_added": False})

    phase_pages = {}
    for phase in ("ready", "linked", "completed"):
        statuses["late"] = cli("late-write-" + phase, WRITE_COMMAND, "step", ["--job", str(job_directories["late"])])
        _require(statuses["late"]["state"] == phase and descriptor(statuses["late"]) == descriptors["late"],
                 "ordinary step changed the pinned later job identity")
        late_states[phase] = statuses["late"]
        phase_pages[phase] = page("expanded-observes-" + phase, "expanded")
    late_show = cli("late-compatible-review-show", "workspace-restore-case-retention-review", "show", ["--review", str(review_directories["late"])])
    late_old_replay = cli("late-compatible-record-replay", "workspace-restore-case-retention-review", "record",
                          ["--plan", str(plan_directories["minimum"]), "--review", str(review_directories["late"]),
                           "--operation-id", late_operation, "--actor", late_actor, "--verdict", "request_fresh_plan"])
    late_basis = cli("late-current-basis", "workspace-restore-case-retention-review", "revalidate", ["--review", str(review_directories["late"])])
    _require(late_show == late_old_replay == statuses["late"]["result"]
             and late_basis["review"] == late_show and late_basis["observed_basis_unchanged"] is True,
             "catalog observation changed compatible review or its separate basis")
    completed_late_controls = (controls(job_directories["late"]), controls(review_directories["late"]))
    stage("pages-observe-phases-without-advancing",
          "Only ordinary write steps advance the later job through ready, linked and completed. Catalog pages retain the same descriptor and show each current status without changing controls; compatible review and separate revalidation remain advisory.",
          {"states": late_states, "pages": phase_pages, "compatible_show": late_show,
           "compatible_record_replay": late_old_replay, "current_basis": late_basis,
           "page_advancement_performed": False, "actor_authenticated": False})

    held_job = private / (PREFIX + "-held-job")
    moved = False
    try:
        job_directories["long"].rename(held_job)
        moved = True
        missing_job = page("expanded-job-unavailable", "expanded", observations={"long": "unavailable"})
    finally:
        if moved:
            held_job.rename(job_directories["long"])
    missing_restored = page("expanded-job-restored", "expanded")
    original_moved, replacement_moved, swap_completed = False, False, False
    try:
        job_directories["long"].rename(held_job)
        original_moved = True
        job_directories["minimum"].rename(job_directories["long"])
        replacement_moved = True
        held_job.rename(job_directories["minimum"])
        original_moved = False
        swap_completed = True
        changed_jobs = page("expanded-jobs-changed", "expanded", observations={"long": "changed", "minimum": "changed"})
    finally:
        if swap_completed:
            job_directories["minimum"].rename(held_job)
            original_moved = True
        if replacement_moved:
            job_directories["long"].rename(job_directories["minimum"])
        if original_moved:
            held_job.rename(job_directories["long"])
    changed_restored = page("expanded-jobs-restored", "expanded")
    _require(originals_unchanged(), "temporary unavailable or different jobs changed original controls")
    stage("unavailable-and-changed-members-stay-visible",
          "An absent job retains one unavailable row. Swapping two existing completed job directories retains two changed rows before intent parsing; restoring paths restores ordinary observations and every historical descriptor remains fixed.",
          {"unavailable": missing_job, "unavailable_restored": missing_restored,
           "changed": changed_jobs, "changed_restored": changed_restored,
           "all_detached_paths_restored": True, "old_blocked_jobs_moved": False})

    source_inputs = [*plan_directories.values(), case_catalog_a, case_catalog_b, *case_directories, *attempt_jobs, source_bundle]
    member_inputs = [*job_directories.values(), *review_directories.values()]
    _require(len(source_inputs) == 14 and len(member_inputs) == 6, "offline observation did not select twenty explicit paths")
    source_detached: list[tuple[Path, Path]] = []
    member_detached: list[tuple[Path, Path]] = []
    try:
        for index, original in enumerate(source_inputs, 1):
            held = private / f"{PREFIX}-offline-source-{index:02d}"
            original.rename(held)
            source_detached.append((original, held))
        try:
            for index, original in enumerate(member_inputs, 1):
                held = private / f"{PREFIX}-offline-member-{index:02d}"
                original.rename(held)
                member_detached.append((original, held))
            offline_shows = {name: cli("offline-" + name + "-show", COMMAND, "show", ["--catalog", str(path)])
                             for name, path in catalog_directories.items()}
            offline_replay = cli("offline-initial-create-replay", COMMAND, "create", initial_args)
            all_unavailable = page("offline-members-unavailable", "expanded", observations={key: "unavailable" for key in statuses})
            _require(offline_shows == receipts and offline_replay == receipts["initial"],
                     "historical catalog show or exact replay read unavailable inputs")
        finally:
            for original, held in reversed(member_detached):
                held.rename(original)
        source_free = page("source-inputs-offline-members-available", "expanded")
    finally:
        for original, held in reversed(source_detached):
            held.rename(original)
    all_restored = page("all-inputs-restored", "expanded")
    _require(originals_unchanged(), "offline historical reads or source-free pages changed original controls")
    stage("historical-catalog-survives-input-loss",
          "With twenty explicit paths absent, both shows and exact create replay remain historical and pages retain unavailable member rows. After the six job/target paths return, pages observe all jobs while fourteen original source paths are still absent; every path returns in finally.",
          {"shows": offline_shows, "initial_replay": offline_replay, "members_unavailable": all_unavailable,
           "members_available_without_sources": source_free, "after_restore": all_restored,
           "maximum_detached_path_count": 20, "source_input_path_count": 14,
           "all_detached_paths_restored": True, "old_blocked_jobs_moved": False})

    invalid = "workspace_restore_case_retention_review_write_catalog_invalid_input"
    conflict = "workspace_restore_case_retention_review_write_catalog_conflict"
    invalid_catalog = private / (PREFIX + "-invalid")
    for label, arguments in (
        ("duplicate-job-input", create_args("initial", catalog=invalid_catalog, jobs=[job_directories["long"]] * 2)),
        ("operation-too-long", create_args("initial", catalog=invalid_catalog, operation="o" * 102)),
        ("operation-malformed", create_args("initial", catalog=invalid_catalog, operation="invalid/operation")),
        ("catalog-aliases-member-job", create_args("initial", catalog=job_directories["long"])),
        ("job-not-private-sibling", create_args("initial", catalog=invalid_catalog, jobs=[private / "nested" / "job"])),
        ("too-many-explicit-jobs", create_args("initial", catalog=invalid_catalog, jobs=[job_directories["long"]] * 257)),
    ):
        cli(label, COMMAND, "create", arguments, expected_error=invalid)
    for label, arguments in (
        ("page-limit-zero", ["--limit", "0"]),
        ("page-limit-too-large", ["--limit", "17"]),
        ("page-negative-after", ["--catalog-id", receipts["expanded"]["catalog_id"], "--after-index", "-1"]),
        ("page-after-outside-membership", ["--catalog-id", receipts["expanded"]["catalog_id"], "--after-index", "4"]),
        ("page-cursor-from-another-catalog", ["--catalog-id", receipts["initial"]["catalog_id"], "--after-index", "1"]),
        ("page-cursor-id-without-index", ["--catalog-id", receipts["expanded"]["catalog_id"]]),
        ("page-cursor-index-without-id", ["--after-index", "1"]),
    ):
        cli(label, COMMAND, "page", ["--catalog", str(catalog_directories["expanded"]), *arguments], expected_error=invalid)
    for label, arguments in (
        ("catalog-operation-conflict", create_args("initial", operation="different-catalog-operation")),
        ("catalog-order-conflict", create_args("initial", jobs=[job_directories["minimum"], job_directories["long"]])),
        ("catalog-membership-conflict", create_args("initial", jobs=[job_directories["long"]])),
    ):
        cli(label, COMMAND, "create", arguments, expected_error=conflict)
    _require(not invalid_catalog.exists(), "invalid requests created an unsupported catalog path")
    final_shows = {name: cli("final-" + name + "-show", COMMAND, "show", ["--catalog", str(path)])
                   for name, path in catalog_directories.items()}
    final_pages = {name: page("final-" + name + "-page", name) for name in catalog_directories}
    final_statuses = {name: cli("final-" + name + "-write-status", WRITE_COMMAND, "status", ["--job", str(path)])
                      for name, path in job_directories.items()}
    _require(final_shows == receipts and final_statuses == statuses, "final reads changed catalog or write identity and phase")
    final_previous_reviews = {name: cli("final-previous-" + name.replace("_", "-") + "-review-show",
                                        "workspace-restore-case-retention-review", "show", ["--review", str(path)])
                              for name, path in prior_review_directories.items()}
    _require(final_previous_reviews == previous_reviews, "catalogs changed a previous immutable review")
    final_case_catalogs = {
        "original": cli("final-original-case-catalog-page", "workspace-restore-case-catalog", "page", ["--catalog", str(case_catalog_a), "--limit", "16"]),
        "expanded": cli("final-expanded-case-catalog-page", "workspace-restore-case-catalog", "page", ["--catalog", str(case_catalog_b), "--limit", "16"]),
    }
    _require(final_case_catalogs["original"] == previous_case_pages["final-original-catalog"]
             and final_case_catalogs["expanded"] == previous_case_pages["final-expanded-catalog"],
             "catalogs changed previous case membership or observations")
    final_details = [cli(f"final-original-case-detail-{index:02d}", "workspace-restore-case", "show", ["--case", str(path)])
                     for index, path in enumerate(case_directories[:3], 1)]
    _require(final_details == previous_details, "catalogs changed original case detail")
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    source_after, live_after = observe(source), observe(live)
    _require(originals_unchanged() and matches(source_before, source_after) and matches(live_before, live_after)
             and all(controls(path) == catalog_controls[name] for name, path in catalog_directories.items())
             and (controls(job_directories["late"]), controls(review_directories["late"])) == completed_late_controls
             and len(cli_outcomes) == 66 and len(rejections) == 19 and len(pages) == 20
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 47 and not discarded_addresses,
             "final controls, history, pages or ordinary command accounting differ from the fixed scenario")
    stage("catalogs-do-not-authorize-domain-actions",
          "Invalid cursors and different ordered requests refuse safely. Final catalogs, completed writes, previous reviews, case catalogs, case details and blocked jobs preserve their histories; no catalog page advances a job or grants authority.",
          {"catalogs": final_shows, "pages": final_pages, "write_statuses": final_statuses,
           "previous_reviews": final_previous_reviews, "case_catalogs": final_case_catalogs,
           "case_details": final_details, "original_control_metadata_unchanged": True,
           "catalog_control_metadata_unchanged": True, "late_completed_control_metadata_unchanged": True,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "jobs_advanced_by_catalog": False, "archival_performed": False, "deletion_performed": False})

    artifacts = {
        PREFIX + "-receipts.json": {"initial": receipts["initial"], "expanded": receipts["expanded"], "final": final_shows,
                                    "offline_initial_replay": offline_replay},
        PREFIX + "-membership.json": {"explicit_initial_order": [descriptors[key] for key in orders["initial"]],
                                      "explicit_expanded_order": [descriptors[key] for key in orders["expanded"]],
                                      "later_job": descriptors["late"], "later_job_automatically_added": False,
                                      "initial_write_count": 2, "expanded_write_count": 3},
        PREFIX + "-pages.json": pages,
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {"actual_create_response_discarded": True, "exact_replay_equal": recovered,
                                          "comparison_digest_exported": False},
        PREFIX + "-control-observations.json": {"original_control_metadata_unchanged": True,
                                                "original_directory_times_compared": False,
                                                "old_blocked_job_metadata_unchanged": True,
                                                "catalog_control_metadata_unchanged": True,
                                                "late_completed_control_metadata_unchanged": True,
                                                "page_control_metadata_unchanged": True,
                                                "all_detached_paths_restored": True, "old_blocked_jobs_moved": False,
                                                "maximum_detached_path_count": 20,
                                                "private_control_contents_read_by_demo": False,
                                                "private_stage_contents_read_by_demo": False,
                                                "jobs_advanced_by_catalog": False,
                                                "archival_performed": False, "deletion_performed": False},
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
        PREFIX + "-write-observations.json": {"baseline_statuses": previous_compatibility["final_statuses"],
                                               "later_states": late_states, "final_statuses": final_statuses,
                                               "compatible_show": late_show, "compatible_record_replay": late_old_replay,
                                               "current_basis": late_basis},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "public artifact accounting differs from the fixed scenario")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Explicit immutable write-job catalogs and bounded read-only current observations",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "write_catalog_stage_count": len(stages),
            "private_write_catalog_count": 2, "captured_catalog_member_count": 5,
            "initial_write_count": 2, "expanded_write_count": 3, "distinct_selected_write_count": 3,
            "new_write_job_count": 1, "total_write_job_count": 3,
            "new_review_record_count": 1, "total_review_record_count": 5,
            "write_catalog_cli_child_process_count": len(cli_outcomes),
            "write_catalog_successful_cli_count": 47, "write_catalog_refused_cli_count": len(rejections),
            "catalog_command_count": 46, "catalog_successful_command_count": 30, "catalog_refused_command_count": 16,
            "page_response_count": len(pages), "discarded_catalog_response_count": 1,
            "discarded_catalog_response_recovered_exactly": recovered,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0, "injected_partial_write_count": 0,
            "operator_rejection_count": len(rejections), "maximum_offline_path_count": 20,
            "source_input_path_count": 14, "member_job_and_target_path_count": 6,
            "later_job_automatically_added": False, "catalog_capture_with_target_unavailable": True,
            "pages_observed_ready_linked_completed": True, "pages_advanced_jobs": False,
            "unavailable_member_retained": True, "changed_members_retained": True,
            "historical_show_and_replay_without_inputs": True, "pages_available_without_source_inputs": True,
            "all_detached_paths_restored": True, "original_control_metadata_unchanged": True,
            "old_blocked_job_metadata_unchanged": True, "original_blocked_jobs_still_blocked": True,
            "catalog_control_metadata_unchanged": True, "late_completed_control_metadata_unchanged": True,
            "current_basis_revalidation_count": 1, "later_review_held_count": 2,
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
            "Membership is an explicit ordered selection; later jobs are not discovered or added automatically. Two catalogs preserve different compositions and identities.",
            "Catalog capture reads validated immutable write intents without target status. A target may be unavailable during capture; a later page retains its descriptor beside an unavailable current observation.",
            "Pages retain compact seven-field historical descriptors beside validated full current status or a fixed changed/unavailable observation. Changed pins are detected before parsing the replacement intent.",
            "Only ordinary write steps advance the later job. Catalog show, replay and pages do not acquire work leases, advance jobs, revalidate original source inputs or grant domain authorization.",
            "The cursor fixes catalog identity and position, not a common state snapshot. checked_at dates one sequential page pass; current job phases may differ on later pages.",
            "An actual validated create reply is discarded and recovered by exact replay. Its ephemeral comparison digest is not exported.",
            "Twenty paths are temporarily absent; job and target paths are then restored while fourteen original source paths remain unavailable. Every path returns in finally, and blocked restore jobs never move.",
            "Static directory identities and full file metadata are compared; directory times changed by rename are excluded. Public Store comparison excludes only top-level governance observed_at.",
            "Actors remain self-declared. Available/current/completed and a matching separate basis do not establish authentication, independent expertise, semantic truth, archival, deletion or activation of old authority.",
            "No new fault worker, forced interruption, partial control write, storage failure, workload capacity or process-overlap measurement is introduced.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Явный каталог заданий записи и отдельные текущие наблюдения", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий поэтапной записи](restore-case-retention-review-write-report.md); его 359 публичных файлов сохранены.",
        "Первый каталог закрепляет long и minimum. Позднее подготовленное задание включено только во второй явно созданный каталог.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Исторический состав и текущая фаза", "",
        "Каталоги сохраняют порядок и компактные descriptor7. Страницы возвращают их рядом с текущими status17 либо явными changed/unavailable; строки не исчезают из-за ошибки участника.",
        "Второй каталог создан при временно отсутствующем review target позднего job. Сохранение membership читает intent metadata, а подробный текущий status наблюдается отдельно.",
        "Обычные step позднего задания дали ready, linked и completed; страницы только наблюдали эти фазы, не меняя управляющие файлы.",
        "Проверены страницы с cursor, терминальная пустая страница, недоступный job и два других job directories на закреплённых путях. Возврат путей восстановил наблюдения без изменения membership.",
        "При отсутствии 20 путей show и exact create replay сохранились, а страницы явно вернули unavailable. После возврата шести job/target paths все задания снова доступны, хотя 14 прежних source inputs ещё отсутствуют.",
        "Все пути возвращены в finally. Исходные blocked jobs никогда не перемещались. Отброшенный настоящий create reply восстановлен точным повтором; сравнительный digest не опубликован.", "",
        "## Публичные основания", "",
        f"- [Каталоги]({PREFIX}-receipts.json), [состав]({PREFIX}-membership.json), [20 страниц]({PREFIX}-pages.json).",
        f"- [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json), [восстановление ответа]({PREFIX}-reply-recovery.json).",
        f"- [Controls]({PREFIX}-control-observations.json), [задания и review]({PREFIX}-write-observations.json).",
        f"- [Источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы результата", "",
        "Шесть этапов, 66 новых обычных CLI: 47 успешных и 19 ожидаемых отказов. С прежним основанием это 437 CLI и 14 унаследованных interrupt workers, всего 451 различных дочерних процессов.",
        "Созданы два каталога с пятью выбранными members и одно позднее задание записи с одним новым review. Всего сохранены три write jobs и пять review records.",
        "Source/живая история сохранили R4/36 событий и R5/59. Прежние каталоги, reviews, case details, данные и blocked jobs сохранены.",
        "Демонстратор не разбирает private control bodies или staging contents. Публикуются безопасные API/Store ответы, счётчики и булевы metadata-наблюдения.",
        "Новых fault workers, SIGKILL, injected partial writes и отказов хранения нет. Предельный параллелизм, throughput и смысловое качество документации не измерялись.",
        "Исторический состав, available status и completed не удостоверяют участника и не разрешают доменных записей, архивирования, удаления либо активации прежних полномочий.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
