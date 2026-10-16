"""Command helpers for the advisory editorial journal.

Usage from the main command line::

    python3 -m agentwiki editorial create --db /tmp/editorial.sqlite \
        --input pair.json --proposer-id author-01
    python3 -m agentwiki editorial show --db /tmp/editorial.sqlite --packet PACKET_ID
    python3 -m agentwiki editorial assess --db /tmp/editorial.sqlite \
        --packet PACKET_ID --current-input pair.json

Actor IDs and actor kinds are local declarations, not authenticated identities.
Recording a decision never accepts a workbench proposal or executes its code.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .editorial import EditorialJournal
from .semantic import load_pair_file
from .store import WorkbenchError


def register_editorial_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register the editorial command family on the existing root parser."""
    editorial = subparsers.add_parser(
        "editorial",
        help="Сохранять пакеты и редакционные решения без автоматического принятия.",
        description=(
            "Локальный журнал рассмотрения структурированных утверждений. "
            "Тип участника заявляется вызывающей стороной и не удостоверяет личность. "
            "Решение не выполняет принятие в Store и не запускает предложенный код."
        ),
    )
    commands = editorial.add_subparsers(dest="editorial_command", required=True)

    create = commands.add_parser("create", help="Сохранить неизменяемый пакет из JSON-пары.")
    create.add_argument("--db", required=True, type=Path, help="SQLite-файл редакционного журнала.")
    create.add_argument("--input", required=True, type=Path, help="JSON с before, after и sources.")
    create.add_argument("--proposer-id", required=True, help="Заявленный локальный ID автора.")
    create.add_argument(
        "--proposer-kind", choices=["agent", "human"], default="agent",
        help="Заявленный тип автора; по умолчанию agent.",
    )

    for name, description in [
        ("show", "Показать сохранённый пакет."),
        ("history", "Показать пакет и историю решений."),
    ]:
        command = commands.add_parser(name, help=description)
        command.add_argument("--db", required=True, type=Path, help="Существующий журнал.")
        command.add_argument("--packet", required=True, help="ID пакета.")

    record = commands.add_parser("record", help="Записать заявленное редакционное решение.")
    record.add_argument("--db", required=True, type=Path, help="Существующий журнал.")
    record.add_argument("--packet", required=True, help="ID рассматриваемого пакета.")
    record.add_argument("--packet-digest", required=True, help="Ожидаемый digest точного пакета.")
    record.add_argument("--reviewer-id", required=True, help="Заявленный локальный ID проверяющего.")
    record.add_argument(
        "--actor-kind", choices=["agent", "human"], required=True,
        help="Обязательный заявленный тип проверяющего; не проверка личности.",
    )
    record.add_argument("--verdict", choices=["approve", "request_changes", "abstain"], required=True)
    record.add_argument("--rationale", required=True, help="Содержательное основание решения.")
    record.add_argument("--operation-id", required=True, help="ID операции для безопасного повтора записи.")
    record.add_argument("--supersedes", help="Ожидаемый ID предыдущего решения этого проверяющего.")

    assess = commands.add_parser("assess", help="Оценить решения и свежесть пакета без принятия.")
    assess.add_argument("--db", required=True, type=Path, help="Существующий журнал.")
    assess.add_argument("--packet", required=True, help="ID пакета.")
    assess.add_argument(
        "--current-input", required=True, type=Path,
        help="Текущая JSON-пара для явной проверки свежести; читается один раз.",
    )

    export = commands.add_parser("export", help="Экспортировать снимок журнала с цепочкой хешей.")
    export.add_argument("--db", required=True, type=Path, help="Существующий журнал.")
    export.add_argument(
        "--output", required=True, type=Path,
        help="Новый JSON-файл; существующий файл никогда не перезаписывается.",
    )


def run_editorial_command(args: argparse.Namespace) -> dict[str, Any]:
    """Execute a registered editorial action and return its JSON-ready result."""
    action = args.editorial_command
    if action not in {"create", "show", "history", "record", "assess", "export"}:
        raise WorkbenchError("unknown_editorial_command", f"Unknown editorial command: {action}")

    # Read once before opening or creating a journal, keeping the captured input.
    pair = None
    if action == "create":
        pair = load_pair_file(args.input.expanduser())
    elif action == "assess":
        pair = load_pair_file(args.current_input.expanduser())
    journal = EditorialJournal(args.db.expanduser(), create=(action == "create"))

    if action == "create":
        return journal.create_packet(pair, proposer=args.proposer_id, proposer_kind=args.proposer_kind)
    if action == "show":
        return journal.get_packet(args.packet)
    if action == "history":
        return {"packet": journal.get_packet(args.packet), "history": journal.history(args.packet)}
    if action == "record":
        return journal.record_decision(
            packet_id=args.packet,
            reviewer=args.reviewer_id,
            actor_kind=args.actor_kind,
            verdict=args.verdict,
            rationale=args.rationale,
            operation_id=args.operation_id,
            expected_packet_digest=args.packet_digest,
            expected_previous_decision=args.supersedes,
        )
    if action == "assess":
        return journal.assess(args.packet, current_pair=pair)

    snapshot = journal.export_snapshot()
    content = json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    output = args.output.expanduser()
    with output.open("x", encoding="utf-8") as stream:
        stream.write(content)
    return {
        "path": str(output),
        "export_digest": snapshot["export_digest"],
        "packets": len(snapshot["packets"]),
        "decisions": len(snapshot["decisions"]),
    }
