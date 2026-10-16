"""A local editorial workflow with programmed agents and an immutable journal.

This product example records decisions about one fixed input. It does not claim
expert review, call a model, execute a proposal, or accept a Store change.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import datetime
import json
from pathlib import Path
from threading import Barrier, current_thread
from typing import Any
from zoneinfo import ZoneInfo

from .corpus import get_case
from .editorial import EditorialJournal
from .store import WorkbenchError


DATABASE_NAME = "editorial.sqlite3"


def _expect(assessment: dict[str, Any], freshness: str, review_state: str) -> None:
    """Stop an incomplete scenario instead of reporting an unobserved outcome."""
    if assessment["freshness"] != freshness or assessment["review_state"] != review_state:
        raise RuntimeError("Editorial scenario stopped at an unexpected state: "
                           + json.dumps(assessment, ensure_ascii=False))
    if assessment["auto_accept"] or assessment["human_review_recorded"]:
        raise RuntimeError("Programmed reviewers cannot stand in for a human editor")


def run_editorial_demo(workspace: str | Path, workers: int = 3) -> dict[str, Any]:
    """Produce a journal and reports in a new, otherwise empty workspace."""
    workspace = Path(workspace).expanduser().resolve()
    if type(workers) is not int or not 1 <= workers <= 32:
        raise ValueError("Workers must be an integer between 1 and 32")
    if workspace.exists() and (not workspace.is_dir() or any(workspace.iterdir())):
        raise ValueError("Editorial demo needs a new empty workspace; existing data is preserved")
    workspace.mkdir(parents=True, exist_ok=True)

    case = get_case("units-equivalent-seconds-milliseconds")
    pair = {key: deepcopy(case[key]) for key in ("before", "after", "sources")}
    journal = EditorialJournal(workspace / DATABASE_NAME)
    packet = journal.create_packet(pair, proposer="proposal-agent", proposer_kind="agent")
    packet_id = packet["packet_id"]
    packet_digest = packet["packet_digest"]
    initial = journal.assess(packet_id, current_pair=pair)
    _expect(initial, "current", "awaiting_reviews")

    # Two submitted jobs share the fixed packet. With workers >= 2 the barrier
    # lets both worker threads start before either writes its decision.
    ready = Barrier(min(workers, 2))

    def reviewer_job(reviewer: str, operation_id: str) -> dict[str, Any]:
        thread_name = current_thread().name
        ready.wait(timeout=30)
        decision = journal.record_decision(
            packet_id,
            reviewer=reviewer,
            actor_kind="agent",
            verdict="approve",
            rationale=("The declared values 30 seconds and 30000 milliseconds agree after exact "
                       "conversion. This programmed decision does not establish source truth or "
                       "the adequacy of the wording; human editorial review remains necessary."),
            operation_id=operation_id,
            expected_packet_digest=packet_digest,
            expected_previous_decision=None,
        )
        return {"reviewer": reviewer, "thread": thread_name, "decision": decision}

    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="editorial-review") as executor:
        futures = [
            executor.submit(reviewer_job, "review-agent-a", "review-a-1"),
            executor.submit(reviewer_job, "review-agent-b", "review-b-1"),
        ]
        parallel_reviews = [future.result() for future in futures]
    agreement = journal.assess(packet_id, current_pair=pair)
    _expect(agreement, "current", "agent_agreement")

    objection = journal.record_decision(
        packet_id,
        reviewer="review-agent-c",
        actor_kind="agent",
        verdict="request_changes",
        rationale=("The equal quantities do not show whether the supplied wording and source "
                   "support the intended claim. Keep that question visible for a human editor."),
        operation_id="review-c-1",
        expected_packet_digest=packet_digest,
        expected_previous_decision=None,
    )
    disagreement = journal.assess(packet_id, current_pair=pair)
    _expect(disagreement, "current", "disagreement")

    revision_arguments = {
        "reviewer": "review-agent-c",
        "actor_kind": "agent",
        "verdict": "approve",
        "rationale": ("The limited packet explicitly compares declared structured fields and "
                      "keeps human review mandatory. I support that limited comparison; the "
                      "source and full wording still need human assessment."),
        "operation_id": "review-c-2",
        "expected_packet_digest": packet_digest,
        "expected_previous_decision": objection["decision_id"],
    }
    revised_decision = journal.record_decision(packet_id, **revision_arguments)
    revised_agreement = journal.assess(packet_id, current_pair=pair)
    _expect(revised_agreement, "current", "agent_agreement")

    history_before_replay = journal.history(packet_id)
    replay = journal.record_decision(packet_id, **revision_arguments)
    history_after_replay = journal.history(packet_id)
    if not replay["replayed"] or len(history_after_replay) != len(history_before_replay):
        raise RuntimeError("An identical operation did not replay its existing decision")

    stale_revision_arguments = dict(revision_arguments)
    stale_revision_arguments["operation_id"] = "review-c-stale-attempt"
    stale_revision_arguments["rationale"] = "This edit is based on the reviewer's older decision."
    try:
        journal.record_decision(packet_id, **stale_revision_arguments)
    except WorkbenchError as error:
        if error.code != "decision_conflict":
            raise
        conflict = {"code": error.code, "message": str(error)}
    else:
        raise RuntimeError("A decision based on an obsolete predecessor unexpectedly replaced the latest decision")

    changed_pair = deepcopy(pair)
    changed_pair["after"]["text"] += " Дополнение для редакционного рассмотрения."
    old_packet_after_change = journal.assess(packet_id, current_pair=changed_pair)
    _expect(old_packet_after_change, "stale_input", "agent_agreement")
    replacement = journal.create_packet(
        changed_pair, proposer="proposal-agent", proposer_kind="agent"
    )
    replacement_assessment = journal.assess(replacement["packet_id"], current_pair=changed_pair)
    _expect(replacement_assessment, "current", "awaiting_reviews")
    old_packet = journal.get_packet(packet_id)
    old_history = journal.history(packet_id)
    new_history = journal.history(replacement["packet_id"])
    if old_packet["packet_digest"] != packet_digest or old_history != history_after_replay or new_history:
        raise RuntimeError("A new packet unexpectedly changed the earlier packet's decision history")

    snapshot = journal.export_snapshot()
    stages = [
        {"name": "initial", "assessment": initial},
        {"name": "two_agent_approvals", "assessment": agreement},
        {"name": "third_agent_objection", "assessment": disagreement},
        {"name": "third_agent_revised_decision", "assessment": revised_agreement},
        {"name": "old_packet_with_changed_input", "assessment": old_packet_after_change},
        {"name": "replacement_packet", "assessment": replacement_assessment},
    ]
    report = {
        "schema_version": 1,
        "created_at": datetime.now(ZoneInfo("Europe/Berlin")).isoformat(),
        "report_timezone": "Europe/Berlin",
        "workspace": str(workspace),
        "case_id": case["case_id"],
        "uses_llm": False,
        "fictional_sources": True,
        "executes_proposed_code": False,
        "calls_store_accept": False,
        "requires_human_review": True,
        "human_review_recorded": False,
        "auto_accept": False,
        "execution": {
            "requested_pool_workers": workers,
            "submitted_parallel_review_jobs": len(parallel_reviews),
            "observed_worker_threads": sorted({item["thread"] for item in parallel_reviews}),
            "distinct_reviewer_ids": ["review-agent-a", "review-agent-b", "review-agent-c"],
            "all_reviewer_actor_kinds": ["agent"],
            "proposer_id": "proposal-agent",
            "proposer_actor_kind": "agent",
        },
        "packets": {"original": packet, "replacement": replacement},
        "parallel_reviews": parallel_reviews,
        "objection": objection,
        "revised_decision": revised_decision,
        "replay": {
            "response": replay,
            "history_count_before": len(history_before_replay),
            "history_count_after": len(history_after_replay),
        },
        "obsolete_predecessor": conflict,
        "stages": stages,
        "original_packet_history": old_history,
        "replacement_packet_history": new_history,
        "summary": {
            "packet_count": 2,
            "original_packet_decision_count": len(old_history),
            "replacement_packet_decision_count": len(new_history),
            "journal_event_count": len(snapshot["events"]),
            "replayed_operation_added_decision": False,
            "obsolete_predecessor_rejected": True,
            "changed_input_requires_new_reviews": True,
            "human_review_recorded": False,
            "auto_accept": False,
        },
        "files": {
            "journal": "journal.json",
            "report": "report.json",
            "readable_report": "report.md",
            "database": DATABASE_NAME,
        },
        "limitations": [
            "Programmed reviewers share a scripted rationale; agreement is not independent evidence.",
            "Actor identities and actor kinds are local labels, not authenticated human identities.",
            "Exact input digests detect a wording change; they do not interpret its meaning.",
            "The journal is separate from Store acceptance and does not publish a knowledge release.",
            "The event hash chain shows local record consistency, not external attestation.",
            "This is one editorial scenario, not a quality evaluation or load measurement.",
        ],
    }
    for name, value in (("journal.json", snapshot), ("report.json", report)):
        (workspace / name).write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )

    lines = [
        "# Выполненный пример редакционного согласования", "",
        "Все участники — программные агенты с заранее заданными действиями. Внешние LLM не вызываются.",
        "Источники вымышлены. Решение человека не записывалось; автоматического принятия нет.", "",
        f"Время отчёта: {report['created_at']} (Europe/Berlin). Время событий журнала хранится в UTC.", "",
        "## Участники и исполнение", "",
        "Автор proposal-agent создаёт пакет; review-agent-a и review-agent-b отправляют решения через пул потоков.",
        "review-agent-c затем добавляет замечание и заменяет собственное решение новым. Это три редакционных идентификатора, а не три независимых модели.",
        f"Размер пула: {workers}; первых заданий ревью: {len(parallel_reviews)}; наблюдавшихся потоков: {len(report['execution']['observed_worker_threads'])}.", "",
        "## Наблюдавшиеся переходы", "",
        "| Этап | Актуальность основания | Состояние ревью | Решение человека |",
        "|---|---|---|---|",
    ]
    labels = {
        "initial": "Новый пакет",
        "two_agent_approvals": "Два агента поддержали пакет",
        "third_agent_objection": "Третий агент запросил изменения",
        "third_agent_revised_decision": "Третий агент заменил своё решение",
        "old_packet_with_changed_input": "Изменён текст входной пары",
        "replacement_packet": "Создан новый пакет",
    }
    for stage in stages:
        state = stage["assessment"]
        lines.append(f"| {labels[stage['name']]} | {state['freshness']} | {state['review_state']} | не записано |")
    lines.extend([
        "", "agent_agreement означает согласие программных агентов по зафиксированному пакету. Во всех этапах requires_human_review=true и auto_accept=false.", "",
        "## История и повторная отправка", "",
        f"Повтор операции review-c-2 вернул replayed={str(replay['replayed']).lower()}; решений до и после: {len(history_before_replay)} и {len(history_after_replay)}.",
        f"Попытка заменить решение, указав уже устаревшего предшественника, отклонена с {conflict['code']}.",
        "Предыдущее решение не стирается: новое ссылается на него через supersedes.", "",
        "## Изменение содержания после ревью", "",
        "К after.text добавлено «Дополнение для редакционного рассмотрения.». Структурированные числа, единицы и источники остались прежними.",
        "Точный входной снимок изменился: прежний пакет имеет stale_input. Это обнаружение изменения байтов основания, а не понимание смысла добавленного предложения.",
        f"Новый пакет начинает работу без перенесённых голосов: решений {len(new_history)}, состояние awaiting_reviews.",
        f"История прежнего пакета сохранена: {len(old_history)} решения.", "",
        "## Идентификаторы и данные", "",
        f"- Исходный пакет: `{packet_id}`.",
        f"- Новый пакет: `{replacement['packet_id']}`.",
        f"- Событий журнала: {len(snapshot['events'])}.",
        "- [Полный отчёт и ответы операций](report.json).",
        "- [Экспорт пакетов, решений и цепочки событий](journal.json).", "",
        "Локальная SQLite-база остаётся в workspace. Абсолютный путь в JSON относится к этому запуску.",
        "Подписи акторов не аутентифицированы. Хеши связывают локальные записи, но не подтверждают истинность источника, независимость проверяющих или личность редактора.",
        "Этот журнал сам по себе не принимает изменения в Store и не выпускает документы или код.", "",
    ])
    (workspace / "report.md").write_text("\n".join(lines), encoding="utf-8")
    return report
