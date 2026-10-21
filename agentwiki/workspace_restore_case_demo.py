"""Record blocked observations and associate separately prepared attempts.

Cases preserve historical facts. New attempts are created by ordinary prepare
with an explicit source; linking them neither proves request equivalence nor
creates authority. Private control-record and staging contents are never read.
"""

from __future__ import annotations

from datetime import datetime
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from zoneinfo import ZoneInfo

from .governance_cli import read_credential
from .recovery_reconciliation_demo import FORBIDDEN_PUBLIC_FIELDS
from .store import Store, WorkbenchError, canonical
from .workspace_backup_cli import _public_summary
from .workspace_restore_cases_cli import _public_response
from .workspace_restore_job_cli import _public_status
from .workspace_restore_prepare_demo import _metadata, run_workspace_restore_prepare_demo


RULES_VERSION = "workspace-restore-case-1"


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError("Restore cases demonstration stopped: " + message)


def run_workspace_restore_case_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Keep three blocked jobs while linking three explicitly prepared attempts."""
    from . import workspace_backup

    workspace = Path(workspace).expanduser().resolve()
    baseline = run_workspace_restore_prepare_demo(workspace, workers)
    private = workspace / "private"
    source_bundle = private / "full-backup"
    repository = Path(__file__).resolve().parents[1]
    source = Store(source_bundle / "state.sqlite3")
    live = Store(private / "next-workspace" / "state.sqlite3")
    source_size = (source_bundle / "state.sqlite3").stat().st_size
    baseline_files = sorted(path.name for path in workspace.iterdir()
                            if path.is_file() and path.suffix in {".json", ".md"})
    markers: list[str] = []
    for path in private.glob("*.credential"):
        token = read_credential(path)
        markers.extend((token, hashlib.sha256(token.encode("ascii")).hexdigest()))
    forbidden = FORBIDDEN_PUBLIC_FIELDS | {
        "old_credential_hashes", "new_administrator_hash", "artifact_anchors", "destination_path",
        "history_path", "job_path", "source_bundle", "original_manifest", "restore_manifest",
        "plan", "owner", "staging_path", "sha256", "schema_fingerprint", "request_digest",
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

    stages: list[dict[str, Any]] = []
    cli_outcomes: list[dict[str, Any]] = []
    rejections: list[dict[str, Any]] = []
    discarded_reply_addresses: dict[str, str] = {}
    cases: list[dict[str, Any]] = []
    targets: dict[str, Any] = {}
    target_archives: dict[str, Any] = {}
    summaries: dict[str, Any] = {}
    reply_recovery: list[dict[str, Any]] = []

    def stage(label: str, explanation: str, observations: Any) -> None:
        artifact = f"restore-cases-stage-{len(stages) + 1:02d}-{label}.json"
        write_json(artifact, {"label": label, "explanation": explanation, "observations": observations})
        stages.append({"label": label, "explanation": explanation, "artifact": artifact})

    def cli(label: str, command: str, action: str, arguments: list[str], *,
            expected_error: str | None = None, discard: bool = False) -> dict[str, Any] | None:
        _require(command in {"workspace-restore", "workspace-restore-case"}, "unsupported ordinary command")
        with subprocess.Popen([sys.executable, "-m", "agentwiki", command, action, *arguments], cwd=repository,
                              shell=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=90)
            except subprocess.TimeoutExpired as error:
                process.kill()
                process.communicate(timeout=15)
                raise RuntimeError("Restore cases demonstration stopped: ordinary CLI timed out") from error
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
                value = public(_public_status(value) if command == "workspace-restore" else _public_response(value, action))
            except (TypeError, ValueError) as error:
                raise RuntimeError("Restore cases demonstration stopped: invalid public CLI response") from error
            if discard:
                # This address covers an already validated public response;
                # neither the address nor the discarded business body is exported.
                discarded_reply_addresses[label] = hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()
                return None
            return value

    captured, live_before = observe(source), observe(live)
    source_summary = public(_public_summary(workspace_backup.inspect_bundle(source_bundle), [], "inspect"))
    archives = {"oldest": observe(Store(source_bundle / "archives" / "history-0001.sqlite3")),
                "nearest": observe(Store(source_bundle / "archives" / "history.sqlite3"))}
    _require(len(baseline_files) == 269 and source_summary["archive_count"] == 2
             and captured["state"]["revision"] == 4 and captured["status"]["events"] == 36
             and live_before["state"]["revision"] == 5 and live_before["status"]["events"] == 59,
             "baseline is not the expected historical R4 and live R5 workspace")
    definitions = [
        ("plan_invalid", "01", "workspace_restore_job_manifest_invalid"),
        ("ownership_unproven", "03", "workspace_restore_job_conflict"),
        ("owner_invalid", "04", "workspace_restore_job_manifest_invalid"),
    ]
    original_metadata: dict[str, Any] = {}
    original_identities: dict[str, Any] = {}
    requests: list[dict[str, Any]] = []
    for index, (cause, old_index, error) in enumerate(definitions, 1):
        job, target = private / ("prepare-job-" + old_index), private / ("prepare-restore-" + old_index)
        topology, identity = _metadata(job, target, source_size)
        original_metadata[cause], original_identities[cause] = topology, identity
        requests.append({"cause": cause, "old_job": job, "old_target": target, "old_error": error,
                         "case_directory": private / f"restore-case-{index:02d}",
                         "case_operation": f"restore-cases-record-{index:02d}",
                         "attempt_job": private / f"case-attempt-job-{index:02d}",
                         "attempt_target": private / f"case-attempt-restore-{index:02d}",
                         "attempt_operation": f"restore-cases-new-attempt-{index:02d}",
                         "link_operation": f"restore-cases-link-{index:02d}"})

    def old_jobs_unchanged() -> bool:
        return all(_metadata(item["old_job"], item["old_target"], source_size)[1] == original_identities[item["cause"]]
                   for item in requests)

    stage("blocked-observations-before-registration",
          "Three blocked jobs remain historical evidence; a case is recorded separately and a new attempt will use an explicitly supplied source.",
          {"source_summary": source_summary, "original_topology": original_metadata,
           "live_status": live_before["status"]})
    for item in requests:
        args = ["--job", str(item["old_job"]), "--case", str(item["case_directory"]),
                "--operation-id", item["case_operation"]]
        record = cli(item["cause"] + "-record", "workspace-restore-case", "record", args)
        replay = cli(item["cause"] + "-record-replay", "workspace-restore-case", "record", args)
        shown = cli(item["cause"] + "-show-unlinked", "workspace-restore-case", "show",
                    ["--case", str(item["case_directory"])])
        _require(record == replay == shown["case"] and record["cause"] == item["cause"]
                 and record["original_error_code"] == item["old_error"]
                 and (record["original_identity"] is None) is (item["cause"] == "plan_invalid")
                 and shown["link"] is None and shown["original_evidence"]["state"] == "unchanged"
                 and shown["current_attempt"]["state"] == "not_linked" and old_jobs_unchanged(),
                 "case registration changed old evidence or reconstructed an unknown identity")
        item.update(record=record, unlinked_show=shown)
    for item in requests:
        prepared = cli(item["cause"] + "-ordinary-explicit-source-prepare", "workspace-restore", "prepare",
                       ["--bundle", str(source_bundle), "--destination", str(item["attempt_target"]),
                        "--job", str(item["attempt_job"]), "--operation-id", item["attempt_operation"]])
        _require(prepared["state"] == "prepared" and prepared["copied_file_count"] == 0
                 and prepared["total_file_count"] == 3, "ordinary prepare did not create a separate empty attempt")
        item["prepared"] = prepared

    # Bound refusals use fresh/existing case paths without corrupting private records.
    invalid_case = private / "restore-case-invalid-operation"
    cli("record-operation-length-bound", "workspace-restore-case", "record",
        ["--job", str(requests[0]["old_job"]), "--case", str(invalid_case), "--operation-id", "b" * 102],
        expected_error="workspace_restore_case_invalid_input")
    _require(not invalid_case.exists() and old_jobs_unchanged(), "invalid record input created a case or changed old evidence")
    cli("link-operation-length-bound", "workspace-restore-case", "link-attempt",
        ["--case", str(requests[0]["case_directory"]), "--job", str(requests[0]["attempt_job"]), "--operation-id", "b" * 102],
        expected_error="workspace_restore_case_invalid_input")

    for index, item in enumerate(requests):
        label = item["cause"] + "-discarded-link-response"
        link_args = ["--case", str(item["case_directory"]), "--job", str(item["attempt_job"]),
                     "--operation-id", item["link_operation"]]
        cli(label, "workspace-restore-case", "link-attempt", link_args, discard=True)
        linked = cli(item["cause"] + "-link-exact-replay", "workspace-restore-case", "link-attempt", link_args)
        recovered = (discarded_reply_addresses.pop(label) == hashlib.sha256(canonical(linked).encode("utf-8")).hexdigest())
        _require(recovered and linked["case_id"] == item["record"]["case_id"]
                 and linked["attempt_status"] == item["prepared"] and linked["operator_association"] is True
                 and linked["fresh_creation_verified"] is False and linked["request_equivalence_verified"] is False,
                 "link replay did not recover the fixed operator association")
        linked_show = cli(item["cause"] + "-show-linked", "workspace-restore-case", "show",
                          ["--case", str(item["case_directory"])])
        _require(linked_show["case"] == item["record"] and linked_show["link"] == linked
                 and linked_show["current_attempt"]["state"] == "available"
                 and linked_show["current_attempt"]["status"] == item["prepared"],
                 "case inspection merged historical link facts with another current status")
        other = requests[(index + 1) % len(requests)]
        for suffix, attempt_job, operation in (
            ("operation", item["attempt_job"], item["link_operation"] + "-competing"),
            ("job", other["attempt_job"], item["link_operation"]),
        ):
            cli(item["cause"] + "-conflicting-link-" + suffix, "workspace-restore-case", "link-attempt",
                ["--case", str(item["case_directory"]), "--job", str(attempt_job), "--operation-id", operation],
                expected_error="workspace_restore_case_conflict")
        _require(old_jobs_unchanged(), "linking or a refused link modified an original job")
        completed = cli(item["cause"] + "-ordinary-complete-attempt", "workspace-restore", "step",
                        ["--job", str(item["attempt_job"]), "--limit", "16"])
        status = cli(item["cause"] + "-completed-attempt-status", "workspace-restore", "status",
                     ["--job", str(item["attempt_job"])])
        completed_show = cli(item["cause"] + "-show-completed-attempt", "workspace-restore-case", "show",
                             ["--case", str(item["case_directory"])])
        historical_link = cli(item["cause"] + "-historical-link-replay-after-completion", "workspace-restore-case", "link-attempt", link_args)
        _require(completed == status and completed["completed"] is True and historical_link == linked
                 and completed_show["case"] == item["record"] and completed_show["link"] == linked
                 and completed_show["current_attempt"]["status"] == completed
                 and completed_show["original_evidence"]["state"] == "unchanged",
                 "completion changed historical records or failed to expose the separate current attempt")
        summary = public(_public_summary(workspace_backup.inspect_bundle(item["attempt_target"]), [], "inspect"))
        restored = Store(item["attempt_target"] / "state.sqlite3")
        observed = observe(restored)
        observed_archives = {"oldest": observe(Store(item["attempt_target"] / "archives" / "history-0001.sqlite3")),
                             "nearest": observe(Store(item["attempt_target"] / "archives" / "history.sqlite3"))}
        _require(matches(captured, observed) and canonical(summary) == canonical(completed["result"])
                 and all(matches(archives[key], value) for key, value in observed_archives.items()),
                 "new attempt does not preserve the explicitly supplied R4 history")
        for action in ("enqueue", "publish"):
            release = workspace / ("restore-cases-forbidden-release-" + str(index + 1))
            watched = observe(restored)
            try:
                if action == "enqueue":
                    restored.enqueue("restore-cases-forbidden-write", "code_author", {})
                else:
                    restored.publish(release)
            except WorkbenchError as error:
                _require(error.code == "store_snapshot_quarantined" and matches(watched, observe(restored))
                         and not release.exists(), "a refused quarantine operation changed the new attempt")
                rejections.append({"label": item["cause"] + "-quarantine-" + action, "action": action,
                                   "code": error.code, "rejected": True, "public_observations_unchanged": True})
            else:
                raise RuntimeError("Restore cases demonstration stopped: quarantine allowed a write")
        case = {"cause": item["cause"], "record": item["record"], "unlinked_show": item["unlinked_show"],
                "ordinary_prepared_attempt": item["prepared"], "historical_link": linked, "linked_show": linked_show,
                "completed_status": completed, "completed_show": completed_show,
                "historical_link_replay_after_completion": historical_link,
                "discarded_link_response_recovered_exactly": recovered,
                "old_job_metadata_unchanged": True, "attempt_prepared_by_case_feature": False,
                "source_explicitly_supplied_to_ordinary_prepare": True, "original_request_equivalence_verified": False,
                "new_attempt_quarantined": True}
        cases.append(case)
        targets[item["cause"]], target_archives[item["cause"]], summaries[item["cause"]] = observed, observed_archives, summary
        reply_recovery.append({"cause": item["cause"], "actual_public_response_discarded": True,
                               "exact_replay_equal": recovered, "comparison_digest_exported": False})
        item.update(completed=completed, completed_show=completed_show, completed_summary=summary)
        stage(item["cause"].replace("_", "-"),
              "The immutable blocked observation is linked to a separately prepared attempt; replay preserves the historical receipt while show reports its completed current status.", case)

    for item in requests:
        cli(item["cause"] + "-original-job-still-blocked", "workspace-restore", "status",
            ["--job", str(item["old_job"])], expected_error=item["old_error"])
    _require(old_jobs_unchanged(), "original blocked evidence changed after all attempts completed")
    detached = private / "restore-cases-source-detached"
    moved = False
    offline: list[dict[str, Any]] = []
    try:
        source_bundle.rename(detached)
        moved = True
        for item in requests:
            shown = cli(item["cause"] + "-show-without-source", "workspace-restore-case", "show",
                        ["--case", str(item["case_directory"])])
            status = cli(item["cause"] + "-status-without-source", "workspace-restore", "status", ["--job", str(item["attempt_job"])])
            summary = public(_public_summary(workspace_backup.inspect_bundle(item["attempt_target"]), [], "inspect"))
            _require(shown == item["completed_show"] and status == item["completed"] and summary == item["completed_summary"]
                     and matches(targets[item["cause"]], observe(Store(item["attempt_target"] / "state.sqlite3"))),
                     "completed observation depends on the unavailable source bundle")
            offline.append({"cause": item["cause"], "case_show_equal": True, "attempt_status_equal": True,
                            "full_summary_equal": True, "public_Store_observations_equal": True})
    finally:
        if moved:
            detached.rename(source_bundle)
    source_final, live_final = observe(source), observe(live)
    _require(old_jobs_unchanged() and matches(captured, source_final) and matches(live_before, live_final)
             and all(matches(archives[key], observe(Store(source_bundle / "archives" / name)))
                     for key, name in (("oldest", "history-0001.sqlite3"), ("nearest", "history.sqlite3"))),
             "case registration or new attempts changed old evidence, source history or the live workspace")
    stage("historical-evidence-and-completed-reads-preserved",
          "The original three jobs still refuse; completed case/attempt observations work with the source bundle unavailable and all original paths are restored.",
          {"original_jobs_still_blocked": True, "original_metadata_unchanged": True, "source_unchanged": True,
           "live_unchanged": True, "source_independent_reads": offline, "source_bundle_restored": True})
    artifacts = {
        "restore-cases-cases.json": cases,
        "restore-cases-cli-outcomes.json": cli_outcomes,
        "restore-cases-rejections.json": rejections,
        "restore-cases-reply-recovery.json": reply_recovery,
        "restore-cases-source-summary.json": source_summary,
        "restore-cases-source-observations.json": {"before": captured, "after": source_final},
        "restore-cases-live-observations.json": {"before": live_before, "after": live_final},
        "restore-cases-target-observations.json": targets,
        "restore-cases-archive-observations.json": {"source": archives, "targets": target_archives},
        "restore-cases-original-observations.json": {"topology": original_metadata, "unchanged": True, "still_blocked": True},
        "restore-cases-completed-summaries.json": summaries,
    }
    for name, value in artifacts.items():
        write_json(name, value)
    report = {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(), "timezone": "Europe/Berlin",
        "scenario": "Immutable blocked observations linked to explicitly and separately prepared attempts",
        "summary": {
            "baseline_public_file_count": len(baseline_files), "restore_cases_stage_count": len(stages),
            "blocked_case_count": len(cases), "separately_prepared_attempt_count": len(targets),
            "restore_cases_cli_child_process_count": len(cli_outcomes),
            "total_cli_child_process_count": baseline["summary"]["total_cli_child_process_count"] + len(cli_outcomes),
            "total_cli_and_interrupt_worker_process_count": baseline["summary"]["total_cli_and_interrupt_worker_process_count"] + len(cli_outcomes),
            "new_interrupt_worker_process_count": 0, "operator_rejection_count": len(rejections),
            "discarded_link_response_count": len(reply_recovery), "discarded_link_responses_recovered_exactly": True,
            "original_jobs_still_blocked": True, "original_files_modified": False,
            "original_identity_reconstructed": False, "attempts_created_by_case_feature": False,
            "request_equivalence_verified": False, "operator_association": True,
            "captured_revision": captured["state"]["revision"], "captured_event_count": captured["status"]["events"],
            "live_revision": live_final["state"]["revision"], "live_event_count": live_final["status"]["events"],
            "complete_archive_count": source_summary["archive_count"], "restored_workspace_quarantined": True,
            "completed_reads_without_source": True, "all_original_paths_restored": True,
            "private_control_contents_read_by_demo": False, "private_stage_contents_read_by_demo": False,
            "private_fingerprints_exported": False, "old_authority_activated": False,
            "external_fencing_established": False, "external_llm_used": False,
            "human_review_recorded": False, "proposed_code_executed": False, "measured_capacity": False,
        },
        "stages": stages, "artifacts": sorted(artifacts),
        "limits": [
            "Cases record three blocked observations outside the old jobs. Case and link receipts are historical; show reports current evidence and current attempt status separately.",
            "Each new attempt is created by ordinary prepare with an explicitly supplied source bundle and separate operation/job/target. Linking only records an operator association; fresh creation and original request equivalence are not verified by that feature.",
            "An invalid original plan does not yield a reconstructed identity or inferred source. Known identity from a valid plan remains historical and does not grant authority.",
            "Actual validated public link responses are parsed and discarded; exact replay is compared using ephemeral addresses of those public responses. No comparison address or private fingerprint is published.",
            "The three original jobs remain blocked and unchanged under narrow lstat observation. The driver never reads private plan, owner, manifest, case-control, staging or checksum contents, and performs no manual repair.",
            "Operation-length bounds and competing operation/job links are refused. New case-control partial writes, process interruption during registration/linking, changed evidence and unavailable original-job cases are not injected in this demonstration.",
            "Completed reads temporarily rename only the source bundle and restore it in finally. This does not model hardware, kernel, storage or power-loss durability, all schedules or production capacity.",
            "Public observation equality excludes only the top-level governance observed_at wrapper, preserving expired flags and addressed proof bodies. Restored history stays quarantined; old credentials and leases are inactive.",
            "Programmed roles and local checksums do not establish human independence, factual truth, external fencing or measured production capacity.",
        ],
    }
    write_json("restore-cases-report.json", report)
    lines = [
        "# Учёт блокирующих наблюдений и связь с отдельными попытками", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Технические отметки используют UTC/Unix epoch.", "",
        "Сначала заново выполнен [сценарий подготовки](restore-prepare-report.md); его файлы сохранены.",
        "Три блокирующие операции зарегистрированы отдельно. Их plan, owner и назначения не исправляются.",
        "Три новые попытки создаются обычным prepare с явно переданным R4 bundle и новыми именами.", "",
        "## Этапы", "", "| Этап | Наблюдение |", "|---|---|",
    ]
    for value in stages:
        lines.append(f"| [{value['label']}]({value['artifact']}) | {value['explanation']} |")
    lines += [
        "", "## Что означает связь", "",
        "Case сохраняет историческое наблюдение и безопасную причину отказа. При недействительном",
        "plan исходная идентичность остаётся неизвестной. Link фиксирует операторскую ассоциацию",
        "с уже существующей новой попыткой. Эта функция не создаёт попытку, не устанавливает",
        "эквивалентность исходного запроса и не восстанавливает прежние полномочия.", "",
        "Настоящий ответ link проверен и отброшен, затем точный replay восстанавливает ту же квитанцию.",
        "После завершения новой попытки исторический link остаётся прежним; show отдельно показывает",
        "её текущий completed status. Конкурирующие job и operation ID отказывают.", "",
        "Все новые назначения сохраняют R4, 36 событий и два архива. Карантин блокирует запись и",
        "публикацию. Прежние три jobs остаются неизменными и блокирующими. Completed show/status,",
        "полный inspect и Store читаются при временно недоступном source bundle, возвращённом в finally.", "",
        "## Публичные основания", "",
        "- [Cases и ссылки](restore-cases-cases.json), [CLI](restore-cases-cli-outcomes.json), [отказы](restore-cases-rejections.json), [восстановление ответа](restore-cases-reply-recovery.json).",
        "- [Исходная сводка](restore-cases-source-summary.json), [источник](restore-cases-source-observations.json), [живая база](restore-cases-live-observations.json), [старые jobs](restore-cases-original-observations.json).",
        "- [Новые назначения](restore-cases-target-observations.json), [архивы](restore-cases-archive-observations.json), [полные сводки](restore-cases-completed-summaries.json), [машинный отчёт](restore-cases-report.json).", "",
        "## Границы результата", "",
        f"Этапов: {len(stages)}; cases: {len(cases)}; новых обычных CLI: {len(cli_outcomes)}; отказов: {len(rejections)}.",
        "Новых worker-прерываний нет. Частичные записи case/link, изменённые или недоступные старые",
        "jobs, аппаратные отказы, все расписания и предельная нагрузка не воспроизводились.",
        "Частные контрольные файлы и staging не разбираются; публикуются только проверенные API/Store",
        "ответы и ограниченные булевы наблюдения. Сравнение исключает лишь верхний governance.observed_at.",
        "Адресованные доказательства, timestamps и expired-признаки сохраняются. Внешние LLM,",
        "независимая экспертиза, истинность содержания и внешний fencing не установлены.", "",
    ]
    with (workspace / "restore-cases-report.md").open("x", encoding="utf-8") as stream:
        stream.write("\n".join(public(lines)))
    return report
