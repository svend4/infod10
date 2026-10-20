"""Compare a quarantined snapshot with one continuing, trusted live source.

The existing snapshot workflow is run once in a new workspace. This extension
adds private reconciliation plans and public observations without reading those
plans, exporting their fingerprints, activating a copy or executing a proposal.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .governance_cli import prepare_credential, read_credential
from .review_continuity_demo import CODE_A, CODE_D, DOC_A, DOC_D, _document, _file
from .store import Store, WorkbenchError, canonical
from .store_backup_demo import CODE_AUTHOR, PRIVATE_PROOF_FIELDS, run_store_backup_demo


ASSESSMENT_FIELDS = {
    "schema_version", "rules_version", "plan_id", "created_at", "snapshot", "source",
    "administrator_grant_id", "event_prefix", "append_only_preserved", "differences",
    "source_rows_match_at_both_observations", "time_semantics", "advisory_only",
    "activation_allowed", "domain_writes_performed", "private_fingerprints_exported",
}
REVALIDATION_FIELDS = {
    "rules_version", "plan_id", "observed_at", "database_rows_unchanged_at_observation",
    "current_administrator_authorized", "elapsed_seconds", "source", "advisory_only",
    "activation_allowed", "domain_writes_performed", "private_fingerprints_exported",
}
FORBIDDEN_PUBLIC_FIELDS = PRIVATE_PROOF_FIELDS | {
    "source_fingerprint", "snapshot_fingerprint", "row_fingerprint", "table_fingerprints",
    "request_digest", "database_sha256", "source_path",
}
FLAGS = {
    "advisory_only": True, "activation_allowed": False,
    "domain_writes_performed": False, "private_fingerprints_exported": False,
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Recovery reconciliation demonstration stopped: " + message)


def run_recovery_reconciliation_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Save an ordinary workflow and its private plans into a new workspace."""
    from . import recovery_reconciliation

    workspace = Path(workspace).expanduser().resolve()
    baseline_report = run_store_backup_demo(workspace, workers)
    private = workspace / "private"
    database = private / "source" / "state.sqlite3"
    bundle = private / "backup-01"
    restored_database = private / "restore-01" / "state.sqlite3"
    repository = Path(__file__).resolve().parents[1]
    source = Store(database)
    restored = Store(restored_database)
    administrator_file = private / "admin.credential"
    administrator = read_credential(administrator_file)
    code_author = read_credential(private / "code-author.credential")
    revoked_reviewer = read_credential(private / "reviewer-b.credential")
    private_markers: list[str] = []

    def remember_credentials() -> None:
        for path in private.glob("*.credential"):
            value = read_credential(path)
            private_markers.extend((value, hashlib.sha256(value.encode("ascii")).hexdigest()))

    remember_credentials()

    def public(value: Any) -> Any:
        if isinstance(value, dict):
            _require(not FORBIDDEN_PUBLIC_FIELDS.intersection(value),
                     "a private field reached a public result")
            return {public(key): public(child) for key, child in value.items()}
        if isinstance(value, (list, tuple)):
            return [public(child) for child in value]
        if isinstance(value, str):
            _require(not any(marker in value for marker in private_markers),
                     "a credential or credential hash reached a public result")
        return value

    def write_json(name: str, value: Any) -> None:
        with (workspace / name).open("x", encoding="utf-8") as stream:
            stream.write(json.dumps(public(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")

    def safe_result(value: Any, action: str) -> dict[str, Any]:
        fields = ASSESSMENT_FIELDS if action == "assess" else REVALIDATION_FIELDS
        _require(isinstance(value, dict) and set(value) == fields,
                 "the reconciliation API returned unsupported public fields")
        _require(value.get("rules_version") == "recovery-reconciliation-1"
                 and isinstance(value.get("plan_id"), str)
                 and bool(re.fullmatch(r"rcp_[0-9a-f]{32}", value["plan_id"]))
                 and all(value.get(key) is expected for key, expected in FLAGS.items()),
                 "a reconciliation result lost its advisory boundary")
        if action == "assess":
            _require(value["append_only_preserved"] is True
                     and value["event_prefix"].get("matches") is True
                     and value["source_rows_match_at_both_observations"] is True
                     and value["snapshot"].get("inspection_only") is True
                     and value["snapshot"].get("domain_writes_allowed") is False,
                     "the assessment did not preserve the snapshot boundary")
        else:
            _require(value["database_rows_unchanged_at_observation"] is True
                     and value["current_administrator_authorized"] is True,
                     "revalidation did not confirm its observed state")
        return public({key: value[key] for key in sorted(fields)})

    def observe(store: Store) -> dict[str, Any]:
        return {"status": store.status(), "state": store.snapshot(), "tasks": store.tasks(),
                "events": store.events(), "governance": store.governance_status(),
                "access": store.snapshot_access()}

    def observations_match(before: dict[str, Any], after: dict[str, Any]) -> bool:
        # governance_status samples a new wall-clock observed_at on every read.
        # Exclude only that wrapper timestamp from comparison, without changing
        # either saved observation or any addressed decision/receipt body.
        def comparable(value: dict[str, Any]) -> dict[str, Any]:
            return {**value, "governance": {
                key: child for key, child in value["governance"].items() if key != "observed_at"
            }}
        return canonical(comparable(before)) == canonical(comparable(after))

    source_baseline = observe(source)
    restored_before = observe(restored)
    _require(source_baseline["access"]["inspection_only"] is False
             and restored_before["access"]["inspection_only"] is True,
             "source and snapshot access modes are not distinct")
    _require(source_baseline["events"][:len(restored_before["events"])] == restored_before["events"],
             "the baseline snapshot history is not the beginning of source history")
    baseline_public_files = sorted(path.name for path in workspace.iterdir()
                                   if path.is_file() and path.suffix in {".json", ".md"})
    cli_outcomes: list[dict[str, Any]] = []
    read_observations: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    assessments: dict[str, dict[str, Any]] = {}
    revalidations: dict[str, dict[str, Any]] = {}
    stages: list[dict[str, Any]] = []

    def stage(label: str, explanation: str, value: Any) -> None:
        name = f"recovery-stage-{len(stages) + 1:02d}-{label}.json"
        body = {"label": label, "explanation": explanation, "source_status": source.status(),
                "snapshot_status": restored.status(), "observations": value}
        write_json(name, body)
        stages.append({"label": label, "explanation": explanation, "artifact": name,
                       "source_event_count": body["source_status"]["events"]})

    def cli(label: str, action: str, plan: Path, *, expected_code: str | None = None) -> dict[str, Any]:
        arguments = ["--workspace", str(database.parent), "--credential", str(administrator_file),
                     "--plan", str(plan)]
        if action == "assess":
            arguments.extend(("--bundle", str(bundle)))
        before = observe(source)
        with subprocess.Popen([sys.executable, "-m", "agentwiki", "store-recovery", action, *arguments],
                              cwd=repository, shell=False, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate()
                raise RuntimeError("Recovery reconciliation demonstration stopped: CLI child timed out") from error
            matched = re.search(r"\[([a-z][a-z0-9_]*)\]", stderr)
            code = matched.group(1) if matched else None
            outcome = {"label": label, "command": "store-recovery", "action": action,
                       "child_process_id": process.pid, "returncode": process.returncode,
                       "error_code": code, "stdout_empty": not stdout, "stderr_empty": not stderr,
                       "shell": False, "completed_child_process": True}
            cli_outcomes.append(outcome)
        after = observe(source)
        _require(observations_match(before, after),
                 "a reconciliation CLI call changed the public source observations")
        read_observations.append({"label": label, "source_public_observations_unchanged": True,
                                  "source_status_before": before["status"], "source_status_after": after["status"],
                                  "governance_observed_at_before": before["governance"].get("observed_at"),
                                  "governance_observed_at_after": after["governance"].get("observed_at"),
                                  "comparison_excludes_only_governance_wrapper_observed_at": True})
        if expected_code is not None:
            _require(process.returncode != 0 and code == expected_code and not stdout,
                     "a stale-plan CLI call did not return the expected safe error")
            rejection = {"label": label, "code": code, "rejected": True,
                         "source_public_observations_unchanged": True}
            rejections.append(rejection)
            return rejection
        _require(process.returncode == 0 and not stderr,
                 "a reconciliation CLI child failed with " + (code or "an unspecified error"))
        try:
            result = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as error:
            raise RuntimeError("Recovery reconciliation demonstration stopped: invalid CLI JSON") from error
        return safe_result(result, action)

    def rejected(label: str, operation: Callable[[], Any], code: str) -> dict[str, Any]:
        before = observe(source)
        try:
            operation()
        except WorkbenchError as error:
            _require(error.code == code, "unexpected rejection for " + label + ": " + error.code)
            _require(observations_match(before, observe(source)),
                     "a rejected API call changed the public source observations")
            result = {"label": label, "code": code, "rejected": True,
                      "source_public_observations_unchanged": True}
            rejections.append(result)
            return result
        raise RuntimeError("Recovery reconciliation demonstration stopped: expected rejection for " + label)

    stage("continuing-source-and-historical-copy",
          "The live source has continued after capture; its old snapshot stays quarantined and readable.",
          {"source": source_baseline["status"], "snapshot": restored_before["status"],
           "source_access": source_baseline["access"], "snapshot_access": restored_before["access"],
           "captured_events_are_exact_source_prefix": True})

    task_id = "recovery-live-heartbeat-task"
    source.enqueue(task_id, "code_author", {"artifact_id": CODE_D, "purpose": "live lease observation"})
    lease = source.claim_task(CODE_AUTHOR, ["code_author"], lease_seconds=3600, credential=code_author)
    _require(lease is not None and lease["task_id"] == task_id, "the new author task was not leased")
    lease_before = next(task for task in source.tasks() if task["task_id"] == task_id)
    stage("fresh-live-author-lease",
          "A new authenticated task and lease exist only in the continuing source, not in the historical copy.",
          {"lease": lease, "task": lease_before})

    plan_one, plan_two, plan_three = (private / f"recovery-plan-{index:02d}.json" for index in range(1, 4))
    assessments["initial"] = cli("initial-assessment", "assess", plan_one)
    revalidations["initial"] = cli("initial-revalidation", "revalidate", plan_one)
    _require(assessments["initial"]["plan_id"] == revalidations["initial"]["plan_id"],
             "revalidation identified another plan")
    stage("initial-plan-observed-current",
          "Live administrative authority creates a private plan; a separate process confirms unchanged rows at observation.",
          {"assessment": assessments["initial"], "revalidation": revalidations["initial"]})

    heartbeat_status_before = source.status()
    heartbeat_events_before = source.events()
    source.heartbeat(task_id, CODE_AUTHOR, lease["token"], lease_seconds=7200, credential=code_author)
    lease_after = next(task for task in source.tasks() if task["task_id"] == task_id)
    heartbeat_status_after = source.status()
    heartbeat_events_after = source.events()
    _require(heartbeat_status_before == heartbeat_status_after
             and heartbeat_events_before == heartbeat_events_after
             and lease_before["lease_until"] != lease_after["lease_until"],
             "the ordinary heartbeat did not isolate an eventless row change")
    heartbeat_rejection = cli("plan-stale-after-eventless-heartbeat", "revalidate", plan_one,
                              expected_code="recovery_plan_stale")
    heartbeat = {"task_before": lease_before, "task_after": lease_after,
                 "status_before": heartbeat_status_before, "status_after": heartbeat_status_after,
                 "events_before": heartbeat_events_before, "events_after": heartbeat_events_after,
                 "runtime_revision_unchanged": True, "event_count_unchanged": True,
                 "event_bodies_unchanged": True, "lease_until_changed": True,
                 "old_plan_rejected": heartbeat_rejection}
    stage("eventless-heartbeat-invalidates-plan",
          "An ordinary heartbeat changes lease_until without changing runtime revision or events; full-row revalidation rejects the old plan.",
          heartbeat)

    assessments["after_heartbeat"] = cli("assessment-after-heartbeat", "assess", plan_two)
    revalidations["after_heartbeat"] = cli("revalidation-after-heartbeat", "revalidate", plan_two)
    _require(assessments["after_heartbeat"]["plan_id"] != assessments["initial"]["plan_id"],
             "a refreshed assessment reused a plan ID")
    stage("fresh-plan-after-heartbeat",
          "A new private plan describes the updated lease and can be revalidated in a later process.",
          {"assessment": assessments["after_heartbeat"], "revalidation": revalidations["after_heartbeat"]})

    event_status_before = source.status()
    source.enqueue("recovery-later-pending-task", "code_author",
                   {"artifact_id": CODE_D, "purpose": "later ordinary source event"})
    event_status_after = source.status()
    _require(event_status_after["events"] == event_status_before["events"] + 1
             and event_status_after["revision"] == event_status_before["revision"],
             "the later task did not isolate a new event at the same runtime revision")
    event_rejection = cli("plan-stale-after-later-source-event", "revalidate", plan_two,
                          expected_code="recovery_plan_stale")
    stage("later-event-invalidates-plan",
          "An ordinary task enqueue adds an event without an accepted revision; the previous plan is stale again.",
          {"source_before": event_status_before, "source_after": event_status_after,
           "old_plan_rejected": event_rejection})

    assessments["final"] = cli("final-assessment", "assess", plan_three)
    revalidations["final"] = cli("final-revalidation", "revalidate", plan_three)
    _require(len({assessment["plan_id"] for assessment in assessments.values()}) == 3,
             "three independent assessments did not create distinct plan IDs")
    stage("final-current-observation",
          "A third private plan includes the later event; revalidation remains advisory and does not activate the copy.",
          {"assessment": assessments["final"], "revalidation": revalidations["final"]})

    unauthorized_plan = private / "unauthorized-recovery-plan.json"
    quarantined_plan = private / "quarantined-source-recovery-plan.json"
    rejected("revoked-reviewer-is-not-live-administrator", lambda: recovery_reconciliation.assess_recovery(
        bundle, database, unauthorized_plan, credential=revoked_reviewer), "governance_unauthorized")
    rejected("quarantined-copy-cannot-be-live-source", lambda: recovery_reconciliation.assess_recovery(
        bundle, restored_database, quarantined_plan, credential=administrator), "recovery_source_invalid")
    plan_signature_before = (plan_one.stat().st_ino, plan_one.stat().st_size, plan_one.stat().st_mtime_ns)
    rejected("existing-plan-is-not-overwritten", lambda: recovery_reconciliation.assess_recovery(
        bundle, database, plan_one, credential=administrator), "recovery_plan_exists")
    _require(plan_signature_before == (plan_one.stat().st_ino, plan_one.stat().st_size, plan_one.stat().st_mtime_ns),
             "an existing private plan was replaced or modified")

    unrelated_directory = private / "unrelated-source"
    unrelated_directory.mkdir(mode=0o700)
    unrelated_directory.chmod(0o700)
    unrelated_database = unrelated_directory / "state.sqlite3"
    unrelated_database.touch(mode=0o600, exist_ok=False)
    unrelated_database.chmod(0o600)
    unrelated = Store(unrelated_database)
    unrelated.bootstrap([_document(DOC_A, 3), _document(DOC_D, 3),
                         _file(CODE_A, "code", "SAMPLE_LIMIT = 3\n"),
                         _file(CODE_D, "code", "SAMPLE_LIMIT = 3\n")])
    unrelated_administrator = prepare_credential(private / "unrelated-administrator.credential")
    remember_credentials()
    unrelated.governance_enable({"schema_version": 1, "minimum_reviewers": 2, "separate_coordinator": True},
                                "unrelated-recovery-administrator", unrelated_administrator)
    unrelated_before = observe(unrelated)
    unrelated_plan = private / "unrelated-source-recovery-plan.json"
    rejected("unrelated-live-source-has-no-original-admin-lineage", lambda: recovery_reconciliation.assess_recovery(
        bundle, unrelated_database, unrelated_plan, credential=unrelated_administrator), "recovery_lineage_mismatch")
    _require(observations_match(unrelated_before, observe(unrelated)),
             "a rejected assessment changed the unrelated source")
    _require(not unauthorized_plan.exists() and not quarantined_plan.exists() and not unrelated_plan.exists(),
             "a rejected assessment created a private plan")
    stage("safe-operator-rejections",
          "Revoked authority, a quarantined live source, an existing plan and an unrelated authorized source are rejected without raw SQL corruption.",
          {"api_rejections": rejections[-4:], "existing_plan_preserved": True,
           "unauthorized_plan_created": False, "quarantined_source_plan_created": False,
           "unrelated_source_plan_created": False, "unrelated_public_observations_unchanged": True})

    restored_after = observe(restored)
    _require(observations_match(restored_before, restored_after),
             "the historical copy changed during reconciliation")
    _require(restored_after["access"]["inspection_only"] is True
             and restored_after["access"]["domain_writes_allowed"] is False
             and source.snapshot_access()["inspection_only"] is False,
             "a source or snapshot access boundary changed")
    for plan in (plan_one, plan_two, plan_three):
        _require(plan.is_file() and plan.stat().st_mode & 0o777 == 0o600,
                 "a private plan did not retain owner-only permissions")
    final_source = observe(source)
    artifacts = {
        "recovery-assessments.json": assessments, "recovery-revalidations.json": revalidations,
        "recovery-cli-outcomes.json": cli_outcomes, "recovery-rejections.json": rejections,
        "recovery-read-observations.json": read_observations, "recovery-heartbeat.json": heartbeat,
        "recovery-baseline-observations.json": {"source": source_baseline, "snapshot": restored_before},
        "recovery-final-observations.json": {"source": final_source, "snapshot": restored_after},
        "recovery-events.json": final_source["events"], "recovery-tasks.json": final_source["tasks"],
        "recovery-snapshot-unchanged.json": {"public_observations_unchanged": True,
                                             "comparison_excludes_only_governance_wrapper_observed_at": True,
                                             "before": restored_before, "after": restored_after},
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "kind": "live_source_recovery_reconciliation_product_example",
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "created_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(),
        "rules_version": "recovery-reconciliation-1", "workers": workers,
        "baseline_report": "report.md", "baseline_public_files": baseline_public_files,
        "stages": stages,
        "summary": {
            "baseline_public_file_count": len(baseline_public_files),
            "baseline_author_job_count": baseline_report["summary"]["author_job_count_before_capture"],
            "baseline_observed_author_thread_count": baseline_report["summary"]["observed_author_thread_count"],
            "recovery_stage_count": len(stages), "assessment_count": len(assessments),
            "successful_revalidation_count": len(revalidations), "stale_plan_rejection_count": 2,
            "operator_api_rejection_count": len(rejections) - 2,
            "recovery_cli_child_process_count": len(cli_outcomes),
            "total_cli_child_process_count": len(cli_outcomes) + baseline_report["summary"]["cli_child_process_count"],
            "snapshot_event_count": restored_before["status"]["events"],
            "source_events_before_recovery_workflow": source_baseline["status"]["events"],
            "source_events_at_initial_assessment": assessments["initial"]["source"]["event_count"],
            "source_events_at_final_assessment": assessments["final"]["source"]["event_count"],
            "snapshot_runtime_revision": restored_before["status"]["revision"],
            "final_source_runtime_revision": final_source["status"]["revision"],
            "eventless_heartbeat_detected": heartbeat["lease_until_changed"] and heartbeat["event_bodies_unchanged"],
            "runtime_revision_sufficient_for_plan_freshness": False,
            "event_counter_sufficient_for_plan_freshness": False,
            "source_public_observations_unchanged_by_all_recovery_cli_calls": all(
                observation["source_public_observations_unchanged"] for observation in read_observations),
            "historical_copy_public_observations_unchanged": True,
            "observation_comparison_excludes_only_governance_wrapper_observed_at": True,
            "private_plan_count": 3, "private_plan_contents_read_by_demo": False,
            "private_fingerprints_exported": False, "snapshot_activated": False,
            "new_active_copy_created": False, "production_failover_performed": False,
            "source_write_fence_installed": False, "measured_capacity": False,
            "external_llm_used": False, "human_review_recorded": False,
            "proposed_code_executed": False, "process_crash_simulated": False,
        },
        "private_file_layout": {"source_workspace": "private/source", "snapshot_bundle": "private/backup-01",
                                "restored_workspace": "private/restore-01",
                                "plans": ["private/recovery-plan-01.json", "private/recovery-plan-02.json",
                                          "private/recovery-plan-03.json"],
                                "unrelated_source_fixture": "private/unrelated-source"},
        "limitations": [
            "Reconciliation is an advisory observation of one trusted live source and one quarantined bundle; it does not activate either copy or install a source write fence.",
            "Private full-row fingerprints catch eventless SQL changes, while time-based grant and lease validity still depends on the sampled clock.",
            "Successful revalidation describes rows and administrative authority at observation, not future authority or a consumable write permission.",
            "The local database owner, implementation and clock remain trusted; matching events and rows are not external origin or completeness attestations.",
            "No credentials, private plans, fingerprints, manifest, intent or SQLite bytes are exported. The demo checks file permissions without reading plan contents.",
            "The source performs ordinary enqueue, claim and heartbeat actions. Recovery assessment and revalidation use read-only connections; visible before/after observations are saved separately.",
            "Public observation comparisons omit only governance_status's top-level observed_at, freshly sampled on each read; raw timestamps remain in saved observations and addressed proof bodies are untouched. Computed grant expiry flags remain compared, so this is not a proof that all private SQL rows are unchanged.",
            "The unrelated database is a separately bootstrapped fictional fixture with fresh authority, not an activated historical snapshot or a production branch.",
            "All principals and author workers are local and programmed. The workflow establishes neither human understanding nor independent models nor service capacity.",
            "Old approvals, leases, operation IDs and receipts remain historical facts; no automatic authority or provenance transfer is performed.",
        ],
    }
    write_json("recovery-report.json", report)
    lines = [
        "# Выполненная сверка снимка с действующим источником", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала обычный [сценарий backup/restore](report.md) создаёт вымышленный документ и код,",
        f"принимает ревизию {restored_before['status']['revision']} и сохраняет частный карантинный снимок с {restored_before['status']['events']} событиями. Источник продолжает",
        f"работу до {source_baseline['status']['events']} событий. Существующие публичные результаты этого запуска сохранены без перезаписи.", "",
        "## Последовательность", "", "| Этап | Наблюдение | Событий источника |", "|---|---|---:|",
    ]
    lines += [f"| [{entry['label']}]({entry['artifact']}) | {entry['explanation']} | {entry['source_event_count']} |"
              for entry in stages]
    lines += [
        "", "## Полномочия и происхождение", "",
        "Assess проверяет готовый bundle и действующий источник с текущим административным credential.",
        "Исходный административный grant должен сохраниться в снимке. Старый префикс событий и строки",
        "неизменяемых таблиц сравниваются точно. Изменяемые таблицы получают безопасные сводки различий.",
        "Частный план содержит внутренние отпечатки строк; публичный результат показывает случайный plan ID,",
        "счётчики и различия. План не является разрешением записи, активации или внешним заверением истории.", "",
        "## Почему числа событий недостаточно", "",
        "В живом источнике создаётся новая авторская аренда. Первый assess и отдельный revalidate завершаются.",
        "Обычный heartbeat затем меняет lease_until: runtime revision, число событий и все их тела остаются",
        "прежними. Повторная проверка первого плана возвращает recovery_plan_stale. Новый план снова проходит",
        "проверку. Позднее обычное enqueue добавляет одно событие при той же ревизии и инвалидирует второй план.",
        "Третий план учитывает оба изменения. Эти действия выполняются без сырого изменения SQL и без аварии.", "",
        "## Сохранённые основания", "",
        "- [Три оценки](recovery-assessments.json), [три успешных повторных проверки](recovery-revalidations.json).",
        "- [Heartbeat до и после](recovery-heartbeat.json), [исходы CLI](recovery-cli-outcomes.json), [отказы](recovery-rejections.json).",
        "- [Наблюдения до и после read API](recovery-read-observations.json), [начальные состояния](recovery-baseline-observations.json).",
        "- [Конечные состояния](recovery-final-observations.json), [события](recovery-events.json), [задачи](recovery-tasks.json).",
        "- [Неизменившаяся историческая копия](recovery-snapshot-unchanged.json), [машинный отчёт](recovery-report.json).", "",
        "## Граница результата", "",
        f"Новых этапов: {len(stages)}; новых CLI-процессов: {len(cli_outcomes)}; планов: {len(assessments)}.",
        f"Событий копии: {restored_before['status']['events']}; событий источника в конце: {final_source['status']['events']}.",
        "Отозванный reviewer, карантинный источник, существующий файл плана и отдельный источник с иным",
        "административным grant получили ожидаемые отказы. Существующий план не был заменён.", "",
        "Копия остаётся inspection-only. Assess и revalidate не устанавливают запрет записи в живом источнике,",
        "не возвращают старые полномочия и не создают активный клон. После наблюдения источник может снова",
        "измениться; время действия grant или lease может истечь без изменения SQL. Полное переключение",
        "требует отдельного протокола fencing и актуальных полномочий.", "",
        "Сравнение публичных наблюдений исключает только верхнее поле governance.observed_at, которое",
        "заново вычисляется при каждом чтении. Исходные временные отметки сохранены; адресованные тела",
        "пакетов, решений и квитанций не изменяются. Вычисляемые признаки expired остаются в сравнении.",
        "Это сверка публичных наблюдений, без утверждения о побайтной неизменности всех частных SQL-строк.", "",
        "Private plans имеют права 0600, их каталог — 0700. Демонстратор не читает их содержимое и не",
        "публикует отпечатки, credentials, manifest или SQLite. Все роли локальные; внешних LLM, человеческой",
        "экспертизы и измерения предельной нагрузки нет. Предложенный код не исполняется.", "",
    ]
    markdown = "\n".join(lines)
    public(markdown)
    with (workspace / "recovery-report.md").open("x", encoding="utf-8") as stream:
        stream.write(markdown)
    return public(report)
