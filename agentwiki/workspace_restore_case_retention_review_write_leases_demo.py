"""Demonstrate bounded local scheduling without advancing captured write jobs.

Ordinary CLI processes compete for explicit catalog positions. The driver only
reads validated public responses, credential markers and lstat metadata. It
never opens the private lease database or prior control bodies, injects a fault,
waits for an expiry, or treats a scheduling claim as domain authorization.
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
from .workspace_restore_case_retention_review_write_catalog_cli import _public_response as _catalog_response
from .workspace_restore_case_retention_review_write_catalog_demo import run_workspace_restore_case_retention_review_write_catalog_demo
from .workspace_restore_case_retention_review_write_cli import _public_response as _write_response
from .workspace_restore_case_retention_review_write_leases_cli import _public_response as _lease_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata


RULES_VERSION = "workspace-restore-case-retention-review-write-leases-1"
COMMAND = "workspace-restore-case-retention-review-write-leases"
PREFIX = "restore-case-retention-review-write-leases"
CATALOG_COMMAND = "workspace-restore-case-retention-review-write-catalog"
WRITE_COMMAND = "workspace-restore-case-retention-review-write"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore case retention review write leases demonstration stopped: " + message)


def run_workspace_restore_case_retention_review_write_leases_demo(
    workspace: str | Path, workers: int = 3,
) -> dict[str, Any]:
    """Coordinate ordinary local workers and preserve every captured job."""
    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_case_retention_review_write_catalog_demo(workspace, workers)
    private = workspace / "private"
    repository = Path(__file__).resolve().parents[1]
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    _require(len(baseline_files) == 377, "the preceding scenario did not produce 377 public files")
    previous_receipts = json.loads((workspace / "restore-case-retention-review-write-catalog-receipts.json").read_text())
    previous_pages = json.loads((workspace / "restore-case-retention-review-write-catalog-pages.json").read_text())
    previous_writes = json.loads((workspace / "restore-case-retention-review-write-catalog-write-observations.json").read_text())
    statuses = previous_writes["final_statuses"]
    catalog_directories = {name: private / ("restore-case-retention-review-write-catalog-" + name)
                           for name in ("initial", "expanded")}
    job_directories = {name: private / ("restore-case-retention-review-write-" + name)
                       for name in ("minimum", "long")}
    job_directories["late"] = private / "restore-case-retention-review-write-catalog-late-job"
    target_directories = {name: private / ("restore-case-retention-review-written-" + name)
                          for name in ("minimum", "long")}
    target_directories["late"] = private / "restore-case-retention-review-write-catalog-late-review"
    ledger_directory = private / PREFIX
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
        _require(command in {COMMAND, CATALOG_COMMAND, WRITE_COMMAND, "workspace-restore"},
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
            raise RuntimeError("Restore case retention review write leases demonstration stopped: ordinary CLI timed out") from error
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
                value = _lease_response(value, action)
            elif command == CATALOG_COMMAND:
                value = _catalog_response(value, action)
            elif command == WRITE_COMMAND:
                value = _write_response(value, action)
            else:
                value = _public_status(value)
            value = public(value)
        except (TypeError, ValueError) as error:
            raise RuntimeError("Restore case retention review write leases demonstration stopped: invalid public CLI response") from error
        if discard:
            discarded_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
            return None
        return value

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> Any:
        with launch(command, action, arguments) as process:
            return collect(process, label, command, action, expected_error=expected_error, discard=discard)

    ledger_directories = {"primary": ledger_directory.with_name(PREFIX + "-primary"),
                          "secondary": ledger_directory.with_name(PREFIX + "-secondary")}
    create_operations = {name: PREFIX + "-" + name for name in ledger_directories}
    ledgers: dict[str, Any] = {}
    operations: dict[str, Any] = {}
    heads: dict[str, dict[int, Any]] = {name: {} for name in ledger_directories}
    descriptors = {entry["index"]: entry["write"] for entry in previous_pages["final-expanded-page"]["entries"]}
    lease_fields = ("lease_id", "generation", "worker", "lease_seconds", "claimed_at", "expires_at", "released_at")
    invalid = "workspace_restore_case_retention_review_write_leases_invalid_input"
    conflict = "workspace_restore_case_retention_review_write_leases_conflict"
    busy = "workspace_restore_case_retention_review_write_leases_busy"

    def create_args(name: str, *, operation: str | None = None, ledger: Path | None = None,
                    catalog: Path | None = None) -> list[str]:
        return ["--ledger", str(ledger_directories[name] if ledger is None else ledger),
                "--catalog", str(catalog_directories["expanded"] if catalog is None else catalog),
                "--operation-id", create_operations[name] if operation is None else operation]

    def claim_args(name: str, index: int, worker: str, operation: str, *, seconds: int = 3600) -> list[str]:
        return ["--ledger", str(ledger_directories[name]), "--index", str(index), "--worker", worker,
                "--lease-seconds", str(seconds), "--operation-id", operation]

    def release_args(name: str, claim: dict[str, Any], operation: str, *,
                     worker: str | None = None, generation: int | None = None) -> list[str]:
        return ["--ledger", str(ledger_directories[name]), "--index", str(claim["index"]),
                "--worker", claim["worker"] if worker is None else worker,
                "--lease-id", claim["lease_id"], "--generation", str(claim["generation"] if generation is None else generation),
                "--operation-id", operation]

    def remember(label: str, name: str, value: dict[str, Any]) -> dict[str, Any]:
        _require(value["ledger_id"] == ledgers[name]["ledger_id"]
                 and value["write"] == descriptors[value["index"]]
                 and value["kind"] in {"claim", "release"}
                 and value["job_steps_enforced"] is False and value["same_ledger_only"] is True,
                 "an operation changed its ledger, descriptor or coordination scope")
        operations[label] = value
        heads[name][value["index"]] = {key: value[key] for key in lease_fields}
        return value

    def claim(label: str, name: str, index: int, worker: str, operation: str) -> dict[str, Any]:
        return remember(label, name, cli(label, COMMAND, "claim", claim_args(name, index, worker, operation)))

    def release(label: str, name: str, value: dict[str, Any], operation: str) -> dict[str, Any]:
        return remember(label, name, cli(label, COMMAND, "release", release_args(name, value, operation)))

    def page(label: str, name: str, *, limit: int = 16, after: int = 0) -> dict[str, Any]:
        before = {key: controls(path) for key, path in ledger_directories.items()}
        arguments = ["--ledger", str(ledger_directories[name]), "--limit", str(limit)]
        if after:
            arguments += ["--ledger-id", ledgers[name]["ledger_id"], "--after-index", str(after)]
        value = cli(label, COMMAND, "page", arguments)
        expected = []
        for index in range(after + 1, min(3, after + limit) + 1):
            current = heads[name].get(index)
            state = "unclaimed" if current is None else "released" if current["released_at"] is not None else "active"
            expected.append({"index": index, "write": descriptors[index], "current_lease": current, "state": state})
        end = after + len(expected)
        cursor = {"ledger_id": ledgers[name]["ledger_id"], "after_index": end} if end < 3 else None
        _require(value["ledger"] == ledgers[name] and value["after_index"] == after and value["limit"] == limit
                 and value["entries"] == expected and value["next_cursor"] == cursor
                 and value["operation_count"] == len({item["operation_id"] for item in operations.values()
                                                       if item["ledger_id"] == ledgers[name]["ledger_id"]})
                 and value["job_states_checked"] is False
                 and {key: controls(path) for key, path in ledger_directories.items()} == before,
                 "a ledger page changed metadata, membership, current heads or cursor arithmetic")
        pages[label] = value
        return value

    plan_directories = [private / ("restore-case-retention-" + name)
                        for name in ("minimum", "long", "missing-case", "missing-attempt")]
    case_directories = [private / f"restore-case-{index:02d}" for index in range(1, 4)]
    case_directories.append(private / "restore-case-catalog-late")
    original_paths = [*plan_directories, private / "restore-case-catalog-main", private / "restore-case-catalog-expanded",
                      *case_directories, *(private / f"case-attempt-job-{index:02d}" for index in range(1, 4)),
                      private / "restore-case-retention-review-minimum", private / "restore-case-retention-review-missing-attempt",
                      *job_directories.values(), *target_directories.values(), *catalog_directories.values()]
    original_controls = [controls(path) for path in original_paths]
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

    ledgers["primary"] = cli("primary-ledger-create", COMMAND, "create", create_args("primary"))
    primary_replay = cli("primary-create-exact-replay", COMMAND, "create", create_args("primary"))
    primary_show = cli("primary-ledger-show", COMMAND, "show", ["--ledger", str(ledger_directories["primary"])])
    ledgers["secondary"] = cli("secondary-ledger-create", COMMAND, "create", create_args("secondary"))
    _require(primary_replay == primary_show == ledgers["primary"]
             and all(value["catalog"] == previous_receipts["expanded"] and value["write_count"] == 3
                     and value["max_operations"] == 1024 for value in ledgers.values())
             and ledgers["primary"]["ledger_id"] != ledgers["secondary"]["ledger_id"],
             "ledger creation lost historical catalog composition or independent identity")
    initial_first = page("primary-initial-first-two", "primary", limit=2)
    initial_last = page("primary-initial-last-one", "primary", limit=2, after=2)
    initial_terminal = page("primary-initial-terminal-empty", "primary", after=3)
    secondary_initial = page("secondary-initial-page", "secondary")
    stage("explicit-ledgers-retain-historical-membership",
          "Two independent private ledgers capture the same explicit three-position catalog. Exact create replay and show preserve each identity; bounded pages start unclaimed and retain cursor arithmetic.",
          {"ledgers": ledgers, "primary_replay": primary_replay, "primary_show": primary_show,
           "first_two": initial_first, "last_one": initial_last, "terminal_empty": initial_terminal,
           "secondary_initial": secondary_initial, "pending_work_established": False})

    contender_specs = [
        ("contender-a", 1, "simulated-lease-worker-a", PREFIX + "-contender-a"),
        ("contender-b", 1, "simulated-lease-worker-b", PREFIX + "-contender-b"),
        ("separate-position", 2, "simulated-lease-worker-c", PREFIX + "-separate-position"),
    ]
    processes: list[tuple[tuple[str, int, str, str], subprocess.Popen[str]]] = []
    competition: dict[str, Any] = {}
    try:
        for specification in contender_specs:
            label, index, worker, operation = specification
            processes.append((specification, launch(COMMAND, "claim", claim_args("primary", index, worker, operation))))
        for specification, process in processes:
            label, index, _, _ = specification
            value = collect(process, label, COMMAND, "claim", allowed_error=busy)
            competition[label] = value
            if not value.get("rejected", False):
                remember(label, "primary", value)
    finally:
        for _, process in processes:
            if process.poll() is None:
                process.kill()
                process.communicate(timeout=15)
    successful_contenders = [(specification, competition[specification[0]]) for specification in contender_specs[:2]
                              if not competition[specification[0]].get("rejected", False)]
    _require(len(successful_contenders) == 1
             and sum(value.get("rejected", False) for value in competition.values()) == 1
             and not competition["separate-position"].get("rejected", False),
             "same-ledger same-position competition did not yield exactly one current claimant")
    winner_specification, winner = successful_contenders[0]
    winner_label, _, winner_worker, winner_operation = winner_specification
    independent = competition["separate-position"]
    competition_page = page("primary-after-competition", "primary")
    winner_replay = cli("winner-claim-exact-replay", COMMAND, "claim", claim_args("primary", 1, winner_worker, winner_operation))
    _require(winner_replay == winner and winner["generation"] == independent["generation"] == 1,
             "claim replay changed the first generation or its original expiry")
    stage("ordinary-workers-compete-within-one-ledger",
          "Three ordinary CLI processes are launched before responses are collected. Two compete for position 1: exactly one succeeds and one receives busy. Position 2 succeeds independently; process overlap, throughput and maximum capacity are not measured.",
          {"results": competition, "winning_contender": winner_label, "page": competition_page,
           "winner_exact_replay": winner_replay, "launched_before_collection_count": 3,
           "same_position_successful_claim_count": 1, "same_position_busy_count": 1,
           "different_position_successful_claim_count": 1, "process_overlap_measured": False})

    first_release = release("winner-first-release", "primary", winner, PREFIX + "-winner-release")
    second_generation = claim("position-one-generation-two", "primary", 1, "simulated-lease-worker-d", PREFIX + "-generation-two")
    stale_release = cli("stale-generation-release", COMMAND, "release",
                        release_args("primary", winner, PREFIX + "-stale-release"), expected_error=conflict)
    historical_claim = cli("old-claim-replay-after-reclaim", COMMAND, "claim", claim_args("primary", 1, winner_worker, winner_operation))
    historical_release = cli("old-release-replay-after-reclaim", COMMAND, "release", release_args("primary", winner, PREFIX + "-winner-release"))
    reclaimed_page = page("primary-after-reclaim", "primary")
    lost_arguments = claim_args("primary", 3, "simulated-lease-worker-e", PREFIX + "-lost-claim")
    cli("position-three-claim-response-discarded", COMMAND, "claim", lost_arguments, discard=True)
    lost_claim = remember("position-three-claim-recovered", "primary",
                          cli("position-three-claim-recovered", COMMAND, "claim", lost_arguments))
    recovered = discarded_addresses.pop("position-three-claim-response-discarded") == hashlib.sha256(canonical(lost_claim).encode("utf-8")).hexdigest()
    lost_release = release("position-three-release", "primary", lost_claim, PREFIX + "-lost-claim-release")
    lost_historical = cli("position-three-claim-replay-after-release", COMMAND, "claim", lost_arguments)
    lost_release_replay = cli("position-three-release-replay", COMMAND, "release",
                              release_args("primary", lost_claim, PREFIX + "-lost-claim-release"))
    released_page = page("primary-after-position-three-release", "primary")
    _require(second_generation["generation"] == 2 and second_generation["lease_id"] != winner["lease_id"]
             and historical_claim == winner and historical_release == first_release
             and lost_historical == lost_claim and lost_release_replay == lost_release and recovered,
             "generation CAS or historical operation replay changed a later head or original receipt")
    stage("generation-cas-and-historical-replies",
          "Releasing position 1 permits a fresh lease ID and generation 2. A stale generation cannot release it; exact old claim/release replies remain historical. A genuinely discarded position-3 claim reply is recovered without extending its expiry, then released.",
          {"first_claim": winner, "first_release": first_release, "second_generation": second_generation,
           "stale_release": stale_release, "historical_claim": historical_claim,
           "historical_release": historical_release, "after_reclaim": reclaimed_page,
           "recovered_claim": lost_claim, "release": lost_release, "historical_recovered_claim": lost_historical,
           "release_replay": lost_release_replay, "after_position_three_release": released_page,
           "actual_discarded_claim_response_recovered_exactly": recovered,
           "comparison_digest_exported": False})

    other_ledger_claim = claim("secondary-position-one-claim", "secondary", 1, "simulated-lease-worker-f", PREFIX + "-secondary-one")
    other_ledger_page = page("secondary-same-position-own-claim", "secondary")
    offline_paths = [*catalog_directories.values(), *job_directories.values(), *target_directories.values()]
    _require(len(offline_paths) == 8 and other_ledger_claim["generation"] == 1
             and heads["primary"][1]["released_at"] is None,
             "separate ledgers did not retain independent claims for the same catalog position")
    detached: list[tuple[Path, Path]] = []
    try:
        for index, original in enumerate(offline_paths, 1):
            held = private / f"{PREFIX}-offline-{index:02d}"
            original.rename(held)
            detached.append((original, held))
        offline_shows = {name: cli("offline-" + name + "-show", COMMAND, "show", ["--ledger", str(path)])
                         for name, path in ledger_directories.items()}
        offline_create = cli("offline-primary-create-replay", COMMAND, "create", create_args("primary"))
        offline_claim = claim("offline-secondary-position-two", "secondary", 2, "simulated-lease-worker-g", PREFIX + "-offline-two")
        offline_secondary_page = page("secondary-offline-after-new-claim", "secondary")
        offline_release = release("offline-secondary-position-two-release", "secondary", offline_claim, PREFIX + "-offline-two-release")
        offline_claim_replay = cli("offline-primary-position-two-replay", COMMAND, "claim",
                                   claim_args("primary", 2, independent["worker"], independent["operation_id"]))
        offline_primary_page = page("primary-offline-page", "primary")
        _require(offline_shows == ledgers and offline_create == ledgers["primary"] and offline_claim_replay == independent,
                 "ledger-only historical reads depended on unavailable captured paths")
    finally:
        for original, held in reversed(detached):
            held.rename(original)
    restored_catalog = cli("restored-expanded-catalog-show", CATALOG_COMMAND, "show", ["--catalog", str(catalog_directories["expanded"])])
    restored_catalog_page = cli("restored-expanded-catalog-page", CATALOG_COMMAND, "page", ["--catalog", str(catalog_directories["expanded"]), "--limit", "16"])
    secondary_release = release("secondary-position-one-release", "secondary", other_ledger_claim, PREFIX + "-secondary-one-release")
    secondary_final_page = page("secondary-final-released-page", "secondary")
    _require(restored_catalog == previous_receipts["expanded"]
             and [{key: value for key, value in entry.items()} for entry in restored_catalog_page["entries"]]
             == previous_pages["final-expanded-page"]["entries"] and originals_unchanged(),
             "offline local coordination changed prior controls or captured job phases")
    stage("independent-ledgers-and-source-free-coordination",
          "A second ledger can claim the same catalog position while the first ledger's generation 2 remains active: exclusivity is scoped to one ledger. With both catalogs and six job/target directories absent, ledger show/page/replay and a new claim/release still work. Every path returns in finally.",
          {"secondary_claim": other_ledger_claim, "secondary_page": other_ledger_page,
           "offline_shows": offline_shows, "offline_create_replay": offline_create,
           "offline_claim": offline_claim, "offline_page": offline_secondary_page,
           "offline_release": offline_release, "offline_primary_claim_replay": offline_claim_replay,
           "offline_primary_page": offline_primary_page, "restored_catalog": restored_catalog,
           "restored_catalog_page": restored_catalog_page, "secondary_release": secondary_release,
           "secondary_final_page": secondary_final_page, "maximum_detached_path_count": 8,
           "all_detached_paths_restored": True, "old_blocked_jobs_moved": False,
           "cross_ledger_exclusivity_established": False})

    for label, arguments, error in (
        ("create-operation-conflict", create_args("primary", operation=PREFIX + "-different-create"), conflict),
        ("create-catalog-conflict", create_args("primary", catalog=catalog_directories["initial"]), conflict),
        ("ledger-aliases-catalog", create_args("primary", ledger=catalog_directories["expanded"]), invalid),
    ):
        cli(label, COMMAND, "create", arguments, expected_error=error)
    cli("creation-operation-reused-for-claim", COMMAND, "claim",
        claim_args("primary", 3, "simulated-lease-worker-a", create_operations["primary"]), expected_error=conflict)
    for label, arguments, error in (
        ("claim-index-zero", claim_args("primary", 0, "simulated-lease-worker-a", PREFIX + "-invalid-index-zero"), invalid),
        ("claim-index-outside-membership", claim_args("primary", 4, "simulated-lease-worker-a", PREFIX + "-invalid-index-four"), invalid),
        ("claim-duration-zero", claim_args("primary", 3, "simulated-lease-worker-a", PREFIX + "-invalid-duration-zero", seconds=0), invalid),
        ("claim-duration-too-large", claim_args("primary", 3, "simulated-lease-worker-a", PREFIX + "-invalid-duration-high", seconds=3601), invalid),
        ("claim-worker-malformed", claim_args("primary", 3, "invalid/worker", PREFIX + "-invalid-worker"), invalid),
        ("claim-operation-malformed", claim_args("primary", 3, "simulated-lease-worker-a", "invalid/operation"), invalid),
        ("claim-replay-other-index", claim_args("primary", 2, winner_worker, winner_operation), conflict),
        ("claim-replay-other-worker", claim_args("primary", 1, "simulated-lease-worker-other", winner_operation), conflict),
        ("claim-replay-other-duration", claim_args("primary", 1, winner_worker, winner_operation, seconds=3599), conflict),
    ):
        cli(label, COMMAND, "claim", arguments, expected_error=error)
    for label, arguments in (
        ("release-wrong-worker", release_args("primary", second_generation, PREFIX + "-wrong-worker", worker=winner_worker)),
        ("release-wrong-generation", release_args("primary", second_generation, PREFIX + "-wrong-generation", generation=3)),
        ("release-already-released-new-operation", release_args("primary", lost_claim, PREFIX + "-released-again")),
    ):
        cli(label, COMMAND, "release", arguments, expected_error=conflict)
    cli("active-generation-remains-busy", COMMAND, "claim",
        claim_args("primary", 1, "simulated-lease-worker-denied", PREFIX + "-active-busy"), expected_error=busy)
    for label, arguments in (
        ("page-limit-zero", ["--limit", "0"]),
        ("page-limit-too-large", ["--limit", "17"]),
        ("page-foreign-ledger-cursor", ["--ledger-id", ledgers["secondary"]["ledger_id"], "--after-index", "1"]),
        ("page-cursor-id-without-index", ["--ledger-id", ledgers["primary"]["ledger_id"]]),
        ("page-cursor-index-without-id", ["--after-index", "1"]),
        ("page-after-outside-membership", ["--ledger-id", ledgers["primary"]["ledger_id"], "--after-index", "4"]),
        ("page-negative-after", ["--ledger-id", ledgers["primary"]["ledger_id"], "--after-index", "-1"]),
    ):
        cli(label, COMMAND, "page", ["--ledger", str(ledger_directories["primary"]), *arguments], expected_error=invalid)
    _require(originals_unchanged(), "refused operations changed an original control")
    stage("invalid-requests-and-stale-cas-refuse",
          "Malformed bounds, identities and cursors refuse. Reusing operation IDs with changed parameters, stale release identities, already released new operations and an active claim refuse without advancing captured jobs or changing accepted history.",
          {"rejections_so_far": list(rejections), "original_control_metadata_unchanged": True,
           "job_steps_enforced": False, "worker_authenticated": False,
           "archival_performed": False, "deletion_performed": False})

    final_generation_release = release("primary-generation-two-final-release", "primary", second_generation, PREFIX + "-generation-two-release")
    independent_release = release("primary-position-two-final-release", "primary", independent, PREFIX + "-separate-position-release")
    primary_final_page = page("primary-final-released-page", "primary")
    final_shows = {name: cli("final-" + name + "-show", COMMAND, "show", ["--ledger", str(path)])
                   for name, path in ledger_directories.items()}
    final_statuses = {name: cli("final-" + name + "-write-status", WRITE_COMMAND, "status", ["--job", str(path)])
                      for name, path in job_directories.items()}
    for index, error in old_definitions:
        cli("original-job-still-blocked-" + index, "workspace-restore", "status",
            ["--job", str(private / ("prepare-job-" + index))], expected_error=error)
    final_catalog_pages = {name: cli("final-" + name + "-catalog-page", CATALOG_COMMAND, "page",
                                      ["--catalog", str(path), "--limit", "16"])
                           for name, path in catalog_directories.items()}
    source_after, live_after = observe(source), observe(live)
    unique_operations = {value["operation_id"]: value for value in operations.values()}
    lease_outcomes = [value for value in cli_outcomes if value["command"] == COMMAND]
    _require(final_shows == ledgers and final_statuses == statuses and originals_unchanged()
             and all(final_catalog_pages[name]["entries"] == previous_pages["final-" + name + "-page"]["entries"]
                     for name in catalog_directories)
             and matches(source_before, source_after) and matches(live_before, live_after)
             and all(value["released_at"] is not None for values in heads.values() for value in values.values())
             and len(unique_operations) == 12 and sum(value["kind"] == "claim" for value in unique_operations.values()) == 6
             and len(cli_outcomes) == 76 and len(rejections) == 29
             and sum(value["returncode"] == 0 for value in cli_outcomes) == 47
             and len(lease_outcomes) == 66 and sum(value["returncode"] == 0 for value in lease_outcomes) == 40
             and len(pages) == 12 and not discarded_addresses,
             "final coordination accounting, generations, unchanged history or controls differ from the fixed scenario")
    stage("released-heads-preserve-jobs-and-authority-boundaries",
          "All five previously claimed ledger positions end released; the secondary ledger's third position remains unclaimed. Twelve unique immutable operations, completed write jobs, historical catalogs, blocked restore jobs and source/live Store observations remain distinct. No lease API advances a job or grants domain authority.",
          {"ledgers": final_shows, "primary_final_page": primary_final_page,
           "secondary_final_page": secondary_final_page, "generation_two_release": final_generation_release,
           "independent_release": independent_release, "write_statuses": final_statuses,
           "catalog_pages": final_catalog_pages, "unique_operation_count": len(unique_operations),
           "original_control_metadata_unchanged": True, "old_blocked_job_metadata_unchanged": True,
           "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
           "jobs_advanced": False, "pending_work_established": False, "domain_writes_allowed": False,
           "archival_performed": False, "deletion_performed": False})

    artifacts = {
        PREFIX + "-ledgers.json": {"primary": ledgers["primary"], "secondary": ledgers["secondary"],
                                   "final": final_shows, "offline_primary_replay": offline_create},
        PREFIX + "-operations.json": operations,
        PREFIX + "-pages.json": pages,
        PREFIX + "-competition.json": {"results": competition, "winning_contender": winner_label,
                                       "launched_before_collection_count": 3,
                                       "same_position_successful_claim_count": 1, "same_position_busy_count": 1,
                                       "different_position_successful_claim_count": 1,
                                       "process_overlap_measured": False, "measured_capacity": False},
        PREFIX + "-cli-outcomes.json": cli_outcomes,
        PREFIX + "-rejections.json": rejections,
        PREFIX + "-reply-recovery.json": {"actual_claim_response_discarded": True, "exact_replay_equal": recovered,
                                          "historical_replay_after_release_equal": lost_historical == lost_claim,
                                          "comparison_digest_exported": False},
        PREFIX + "-control-observations.json": {"original_control_metadata_unchanged": True,
                                                "original_directory_times_compared": False,
                                                "old_blocked_job_metadata_unchanged": True,
                                                "ledger_page_control_metadata_unchanged": True,
                                                "all_detached_paths_restored": True, "old_blocked_jobs_moved": False,
                                                "maximum_detached_path_count": 8,
                                                "private_control_contents_read_by_demo": False,
                                                "private_lease_database_read_by_demo": False,
                                                "private_stage_contents_read_by_demo": False,
                                                "jobs_advanced": False, "pending_work_established": False,
                                                "cross_ledger_exclusivity_established": False,
                                                "expiry_path_demonstrated": False,
                                                "archival_performed": False, "deletion_performed": False},
        PREFIX + "-source-observations.json": {"before": source_before, "after": source_after},
        PREFIX + "-live-observations.json": {"before": live_before, "after": live_after},
    }
    _require(len(stages) == 6 and len(artifacts) == 10, "public artifact accounting differs from the fixed scenario")
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Bounded local coordination leases over explicit historical write-job membership",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "lease_stage_count": len(stages),
            "private_lease_ledger_count": 2, "captured_member_occurrence_count": 6,
            "distinct_selected_write_count": 3, "new_write_job_count": 0, "total_write_job_count": 3,
            "new_review_record_count": 0, "total_review_record_count": 5,
            "unique_operation_count": 12, "unique_claim_count": 6, "unique_release_count": 6,
            "primary_operation_count": 8, "secondary_operation_count": 4,
            "primary_position_one_final_generation": 2, "final_released_position_count": 5,
            "final_unclaimed_position_count": 1, "lease_page_response_count": len(pages),
            "new_cli_child_process_count": len(cli_outcomes), "new_successful_cli_count": 47,
            "new_refused_cli_count": len(rejections), "lease_command_count": len(lease_outcomes),
            "lease_successful_command_count": 40, "lease_refused_command_count": 26,
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "concurrent_batch_launched_child_count": 3, "same_position_successful_claim_count": 1,
            "same_position_busy_count": 1, "different_position_successful_claim_count": 1,
            "discarded_claim_response_count": 1, "discarded_claim_response_recovered_exactly": recovered,
            "new_interrupt_worker_process_count": 0, "new_fault_worker_count": 0, "injected_partial_write_count": 0,
            "maximum_offline_path_count": 8, "all_detached_paths_restored": True,
            "original_control_metadata_unchanged": True, "old_blocked_job_metadata_unchanged": True,
            "original_blocked_jobs_still_blocked": True, "lease_page_control_metadata_unchanged": True,
            "source_public_observations_unchanged": True, "live_public_observations_unchanged": True,
            "historical_replay_extends_expiry": False, "stale_release_rejected": True,
            "historical_operations_preserved_after_reclaim": True, "coordination_without_captured_paths": True,
            "same_ledger_only": True, "cross_ledger_exclusivity_established": False,
            "job_steps_enforced": False, "jobs_advanced": False, "job_states_checked_by_leases": False,
            "pending_work_established": False, "expiry_path_demonstrated": False,
            "clock_regression_path_demonstrated": False, "operation_cap_path_demonstrated": False,
            "new_restore_attempt_count": 0, "archival_performed": False, "deletion_performed": False,
            "captured_revision": source_before["state"]["revision"], "captured_event_count": source_before["status"]["events"],
            "live_revision": live_after["state"]["revision"], "live_event_count": live_after["status"]["events"],
            "private_control_contents_read_by_demo": False, "private_lease_database_read_by_demo": False,
            "private_stage_contents_read_by_demo": False, "private_fingerprints_exported": False,
            "actor_authenticated": False, "worker_authenticated": False, "old_authority_activated": False,
            "external_fencing_established": False, "external_llm_used": False, "human_review_recorded": False,
            "proposed_code_executed": False, "process_overlap_measured": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "A claim coordinates one explicit catalog position within one ledger. A second ledger can simultaneously claim that same position; no cross-ledger or external fencing exists.",
            "The captured write jobs are already completed. Leases do not inspect job phases, establish pending work, execute steps, authenticate workers or grant domain write authority.",
            "Three ordinary CLI children are launched before responses are collected. The result demonstrates one accepted current claimant per position in this ledger; overlap, throughput and maximum capacity are not measured.",
            "Release compares the latest lease ID, generation and self-declared worker. A new claim after release increments generation and creates a new lease ID; stale release cannot replace it.",
            "Exact operation replay returns an immutable historical receipt, including its original expiry, even after release or reclaim. It does not establish current ownership or extend time.",
            "One actual validated claim reply is discarded and recovered by exact replay. Its ephemeral comparison digest is not exported.",
            "Show, page, exact create replay, new claim and release use only the private ledger after capture. Eight captured catalog/job/target paths are temporarily unavailable and every path returns in finally; blocked restore jobs never move.",
            "Pages observe one ledger transaction and do not read job states. Metadata comparisons exclude directory times changed by rename; public Store comparisons exclude only top-level governance observed_at.",
            "Duration, expiry, clock-regression, resource limits and SQLite durability depend on the specified local protocol. This ordinary scenario does not wait for expiry, regress clocks, exhaust the operation cap, inject storage errors or prove power-loss behavior.",
            "No new fault worker, forced interruption, partial control write, arbitrary code execution or external LLM is introduced. Self-declared agreement and safe scheduling do not establish semantic truth or independent expertise.",
        ],
    }
    write_json(PREFIX + "-report.json", report)
    lines = [
        "# Ограниченная локальная аренда позиций каталога заданий", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий write-job catalog](restore-case-retention-review-write-catalog-report.md); его 377 публичных файлов сохранены.",
        "Два отдельных ledger закрепляют один явный каталог из трёх уже завершённых write jobs. Назначение позиции не устанавливает наличие невыполненной работы.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Конкуренция, поколения и исторические ответы", "",
        "Три обычных CLI запущены до сбора ответов. Два претендента на позицию 1 дали один claim и один busy; позиция 2 назначена отдельно. Перекрытие выполнения процессов и пропускная способность не измерялись.",
        "Release первой позиции позволил claim поколения 2 с новым lease ID. Устаревший release отвергнут. Точные повторы прежних claim/release вернули исторические квитанции без смены текущей головы и продления срока.",
        "Настоящий ответ claim позиции 3 отброшен, затем восстановлен точным повтором. Сравнительный digest остался только в памяти демонстратора.",
        "Во втором ledger та же позиция 1 назначается независимо от действующего claim первого ledger. Единственность ограничена одним ledger; внешнего fencing нет.",
        "При отсутствии восьми catalog/job/target каталогов show/page/replay и новый claim/release продолжили работу по собственным controls. Все пути возвращены в finally; прежние blocked restore jobs никогда не перемещались.", "",
        "## Публичные основания", "",
        f"- [Ledgers]({PREFIX}-ledgers.json), [12 операций]({PREFIX}-operations.json), [12 страниц]({PREFIX}-pages.json).",
        f"- [Конкуренция]({PREFIX}-competition.json), [CLI]({PREFIX}-cli-outcomes.json), [отказы]({PREFIX}-rejections.json).",
        f"- [Восстановление ответа]({PREFIX}-reply-recovery.json), [controls]({PREFIX}-control-observations.json).",
        f"- [Источник]({PREFIX}-source-observations.json), [живая среда]({PREFIX}-live-observations.json), [машинный отчёт]({PREFIX}-report.json).", "",
        "## Границы результата", "",
        "Шесть этапов, 76 новых CLI: 47 успешных и 29 ожидаемых отказов. Из них 66 lease-команд: 40 успешных и 26 отказов. С прежним основанием это 513 обычных CLI и 14 прежних interrupt workers, всего 527 различных дочерних процессов.",
        "Два ledger сохраняют 12 различных операций: шесть claim и шесть release. Пять когда-либо назначенных позиций завершили сценарий released; одна позиция второго ledger осталась unclaimed. Все три write jobs и пять review records сохранены.",
        "Source/живая история сохранили R4/36 событий и R5/59. Управляющие записи прежних plans, cases, catalogs, jobs и reviews не изменились.",
        "Демонстратор не читает private control bodies, содержимое ledger SQLite либо staging. Публикуются безопасные API/Store ответы, счётчики и булевы metadata-наблюдения.",
        "Нет новых fault workers, SIGKILL, injected partial writes либо ожидания expiry. Истечение срока, регресс часов, исчерпание лимита операций и отказ хранения этим запуском не воспроизводились.",
        "Lease API не проверяет фазы jobs и не вызывает step. Прежний прямой step не требует этого lease. Worker самозаявлен; назначение не удостоверяет личность, независимость экспертизы или истинность текста, не разрешает доменные изменения, архивирование, удаление либо активацию прежних полномочий.", "",
    ]
    with (workspace / (PREFIX + "-report.md")).open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
