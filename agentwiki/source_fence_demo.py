"""Stop one continuing source through the cooperative current Store API.

The earlier recovery workflow remains intact. This extension reads no private
plan or fence envelope, runs no proposed code and activates no historical copy.
Two ordinary requests are launched together; their return times do not identify
the exact SQLite commit or prove that their lock attempts overlapped.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from threading import Barrier
import time
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS, run_recovery_reconciliation_demo
from .store import Store, WorkbenchError, canonical
from .store_backup_demo import CODE_AUTHOR


RECEIPT_FIELDS = {
    "schema_version", "rules_version", "fence_id", "source_epoch", "operation_id", "plan_id",
    "administrator_grant_id", "created_at", "revision", "event_high_water_before",
    "event_count_before", "fence_event_seq", "source_writes_blocked", "publication_blocked",
    "irreversible", "snapshot_activation_allowed", "external_fencing_established",
    "trusted_database_owner_can_bypass",
}
STATUS_FIELDS = {
    "rules_version", "source_fenced", "source_writes_blocked", "publication_blocked",
    "irreversible", "receipt",
}
RECEIPT_FLAGS = {
    "source_writes_blocked": True, "publication_blocked": True, "irreversible": True,
    "snapshot_activation_allowed": False, "external_fencing_established": False,
    "trusted_database_owner_can_bypass": True,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Source fence demonstration stopped: " + message)


def run_source_fence_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save a fresh fictional source, its seal and safe observable outcomes."""
    from . import recovery_reconciliation, source_fence, store_backup

    workspace = Path(workspace).expanduser().resolve()
    baseline_report = run_recovery_reconciliation_demo(workspace, workers)
    private = workspace / "private"
    database = private / "source" / "state.sqlite3"
    bundle = private / "backup-01"
    restored_database = private / "restore-01" / "state.sqlite3"
    plan = private / "recovery-plan-03.json"
    stale_plan = private / "recovery-plan-02.json"
    administrator_file = private / "admin.credential"
    administrator = read_credential(administrator_file)
    code_author = read_credential(private / "code-author.credential")
    coordinator = read_credential(private / "coordinator.credential")
    revoked_reviewer = read_credential(private / "reviewer-b.credential")
    repository = Path(__file__).resolve().parents[1]
    source, restored = Store(database), Store(restored_database)
    operation_id = "source-fence-demo-seal"
    baseline_public_files = sorted(path.name for path in workspace.iterdir()
                                   if path.is_file() and path.suffix in {".json", ".md"})
    private_markers = []
    for path in private.glob("*.credential"):
        value = read_credential(path)
        private_markers.extend((value, hashlib.sha256(value.encode("ascii")).hexdigest()))

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not FORBIDDEN_PUBLIC_FIELDS.intersection(value),
                     "a private field reached a public result")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers),
                     "a credential or its hash reached a public result")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def safe_receipt(value: Any, *, replay: bool = False) -> dict[str, Any]:
        fields = RECEIPT_FIELDS | ({"replayed"} if replay else set())
        _require(isinstance(value, dict) and set(value) == fields,
                 "the source fence returned unsupported public receipt fields")
        _require(value.get("rules_version") == "source-fence-1"
                 and value.get("schema_version") == 1
                 and bool(re.fullmatch(r"sfn_[0-9a-f]{32}", value.get("fence_id", "")))
                 and bool(re.fullmatch(r"sfe_[0-9a-f]{32}", value.get("source_epoch", "")))
                 and all(value.get(key) is expected for key, expected in RECEIPT_FLAGS.items()),
                 "a receipt lost its cooperative, irreversible boundary")
        if replay:
            _require(type(value["replayed"]) is bool, "the seal replay flag is invalid")
        return public({key: value[key] for key in sorted(fields)})

    def safe_status(value: Any) -> dict[str, Any]:
        _require(isinstance(value, dict) and set(value) == STATUS_FIELDS
                 and value.get("rules_version") == "source-fence-1"
                 and type(value.get("source_fenced")) is bool,
                 "the source fence returned unsupported public status fields")
        fenced = value["source_fenced"]
        _require(all(value.get(key) is fenced for key in (
            "source_writes_blocked", "publication_blocked", "irreversible")),
            "the source fence status flags disagree")
        result = dict(value)
        result["receipt"] = safe_receipt(value["receipt"]) if fenced else None
        _require(fenced or value["receipt"] is None, "an unfenced status includes a receipt")
        return public(result)

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(),
                "access": store.snapshot_access()}

    def observations_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {
                key: child for key, child in value["governance"].items() if key != "observed_at"
            }}
        return canonical(comparable(before)) == canonical(comparable(after))

    rejections: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    read_observations: list[dict[str, Any]] = []
    stages: list[dict[str, Any]] = []
    source_before, snapshot_before = observe(source), observe(restored)

    def stage(label: str, explanation: str, value: Any) -> None:
        name = f"fence-stage-{len(stages) + 1:02d}-{label}.json"
        body = {"label": label, "explanation": explanation, "source_status": source.status(),
                "snapshot_status": restored.status(), "observations": value}
        write_json(name, body)
        stages.append({"label": label, "explanation": explanation, "artifact": name,
                       "source_event_count": body["source_status"]["events"]})

    def rejected(label: str, action: Callable[[], Any], expected: str,
                 *, category: str = "operator", observed_store: Store | None = None) -> dict[str, Any]:
        watched = observed_store or source
        before = observe(watched)
        try:
            action()
        except WorkbenchError as error:
            _require(error.code == expected, "unexpected rejection for " + label + ": " + error.code)
            after = observe(watched)
            _require(observations_match(before, after), "a rejected operation changed public observations")
            result = {"label": label, "category": category, "code": error.code, "rejected": True,
                      "public_observations_unchanged": True,
                      "comparison_excludes_only_governance_wrapper_observed_at": True}
            rejections.append(result)
            return result
        raise RuntimeError("Source fence demonstration stopped: expected rejection for " + label)

    def cli(label: str, action: str) -> dict[str, Any]:
        arguments = ["--workspace", str(database.parent)]
        if action == "seal":
            arguments += ["--credential", str(administrator_file), "--plan", str(plan),
                          "--operation-id", operation_id]
        started_ns = time.monotonic_ns()
        with subprocess.Popen([sys.executable, "-m", "agentwiki", "store-fence", action, *arguments],
                              cwd=repository, shell=False, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate()
                raise RuntimeError("Source fence demonstration stopped: CLI child timed out") from error
            finished_ns = time.monotonic_ns()
            matched = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
            code = matched.group(1) if matched else None
            cli_outcomes.append({"label": label, "command": "store-fence", "action": action,
                                 "child_process_id": process.pid, "returncode": process.returncode,
                                 "error_code": code, "stderr_empty": not stderr,
                                 "started_monotonic_ns": started_ns, "finished_monotonic_ns": finished_ns,
                                 "shell": False, "completed_child_process": True})
        _require(process.returncode == 0 and not stderr,
                 "a fence CLI child failed with " + (code or "an unspecified error"))
        try:
            value = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Source fence demonstration stopped: invalid CLI JSON") from error
        return safe_receipt(value, replay=True) if action == "seal" else safe_status(value)

    status_initial = cli("unfenced-source-status", "status")
    _require(status_initial["source_fenced"] is False and not source_before["access"]["inspection_only"],
             "the continuing source was already fenced")
    stage("active-source-and-historical-copy",
          "The source continues at its current state; the old restored copy remains quarantined.",
          {"fence_status": status_initial, "source_access": source_before["access"],
           "snapshot_access": snapshot_before["access"]})

    rejected("stale-plan-cannot-seal-source", lambda: source_fence.seal_source(
        database, stale_plan, credential=administrator, operation_id="source-fence-stale-attempt"),
        "recovery_plan_stale", category="preseal")
    rejected("revoked-reviewer-cannot-seal-source", lambda: source_fence.seal_source(
        database, plan, credential=revoked_reviewer, operation_id="source-fence-unauthorized-attempt"),
        "governance_unauthorized", category="preseal")
    stage("stale-and-unauthorized-seals-rejected",
          "An old plan and a revoked non-administrator leave the source unfenced and unchanged.",
          {"rejections": rejections[-2:], "source_remains_unfenced":
           not source_fence.source_fence_status(database)["source_fenced"]})

    gate = Barrier(2)
    publication_output = workspace / "pre-fence-release"

    def publish_request() -> dict[str, Any]:
        gate.wait(timeout=30)
        started_ns = time.monotonic_ns()
        try:
            value = source.publish(publication_output)
        except WorkbenchError as error:
            _require(error.code == "store_source_fenced", "the raced publication failed for another reason")
            return {"outcome": "rejected_after_fence", "error_code": error.code,
                    "started_monotonic_ns": started_ns, "finished_monotonic_ns": time.monotonic_ns(),
                    "output_directory_created": publication_output.exists()}
        return public({"outcome": "completed", "started_monotonic_ns": started_ns,
                       "finished_monotonic_ns": time.monotonic_ns(), "release_id": value["release_id"],
                       "revision": value["revision"], "manifest_content_file_count": len(value["files"]),
                       "relative_directory": str(Path(value["path"]).relative_to(workspace)),
                       "release_files_exported_with_this_report": False})

    def seal_request() -> dict[str, Any]:
        gate.wait(timeout=30)
        return cli("seal-current-source", "seal")

    with ThreadPoolExecutor(max_workers=2) as executor:
        publish_future = executor.submit(publish_request)
        seal_future = executor.submit(seal_request)
        publication = publish_future.result()
        sealed = seal_future.result()
    _require(sealed["replayed"] is False, "the first seal unexpectedly replayed another operation")
    receipt = {key: sealed[key] for key in RECEIPT_FIELDS}
    source_after_seal = observe(source)
    _require(source_after_seal["events"][:-1] == source_before["events"]
             and source_after_seal["events"][-1]["type"] == "source_write_fenced"
             and source_after_seal["events"][-1]["data"] == receipt
             and source_after_seal["events"][-1]["seq"] == receipt["fence_event_seq"],
             "the seal did not append exactly its safe receipt event")
    _require(source_before["state"] == source_after_seal["state"]
             and source_before["tasks"] == source_after_seal["tasks"],
             "sealing changed content or task state")
    race = {"launch_barrier_participants": 2, "publication": publication,
            "seal_receipt": receipt, "writer_reservation_contract": "BEGIN IMMEDIATE through publication cleanup and seal commit",
            "observed_return_times_are_commit_times": False, "lock_attempt_overlap_measured": False,
            "both_orderings_intentionally_forced": False, "forced_delays_or_hooks_used": False,
            "source_event_count_before": source_before["status"]["events"],
            "source_event_count_after": source_after_seal["status"]["events"]}
    if publication["outcome"] == "rejected_after_fence":
        _require(not publication_output.exists(), "a blocked raced publication created output")
    stage("publication-and-seal-launched-together",
          "Two ordinary requests share a launch barrier. Publication either completes under its writer reservation or observes the committed fence.",
          race)

    reopened = Store(database)
    reopened_access = reopened.snapshot_access()
    _require(reopened_access["source_fenced"] is True and reopened_access["inspection_only"] is True
             and reopened_access["domain_writes_allowed"] is False,
             "reopening the source lost its persistent fence")
    status_fenced = cli("fenced-source-status", "status")
    _require(status_fenced["receipt"] == receipt,
             "public status does not return the original seal receipt")
    stage("persistent-fence-remains-readable",
          "An existing Store and a reopened Store read the current history and observe the same irreversible source fence.",
          {"status": status_fenced, "existing_access": source_after_seal["access"],
           "reopened_access": reopened_access, "content_and_tasks_preserved": True})

    before_replay = observe(source)
    replayed = cli("exact-seal-operation-replay", "seal")
    after_replay = observe(source)
    _require(replayed["replayed"] is True
             and {key: replayed[key] for key in RECEIPT_FIELDS} == receipt
             and observations_match(before_replay, after_replay),
             "the exact seal replay changed its receipt or source observations")
    read_observations.append({"label": "exact-seal-replay", "source_public_observations_unchanged": True,
                              "source_status_before": before_replay["status"],
                              "source_status_after": after_replay["status"],
                              "comparison_excludes_only_governance_wrapper_observed_at": True})
    rejected("another-seal-operation-conflicts", lambda: source_fence.seal_source(
        database, plan, credential=administrator, operation_id="another-source-fence-operation"),
        "source_fence_conflict", category="conflict")
    rejected("same-operation-with-another-plan-conflicts", lambda: source_fence.seal_source(
        database, stale_plan, credential=administrator, operation_id=operation_id),
        "source_fence_conflict", category="conflict")
    stage("exact-replay-and-input-conflicts",
          "The original operation returns the saved receipt without another event; a different operation or plan conflicts.",
          {"replayed_receipt": replayed, "conflicts": rejections[-2:],
           "events_unchanged_by_exact_replay": True})

    task = next(value for value in source.tasks() if value["task_id"] == "recovery-live-heartbeat-task")
    acceptance = json.loads((workspace / "acceptance.json").read_text())
    grant = json.loads((workspace / "grants.json").read_text())["code-author"]
    blocked_output = workspace / "fence-blocked-release"
    source_calls: list[tuple[str, Callable[[], Any]]] = [
        ("existing-store-enqueue", lambda: source.enqueue("fence-blocked-task", "code_author", {})),
        ("existing-store-heartbeat", lambda: source.heartbeat(
            task["task_id"], CODE_AUTHOR, task["token"], lease_seconds=7200, credential=code_author)),
        ("existing-store-claim", lambda: source.claim_task(CODE_AUTHOR, ["code_author"], credential=code_author)),
        ("existing-store-submit", lambda: source.submit(
            task["task_id"], CODE_AUTHOR, task["token"],
            {"readset": {}, "queries": {}, "writes": [], "rationale": "Not admitted after source fence."},
            credential=code_author)),
        ("original-grant-operation-replay", lambda: source.governance_grant(
            CODE_AUTHOR, "code_author", grant["artifact_ids"], grant["expires_at"],
            credential=administrator, new_credential=code_author, operation_id=grant["operation_id"])),
        ("accepted-proposal-operation-replay", lambda: source.accept(
            acceptance["proposal_id"], "snapshot-coordinator", credential=coordinator)),
        ("publication-after-fence", lambda: source.publish(blocked_output)),
        ("reopened-store-enqueue", lambda: reopened.enqueue("reopened-fence-blocked-task", "code_author", {})),
    ]
    for label, action in source_calls:
        rejected(label, action, "store_source_fenced", category="source_write")
    _require(not blocked_output.exists(), "a post-fence publication created an output directory")
    stage("normal-source-writes-and-replays-blocked",
          "Existing and reopened Store paths reject ordinary writes, old leases, historical write replays and publication before their action bodies.",
          {"rejections": [value for value in rejections if value["category"] == "source_write"],
           "blocked_publication_directory_created": False})

    blocked_backup = private / "fence-blocked-backup"
    blocked_assessment = private / "fence-blocked-assessment.recovery-plan.json"
    rejected("direct-backup-of-fenced-source", lambda: store_backup.create_backup(
        database, blocked_backup, credential=administrator), "store_source_fenced", category="source_helper")
    rejected("store-backup-of-fenced-source", lambda: source.create_backup(
        private / "fence-blocked-store-backup", credential=administrator),
        "store_source_fenced", category="source_helper")
    rejected("assessment-cannot-use-fenced-live-authority", lambda: recovery_reconciliation.assess_recovery(
        bundle, database, blocked_assessment, credential=administrator),
        "recovery_source_invalid", category="source_helper")
    rejected("revalidation-cannot-use-fenced-live-authority", lambda: recovery_reconciliation.revalidate_recovery(
        plan, database, credential=administrator), "recovery_source_invalid", category="source_helper")
    _require(not blocked_backup.exists() and not blocked_assessment.exists(),
             "a rejected helper created a private result")
    stage("live-authority-helpers-reject-fenced-source",
          "A fenced source cannot authorize new backup or recovery assessment through the live-source APIs.",
          {"rejections": [value for value in rejections if value["category"] == "source_helper"],
           "new_private_results_created": False})

    rejected("quarantined-copy-is-not-a-source-fence-status-target", lambda: source_fence.source_fence_status(
        restored_database), "source_fence_source_invalid", category="snapshot")
    rejected("snapshot-writes-still-quarantined", lambda: restored.enqueue(
        "fence-snapshot-blocked-task", "code_author", {}),
        "store_snapshot_quarantined", category="snapshot", observed_store=restored)
    snapshot_after = observe(restored)
    _require(observations_match(snapshot_before, snapshot_after)
             and snapshot_after["access"]["quarantined"] is True
             and snapshot_after["access"]["domain_writes_allowed"] is False,
             "sealing the source changed or activated the old copy")
    final_source = observe(source)
    _require(observations_match(source_after_seal, final_source),
             "post-fence operations changed the source public observations")
    _require(sum(event["type"] == "source_write_fenced" for event in final_source["events"]) == 1,
             "more than one source fence event was recorded")
    stage("historical-copy-retains-its-quarantine",
          "The historical copy remains unchanged and quarantined; the source remains readable with exactly one seal event.",
          {"snapshot_status_before": snapshot_before["status"], "snapshot_status_after": snapshot_after["status"],
           "snapshot_public_observations_unchanged": True, "source_public_observations_unchanged_after_seal": True,
           "comparison_excludes_only_governance_wrapper_observed_at": True})

    artifacts = {
        "fence-receipts.json": {"sealed": sealed, "replayed": replayed},
        "fence-statuses.json": {"initial": status_initial, "fenced": status_fenced,
                                "existing_store_access": final_source["access"], "reopened_store_access": reopened_access},
        "fence-cli-outcomes.json": cli_outcomes, "fence-race.json": race,
        "fence-rejections.json": rejections,
        "fence-observations.json": {"before": source_before, "after": final_source},
        "fence-events.json": final_source["events"],
        "fence-snapshot-unchanged.json": {"public_observations_unchanged": True,
                                           "status_before": snapshot_before["status"], "status_after": snapshot_after["status"],
                                           "comparison_excludes_only_governance_wrapper_observed_at": True},
        "fence-read-observations.json": read_observations,
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": "source-fence-1",
        "kind": "cooperative_source_write_fence_product_example",
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        "baseline_report": "recovery-report.md", "baseline_public_files": baseline_public_files,
        "stages": stages,
        "summary": {
            "baseline_public_file_count": len(baseline_public_files), "fence_stage_count": len(stages),
            "baseline_observed_author_thread_count": baseline_report["summary"]["baseline_observed_author_thread_count"],
            "fence_cli_child_process_count": len(cli_outcomes),
            "total_cli_child_process_count": baseline_report["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "seal_event_count": 1, "source_event_count_before": source_before["status"]["events"],
            "source_event_count_after": final_source["status"]["events"],
            "source_runtime_revision": final_source["status"]["revision"],
            "snapshot_event_count": snapshot_after["status"]["events"],
            "exact_seal_replay_count": 1,
            "operator_rejection_count": len(rejections),
            "normal_source_write_rejection_count": sum(value["category"] == "source_write" for value in rejections),
            "live_source_helper_rejection_count": sum(value["category"] == "source_helper" for value in rejections),
            "publication_race_outcome": publication["outcome"],
            "concurrent_request_launch_count": 2, "lock_attempt_overlap_measured": False,
            "source_fence_persisted": True, "source_fence_reversible_through_api": False,
            "snapshot_activated": False, "production_failover_performed": False,
            "external_fencing_established": False, "older_store_binary_blocked": False,
            "private_plan_contents_read_by_demo": False, "private_fence_envelope_read_by_demo": False,
            "private_fingerprints_exported": False, "measured_capacity": False,
            "external_llm_used": False, "proposed_code_executed": False,
            "network_failure_simulated": False, "process_crash_simulated": False,
        },
        "limitations": [
            "The persistent marker is a cooperative fence enforced by the current Store API on the same canonical database, not a global operating-system or distributed authority boundary.",
            "A trusted SQLite owner, raw SQL caller or older Store version that does not recognize the marker can bypass this protocol. All participating workers must use the current implementation.",
            "Publication and seal requests are launched together. Return-time observations do not establish exact commit times, and the demonstration neither measures lock-attempt overlap nor forces both scheduling outcomes.",
            "The current implementation holds a writer reservation through all publication rendering, staging and cleanup. The safe ordering claim also relies on static review of that implementation.",
            "A completed raced release is an immutable local export made before the seal's writer reservation. Its files are left in the run workspace and are not exported with these flat public reports.",
            "The original private plan and request digest remain private. Exact replay rechecks current original administrative authority and returns the same saved receipt without another event.",
            "Read observations omit only governance_status's changing top-level observed_at for comparison. Raw timestamps, grant expiry flags and addressed proof bodies are preserved.",
            "Earlier read-only backup or assessment requests can finish writing historical or advisory files after the seal. No global guarantee that all filesystem activity stops is claimed.",
            "No historical copy is activated, no grants are transferred, no leases are reassigned, and no production service switch or external effects reconciliation is performed.",
            "The local database owner, implementation and clock remain trusted. Local IDs, equal receipts and immutable events are not signatures or external completeness attestations.",
        ],
    }
    write_json("fence-report.json", report)
    lines = [
        "# Выполненный постоянный запрет записи исходной среды", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала выполнен [сценарий сверки восстановления](recovery-report.md). Его публичные основания",
        "сохранены без перезаписи. Источник продолжил работу, а прежняя копия осталась в карантине.", "",
        "## Последовательность", "", "| Этап | Наблюдение | Событий источника |", "|---|---|---:|",
    ]
    lines += [f"| [{value['label']}]({value['artifact']}) | {value['explanation']} | {value['source_event_count']} |"
              for value in stages]
    lines += [
        "", "## Что зафиксировано", "",
        "Свежий частный план и действующий исходный администратор закрепили постоянную метку в meta.",
        "Запись метки и безопасного события выполнена одной транзакцией. Прежний план и отозванный",
        "reviewer были отклонены до остановки. Содержание и задачи сохранены, добавлено одно событие.", "",
        "После повторного открытия источник доступен для чтения. Точный повтор операции вернул исходную",
        "квитанцию без нового события; другой operation ID или другой план вызвали конфликт.", "",
        "## Публикация и остановка", "",
        "Два обычных запроса — публикация и CLI seal — запущены через общий барьер в двух потоках.",
        f"Фактический исход публикации: `{publication['outcome']}`. Новый publish после остановки отклонён до создания каталога.",
        "Времена возврата запросов сохранены как наблюдения, без выдачи их за точное время commit.",
        "Перекрытие попыток SQLite-блокировок не измерялось; обе очередности намеренно не навязывались.",
        "Граница порядка опирается также на статически проверенную блокировку записи, охватывающую",
        "рендеринг, запись файлов и уборку публикации. Успевший выпуск остаётся в каталоге запуска и",
        "не входит в плоский набор файлов этого отчёта.", "",
        "## Публичные основания", "",
        "- [Квитанции и replay](fence-receipts.json), [статусы и доступ](fence-statuses.json).",
        "- [Исходы CLI](fence-cli-outcomes.json), [конкурирующие запросы](fence-race.json).",
        "- [Ожидаемые отказы](fence-rejections.json), [наблюдения источника](fence-observations.json).",
        "- [События](fence-events.json), [неизменившаяся копия](fence-snapshot-unchanged.json).",
        "- [Наблюдение replay](fence-read-observations.json), [машинный отчёт](fence-report.json).", "",
        "## Граница результата", "",
        f"Новых этапов: {len(stages)}; новых CLI-процессов: {len(cli_outcomes)}; ожидаемых отказов: {len(rejections)}.",
        f"Событий источника: {source_before['status']['events']} → {final_source['status']['events']}; ревизия: {final_source['status']['revision']}; событий копии: {snapshot_after['status']['events']}.",
        "Это запрет кооперативного текущего Store API для одной канонической базы. Старые версии Store,",
        "сырой SQL и доверенный владелец SQLite способны его обойти. Внешний или распределённый fence",
        "не установлен. Снятие запрета и активация старой копии через API отсутствуют.", "",
        "Чтения старых backup/assessment могут завершить исторические или совещательные файлы позднее;",
        "запрет всей файловой активности не обещается. Полное переключение рабочей среды требует",
        "отдельного согласования внешних эффектов, новых полномочий и аренд.", "",
        "Демонстратор не читает содержимое частных планов или envelope метки. Credentials, SQLite,",
        "контрольные суммы и частные request digests не публикуются. Предлагаемый код не исполняется;",
        "внешние LLM, имитация аварий и измерение предельной нагрузки отсутствуют.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "fence-report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
