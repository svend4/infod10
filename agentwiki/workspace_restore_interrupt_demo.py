"""Kill trusted restore workers at five fixed filesystem boundaries.

The driver observes only public summaries, public Store records and narrow
filesystem topology. Private plans, ownership records, hashes and origin rows
are never parsed. No proposed Python or arbitrary callback is executed.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import sys
import time
from typing import Any
from zoneinfo import ZoneInfo

from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .store import Store, WorkbenchError, canonical
from .workspace_backup_cli import _public_summary
from .workspace_restore_interrupt_worker import BOUNDARIES, RULES_VERSION
from .workspace_restore_job_cli import _public_status
from .workspace_restore_job_demo import run_workspace_restore_job_demo


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore interruption demonstration stopped: " + message)


def _kill_at_boundary(repository: Path, job: Path, boundary: str) -> dict[str, Any]:
    """Require a fixed ACK and SIGKILL reap; bound cleanup attempts on failure."""
    _require(boundary in BOUNDARIES, "unsupported trusted boundary")
    expected = {"rules_version": RULES_VERSION, "boundary": boundary, "checkpoint_reached": True}
    expected_line = (json.dumps(expected, sort_keys=True, separators=(",", ":")) + "\n").encode("ascii")
    process = subprocess.Popen(
        [sys.executable, "-m", "agentwiki.workspace_restore_interrupt_worker",
         "--job", str(job), "--boundary", boundary],
        cwd=repository, shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        _require(process.stdout is not None and process.stderr is not None, "worker pipes are unavailable")
        received = bytearray()
        deadline = time.monotonic() + 60
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while b"\n" not in received:
                remaining = deadline - time.monotonic()
                _require(remaining > 0, "worker boundary acknowledgement timed out")
                if not selector.select(min(remaining, 1)):
                    continue
                block = os.read(process.stdout.fileno(), 4096)
                _require(bool(block), "worker exited before its boundary acknowledgement")
                received.extend(block)
                _require(len(received) <= 4096, "worker acknowledgement exceeded its fixed bound")
        _require(bytes(received) == expected_line, "worker acknowledgement has an unsupported schema or body")
        _require(process.poll() is None, "acknowledged worker is no longer alive")
        process.kill()
        try:
            stdout, stderr = process.communicate(timeout=15)
        except subprocess.TimeoutExpired as error:
            raise RuntimeError("Restore interruption demonstration stopped: killed worker was not reaped in time") from error
        _require(process.returncode == -signal.SIGKILL, "worker was not terminated by the driver's SIGKILL")
        _require(stdout == b"" and stderr == b"", "worker emitted data beyond its safe acknowledgement")
        return {
            "boundary": boundary, "acknowledgement": expected,
            "child_process_id": process.pid, "child_alive_after_acknowledgement": True,
            "signal_name": "SIGKILL", "signal_number": int(signal.SIGKILL),
            "returncode": process.returncode, "completed_child_process": True,
            "business_response_received": False, "additional_stdout_empty": True,
            "stderr_empty": True, "shell": False,
            "worker_alarm_seconds": 90, "acknowledgement_deadline_seconds": 60,
        }
    finally:
        if process.poll() is None:
            process.kill()
        try:
            # Also wait for a child that exited before an invalid ACK. An OS
            # that cannot reap within this bound causes the demonstration to fail.
            process.communicate(timeout=15)
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()


def _topology(job: Path, target: Path, job_id: str, source_size: int) -> tuple[dict[str, bool], tuple[Any, ...]]:
    """Inspect only fixed staging index 3 and DB/outer-manifest metadata."""
    stage = job / f".restore-{job_id}-03.part"
    database = target / "state.sqlite3"
    outer = target / ".agentwiki-workspace-manifest.json"

    def details(path: Path) -> os.stat_result | None:
        try:
            return path.lstat()
        except FileNotFoundError:
            return None

    staged, installed, manifest = details(stage), details(database), details(outer)

    def private_regular(value: os.stat_result | None) -> bool:
        return (value is not None and stat.S_ISREG(value.st_mode)
                and stat.S_IMODE(value.st_mode) == 0o600 and value.st_uid == os.getuid())

    safe = {
        "stage_present": staged is not None,
        "stage_private_regular_file": private_regular(staged),
        "stage_nonempty_proper_prefix": staged is not None and 0 < staged.st_size < source_size,
        "stage_complete_size": staged is not None and staged.st_size == source_size,
        "stage_single_link": staged is not None and staged.st_nlink == 1,
        "stage_two_links": staged is not None and staged.st_nlink == 2,
        "target_database_present": installed is not None,
        "target_private_regular_file": private_regular(installed),
        "target_complete_size": installed is not None and installed.st_size == source_size,
        "target_single_link": installed is not None and installed.st_nlink == 1,
        "target_two_links": installed is not None and installed.st_nlink == 2,
        "stage_target_same_inode": (staged is not None and installed is not None
                                    and (staged.st_dev, staged.st_ino) == (installed.st_dev, installed.st_ino)),
        "outer_manifest_present": manifest is not None,
    }
    # Identity, timestamps and byte counts stay ephemeral. They strengthen the
    # unchanged-status observation without publishing any private fingerprint.
    identity = tuple(None if value is None else (
        value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
        value.st_size, value.st_mtime_ns, value.st_ctime_ns,
    ) for value in (staged, installed, manifest))
    return safe, identity


def run_workspace_restore_interrupt_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Recover five independent three-data-file operations after real SIGKILL."""
    from . import workspace_backup

    workspace = Path(workspace).expanduser().resolve()
    baseline_report = run_workspace_restore_job_demo(workspace, workers)
    private = workspace / "private"
    source_bundle = private / "full-backup"
    repository = Path(__file__).resolve().parents[1]
    source = Store(source_bundle / "state.sqlite3")
    live = Store(private / "next-workspace" / "state.sqlite3")
    previous = Store(private / "job-restore" / "state.sqlite3")
    source_size = (source_bundle / "state.sqlite3").stat().st_size
    baseline_public_files = sorted(path.name for path in workspace.iterdir()
                                   if path.is_file() and path.suffix in {".json", ".md"})
    private_markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        private_markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "source_bundle", "original_manifest", "restore_manifest",
        "plan", "owner", "staging_path", "sha256", "schema_fingerprint", "request_digest",
    }

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not forbidden.intersection(value), "private metadata reached public output")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers), "a credential or its hash reached public output")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(),
                "access": store.snapshot_access()}

    def observations_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {key: child for key, child in value["governance"].items()
                                             if key != "observed_at"}}
        return canonical(comparable(before)) == canonical(comparable(after))

    stages: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    worker_outcomes: list[dict[str, Any]] = []
    cases: list[dict[str, Any]] = []
    target_observations: dict[str, Any] = {}
    archive_observations: dict[str, Any] = {}
    rejections: list[dict[str, Any]] = []

    def stage(label: str, explanation: str, observations: Any) -> None:
        name = f"restore-interrupt-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(name, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": name})

    def cli(label: str, action: str, job: Path, target: Path, operation: str) -> dict[str, Any]:
        arguments = ["--job", str(job)]
        if action == "prepare":
            arguments += ["--bundle", str(source_bundle), "--destination", str(target), "--operation-id", operation]
        elif action == "step":
            arguments += ["--limit", "16"]
        else:
            _require(action == "status", "unsupported ordinary CLI action")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", "workspace-restore", action, *arguments],
                              cwd=repository, shell=False, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore interruption demonstration stopped: ordinary CLI child timed out") from error
            cli_outcomes.append({"label": label, "command": "workspace-restore", "action": action,
                                 "child_process_id": process.pid, "returncode": process.returncode,
                                 "stderr_empty": not stderr, "completed_child_process": True, "shell": False})
            _require(process.returncode == 0 and not stderr, "ordinary CLI continuation failed")
            try:
                return public(_public_status(json.loads(stdout)))
            except (TypeError, ValueError) as error:
                raise RuntimeError("Restore interruption demonstration stopped: invalid ordinary CLI response") from error

    captured, live_before, previous_before = observe(source), observe(live), observe(previous)
    source_summary = public(_public_summary(workspace_backup.inspect_bundle(source_bundle), [], "inspect"))
    source_archives = {"oldest": observe(Store(source_bundle / "archives" / "history-0001.sqlite3")),
                       "nearest": observe(Store(source_bundle / "archives" / "history.sqlite3"))}
    _require(len(baseline_public_files) == 227 and source_summary["archive_count"] == 2
             and captured["state"]["revision"] == 4 and captured["status"]["events"] == 36
             and live_before["state"]["revision"] == 5 and live_before["status"]["events"] == 59,
             "baseline is not the expected historical R4 and live R5 workspace")
    stage("historical-source-before-interruption",
          "The unchanged file-sized restore scenario supplies a quarantined R4 snapshot and two archives; the live workspace remains R5.",
          {"source_summary": source_summary, "captured_status": captured["status"], "live_status": live_before["status"]})

    expected_progress = {"staging_partial": ("prepared", 0), "staging_synced": ("prepared", 0),
                         "target_linked": ("copying", 1), "data_ready": ("ready", 3), "completed": ("completed", 3)}
    for index, boundary in enumerate(BOUNDARIES, 1):
        job = private / f"interrupt-job-{index:02d}"
        target = private / f"interrupt-restore-{index:02d}"
        operation = "restore-interrupt-" + boundary.replace("_", "-")
        prepared = cli(boundary + "-prepare", "prepare", job, target, operation)
        _require(prepared["state"] == "prepared" and prepared["copied_file_count"] == 0
                 and prepared["total_file_count"] == 3, "fresh interruption job is not empty with three data files")
        outcome = public(_kill_at_boundary(repository, job, boundary))
        worker_outcomes.append(outcome)
        topology_before, identity_before = _topology(job, target, prepared["job_id"], source_size)
        stage_present = boundary in BOUNDARIES[:3]
        target_present = boundary in BOUNDARIES[2:]
        _require(topology_before["stage_present"] is stage_present
                 and topology_before["target_database_present"] is target_present
                 and topology_before["outer_manifest_present"] is (boundary == "completed"),
                 "killed worker left another boundary topology")
        if stage_present:
            _require(topology_before["stage_private_regular_file"], "staging permissions or ownership differ")
            if boundary == "staging_partial":
                _require(topology_before["stage_nonempty_proper_prefix"] and topology_before["stage_single_link"],
                         "partial checkpoint did not retain its own proper-prefix staging file")
            elif boundary == "staging_synced":
                _require(topology_before["stage_complete_size"] and topology_before["stage_single_link"],
                         "synced checkpoint did not retain its complete unlinked staging file")
            else:
                _require(topology_before["stage_complete_size"] and topology_before["stage_two_links"]
                         and topology_before["target_two_links"] and topology_before["stage_target_same_inode"],
                         "linked checkpoint did not retain the proven own hard-link pair")
        if target_present:
            _require(topology_before["target_private_regular_file"] and topology_before["target_complete_size"],
                     "installed current database has unexpected metadata")
            if boundary != "target_linked":
                _require(topology_before["target_single_link"], "ready current database has an unexpected alias")

        without_source = boundary in {"data_ready", "completed"}
        detached = private / f"interrupt-source-detached-{index:02d}"
        source_renamed = False
        try:
            if without_source:
                source_bundle.rename(detached)
                source_renamed = True
            # This process starts only after SIGKILL has been reaped and flock
            # released. Status must observe an alias without repairing it.
            interrupted = cli(boundary + "-status-after-kill", "status", job, target, operation)
            topology_status, identity_status = _topology(job, target, prepared["job_id"], source_size)
            _require(topology_status == topology_before and identity_status == identity_before,
                     "read-only status changed staging or committed-file metadata")
            expected_state, expected_count = expected_progress[boundary]
            _require(interrupted["state"] == expected_state and interrupted["copied_file_count"] == expected_count,
                     "status did not recover the acknowledged data progress")
            resumed = cli(boundary + "-ordinary-step-resume", "step", job, target, operation)
            completed = cli(boundary + "-ordinary-status-completed", "status", job, target, operation)
            _require(resumed == completed and completed["completed"] is True
                     and completed["copied_file_count"] == 3,
                     "ordinary continuation did not produce one stable completed result")
            _require(all(prepared[key] == completed[key] == interrupted[key]
                         for key in ("job_id", "operation_id", "created_at", "workspace_id", "total_file_count")),
                     "interruption changed the fixed operation identity")
            if boundary == "completed":
                _require(interrupted == resumed == completed, "completed-before-response fact changed on recovery")
            summary = public(_public_summary(workspace_backup.inspect_bundle(target), [], "inspect"))
            restored = Store(target / "state.sqlite3")
            observed = observe(restored)
            archives = {"oldest": observe(Store(target / "archives" / "history-0001.sqlite3")),
                        "nearest": observe(Store(target / "archives" / "history.sqlite3"))}
            _require(observations_match(captured, observed)
                     and all(observations_match(source_archives[label], value) for label, value in archives.items())
                     and canonical(summary) == canonical(completed["result"]),
                     "completed inspection or public historical records differ")
            final_topology, final_identity = _topology(job, target, prepared["job_id"], source_size)
            _require(not final_topology["stage_present"] and final_topology["target_single_link"]
                     and final_topology["outer_manifest_present"], "ordinary step left staging or an incomplete bundle")
            installed_inode_preserved = None
            if target_present:
                installed_inode_preserved = (identity_before[1] is not None and final_identity[1] is not None
                                             and identity_before[1][:2] == final_identity[1][:2])
                _require(installed_inode_preserved, "ordinary step replaced an already installed database inode")
        finally:
            if source_renamed:
                detached.rename(source_bundle)

        for action in ("enqueue", "publish"):
            forbidden_release = workspace / f"restore-interrupt-forbidden-release-{index:02d}"
            before_rejection = observe(restored)
            try:
                if action == "enqueue":
                    restored.enqueue("restore-interrupt-forbidden-write", "code_author", {})
                else:
                    restored.publish(forbidden_release)
            except WorkbenchError as error:
                _require(error.code == "store_snapshot_quarantined", "restored target refused an unexpected operation")
                _require(observations_match(before_rejection, observe(restored)) and not forbidden_release.exists(),
                         "a rejected write or publication changed the restored target")
                rejections.append({"boundary": boundary, "action": action, "code": error.code,
                                   "rejected": True, "public_observations_unchanged": True})
            else:
                raise RuntimeError("Restore interruption demonstration stopped: target quarantine did not block a write")

        case = {"boundary": boundary, "prepared": prepared, "worker": outcome,
                "status_after_kill": interrupted, "ordinary_step": resumed, "completed_status": completed,
                "full_summary": summary, "topology_after_kill": topology_before,
                "topology_after_readonly_status": topology_status, "topology_after_resume": final_topology,
                "status_changed_staging_or_target_metadata": False,
                "own_hardlink_pair_observed": boundary == "target_linked",
                "own_hardlink_pair_repaired_by_step": boundary == "target_linked",
                "installed_database_inode_preserved": installed_inode_preserved,
                "source_bundle_unavailable_during_recovery": without_source,
                "source_bundle_restored": True, "fixed_identity_preserved": True,
                "business_response_received_before_kill": False,
                "completed_before_response": boundary == "completed",
                "full_history_preserved": True, "old_authority_activated": False}
        cases.append(case)
        target_observations[boundary] = observed
        archive_observations[boundary] = archives
        stage(boundary.replace("_", "-"),
              "A trusted worker acknowledges the fixed boundary, is killed and reaped; ordinary status observes it and ordinary step resumes the same quarantined operation.",
              case)

    captured_final, live_final, previous_final = observe(source), observe(live), observe(previous)
    _require(observations_match(captured, captured_final) and observations_match(live_before, live_final)
             and observations_match(previous_before, previous_final)
             and all(observations_match(source_archives[label], observe(Store(source_bundle / "archives" / name)))
                     for label, name in (("oldest", "history-0001.sqlite3"), ("nearest", "history.sqlite3"))),
             "interrupted restores changed a baseline source, archive, live workspace or previous restore")
    stage("historical-and-live-state-preserved",
          "All five recovered bundles remain quarantined; the captured source, both archives, live R5 workspace and preceding restore retain their public records.",
          {"source_unchanged": True, "live_unchanged": True, "previous_restore_unchanged": True,
           "archives_unchanged": True, "rejections": rejections})

    data_artifacts = {
        "restore-interrupt-cases.json": cases,
        "restore-interrupt-worker-outcomes.json": worker_outcomes,
        "restore-interrupt-cli-outcomes.json": cli_outcomes,
        "restore-interrupt-rejections.json": rejections,
        "restore-interrupt-source-summary.json": source_summary,
        "restore-interrupt-source-observations.json": {"before": captured, "after": captured_final},
        "restore-interrupt-live-observations.json": {"before": live_before, "after": live_final},
        "restore-interrupt-previous-observations.json": {"before": previous_before, "after": previous_final},
        "restore-interrupt-target-observations.json": target_observations,
        "restore-interrupt-archive-observations.json": {"source": source_archives, "targets": archive_observations},
    }
    for name, value in data_artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Five fixed real-process interruptions of one quarantined historical restore format",
        "summary": {
            "baseline_public_file_count": len(baseline_public_files), "restore_interrupt_stage_count": len(stages),
            "restore_interrupt_case_count": len(cases), "restore_interrupt_worker_process_count": len(worker_outcomes),
            "restore_interrupt_cli_child_process_count": len(cli_outcomes),
            "restore_interrupt_child_process_count": len(worker_outcomes) + len(cli_outcomes),
            "total_cli_child_process_count": baseline_report["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": (baseline_report["summary"]["total_cli_child_process_count"]
                                                            + len(cli_outcomes) + len(worker_outcomes)),
            "observed_interrupted_file_counts": [case["status_after_kill"]["copied_file_count"] for case in cases],
            "actual_sigkill_count": len(worker_outcomes), "completed_before_response_case_count": 1,
            "own_partial_staging_observed": True, "own_full_staging_observed": True,
            "own_hardlink_pair_observed_and_repaired": True, "status_repairs_staging": False,
            "ready_recovered_without_source": True, "completed_recovered_without_source": True,
            "all_original_paths_restored": True, "operator_rejection_count": len(rejections),
            "captured_revision": captured["state"]["revision"], "captured_event_count": captured["status"]["events"],
            "live_revision": live_final["state"]["revision"], "live_event_count": live_final["status"]["events"],
            "complete_archive_count": source_summary["archive_count"], "restored_workspace_quarantined": True,
            "full_history_preserved": True, "old_authority_activated": False,
            "private_plan_contents_read_by_demo": False, "private_owner_contents_read_by_demo": False,
            "private_manifest_contents_read_by_demo": False, "private_origin_contents_read_by_demo": False,
            "private_fingerprints_exported": False, "hardware_or_power_failure_simulated": False,
            "external_fencing_established": False, "external_llm_used": False,
            "human_review_recorded": False, "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(data_artifacts),
        "limits": [
            "Five separate fresh three-data-file jobs are killed only after an exact allowlisted acknowledgement from the trusted worker; every child is reaped with return code -SIGKILL.",
            "The partial staging checkpoint follows flush and fsync of a nonempty proper prefix. The full staging checkpoint follows complete verification and fsync. The linked checkpoint follows link and target-parent fsync before unlink.",
            "Status runs after the worker has been killed and reaped. It observes a proven own hardlink pair without modifying metadata; ordinary step repairs that pair and resumes copying.",
            "Ready and completed recovery temporarily rename only the source bundle and restore it in finally. The earlier file-sized scenario separately exercises all original workspace paths becoming unavailable.",
            "The completed checkpoint is before API return: a business response was not issued or received. Stable recovered status is compared with later ordinary step and inspect, without asserting equality to nonexistent response bytes.",
            "This worker is restricted to the demonstration's current DB plus two archives; it is not a general interruption runner for the core's maximum seventeen data files.",
            "A local process SIGKILL does not establish hardware, kernel, storage or power-loss durability and does not cover every possible instruction, crash point, concurrent schedule or hostile filesystem mutation.",
            "The driver inspects only lstat metadata of fixed staging/DB/outer-manifest paths. Private plan, owner, checksum, manifest and origin contents are not read or exported.",
            "Public equality excludes only the changing top-level governance observed_at wrapper, preserving expired flags and full addressed proof bodies.",
            "Quarantine persists across recovery; historical grants, leases and opinions are not active authority. Programmed roles do not establish human independence, factual truth, external fencing or measured capacity.",
        ],
    }
    write_json("restore-interrupt-report.json", report)
    lines = [
        "# Восстановление после реального SIGKILL на фиксированных границах", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий файлового восстановления](restore-job-report.md).",
        "Его публичные файлы сохранены. Пять независимых операций восстанавливают тот же исторический",
        "комплект R4 с двумя архивами; действующая база остаётся R5. Новые полномочия не выдаются.", "",
        "## Наблюдавшиеся этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Что было прервано", "",
        "1. Непустой собственный staging-префикс после flush и fsync, до завершения файла.",
        "2. Полный проверенный staging после fsync, до установки ссылки назначения.",
        "3. Собственная пара hardlink после link и fsync родителя назначения, до удаления staging.",
        "4. Все три файла данных готовы; общий manifest ещё отсутствует.",
        "5. Завершение проверено; API ещё не вернул бизнес-ответ.", "",
        "Доверенный отдельный worker выдаёт только фиксированное подтверждение границы. Родитель",
        "проверяет живой процесс, отправляет именно ему SIGKILL и дожидается returncode -9. Только",
        "после reap обычный CLI status читает сохранённый прогресс: 0, 0, 1, 3, 3. Status не меняет",
        "наблюдавшуюся staging-топологию; обычный step чинит собственную пару и завершает комплект.", "",
        "Для ready и completed source bundle временно недоступен. Status, step, полный inspect, Store",
        "и оба архива работают из назначения. Каталог источника возвращён в finally. Во всех случаях",
        "сохраняются job ID, operation ID, время, workspace ID, R4, 36 событий и оба архива.", "",
        "В пятом случае ответ до SIGKILL ещё не выдавался. Сравниваются восстановленный факт и",
        "последующие обычные ответы; равенство несуществовавшим потерянным байтам не заявляется.", "",
        "## Публичные основания", "",
        "- [Пять случаев](restore-interrupt-cases.json), [процессы SIGKILL](restore-interrupt-worker-outcomes.json), [обычные CLI](restore-interrupt-cli-outcomes.json).",
        "- [Отказы записи и публикации](restore-interrupt-rejections.json), [исходная сводка](restore-interrupt-source-summary.json).",
        "- [Исторический источник](restore-interrupt-source-observations.json), [живая база](restore-interrupt-live-observations.json), [прежнее восстановление](restore-interrupt-previous-observations.json).",
        "- [Восстановленные базы](restore-interrupt-target-observations.json), [архивы](restore-interrupt-archive-observations.json), [машинный отчёт](restore-interrupt-report.json).", "",
        "## Границы результата", "",
        f"Новых этапов: {len(stages)}; SIGKILL: {len(worker_outcomes)}; обычных CLI: {len(cli_outcomes)}; отказов: {len(rejections)}.",
        "Это пять выбранных границ отдельного процесса, а не моделирование отключения питания, отказа",
        "ядра или хранилища. Все возможные точки прерывания, расписания и предельная нагрузка не проверялись.",
        "Worker рассчитан на этот комплект из трёх файлов данных. API восстановления допускает до",
        "17 файлов; текущая демонстрация не расширяет покрытие на максимальный комплект.", "",
        "Драйвер читает только публичные Store/API-ответы и узкие метаданные фиксированных путей.",
        "Частные планы, записи owner, manifests, контрольные суммы и origin не разбираются и не",
        "публикуются. Сравнение исключает только верхний governance.observed_at. Адресованные",
        "доказательства, timestamps и expired-признаки сохраняются. Карантин блокирует новые записи",
        "и публикацию; внешний fencing, LLM, независимая экспертиза и истинность содержания не установлены.", "",
    ]
    with (workspace / "restore-interrupt-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
