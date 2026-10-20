"""Commands for bounded, resumable advisory review-history inspection.

The target is frozen when an audit starts. An audit or checkpoint does not
grant acceptance authority or replace the live acceptance checks.
"""

from __future__ import annotations

import argparse
import sqlite3
from typing import Any

from .governance_cli import (
    _credential_argument,
    _existing_database,
    _positive_generation,
    _redact_text,
    _redact_value,
    _workspace_argument,
    read_credential,
)
from .store import Store, WorkbenchError


def _edge_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите целое число переходов от 1 до 64.") from error
    if not 1 <= limit <= 64:
        raise argparse.ArgumentTypeError("За один шаг можно рассмотреть от 1 до 64 переходов.")
    return limit


def _nonnegative_sequence(value: str) -> int:
    try:
        sequence = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите неотрицательный целый номер последовательности.") from error
    if sequence < 0:
        raise argparse.ArgumentTypeError("Номер последовательности должен быть неотрицательным.")
    return sequence


def _catalogue_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите целое число записей от 1 до 20.") from error
    if not 1 <= limit <= 20:
        raise argparse.ArgumentTypeError("Страница каталога может содержать от 1 до 20 записей.")
    return limit


def _authenticated_workspace(parser: argparse.ArgumentParser) -> None:
    _workspace_argument(parser)
    _credential_argument(parser)


def register_history_audit_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register advisory audit operations against an existing Store."""
    parser = subparsers.add_parser(
        "history-audit", help="Пошагово рассмотреть историю решения без права принятия.",
        description=(
            "Совещательный аудит истории continuity с замороженной целью. "
            "Каждый advance рассматривает не более 64 переходов. "
            "Start, advance и recover требуют действующего административного допуска. "
            "Recover читает сохранённый результат операции без создания нового шага. "
            "List фиксирует состав каталога по snapshot_max_seq; головы аудитов "
            "наблюдаются на каждой странице отдельно. "
            "Пакет аудита и checkpoints не предоставляют полномочий принятия "
            "и не заменяют актуальные проверки Store.accept."
        ),
    )
    commands = parser.add_subparsers(dest="history_audit_command", required=True)

    start = commands.add_parser("start", help="Зафиксировать цель и начать совещательный аудит решения.")
    _authenticated_workspace(start)
    start.add_argument("--decision", required=True, help="ID исходного решения gvd_.")
    start.add_argument("--expected-policy-generation", required=True, type=_positive_generation)
    start.add_argument(
        "--expected-target-packet-digest", required=True,
        help="Точный digest целевого пакета содержания, который будет заморожен в аудите.",
    )
    start.add_argument("--operation", required=True, help="ID операции для точного повтора запроса.")

    advance = commands.add_parser("advance", help="Рассмотреть следующий ограниченный шаг истории.")
    _authenticated_workspace(advance)
    advance.add_argument("--audit", required=True, help="ID начатого аудита rha_.")
    advance.add_argument(
        "--expected-checkpoint", required=True,
        help="Ожидаемый текущий checkpoint rhs_; защищает от конкурентного продвижения.",
    )
    advance.add_argument("--limit", type=_edge_limit, default=64, help="Число переходов за шаг, 1–64; по умолчанию 64.")
    advance.add_argument("--operation", required=True, help="ID операции для точного повтора шага.")

    show = commands.add_parser("show", help="Прочитать аудит или его исторический checkpoint без изменения состояния.")
    _workspace_argument(show)
    show.add_argument("--audit", required=True, help="ID аудита rha_.")
    show.add_argument("--checkpoint", help="ID исторического checkpoint rhs_; без него читается текущий.")

    recover = commands.add_parser("recover", help="Восстановить сохранённый результат известной операции.")
    _authenticated_workspace(recover)
    recover.add_argument("--operation", required=True, help="ID ранее выполненной операции start или advance.")

    catalogue = commands.add_parser(
        "list", help="Прочитать публичную страницу каталога аудитов с фиксированным составом.",
        description=(
            "Состав каталога ограничен snapshot_max_seq, головы аудитов наблюдаются "
            "на каждой странице. Для продолжения передайте оба поля next_cursor: "
            "after_seq и snapshot_max_seq. При after_seq > 0 верхняя граница обязательна."
        ),
    )
    _workspace_argument(catalogue)
    catalogue.add_argument(
        "--after-seq", type=_nonnegative_sequence, default=0,
        help="Последний прочитанный номер из next_cursor.after_seq; по умолчанию 0.",
    )
    catalogue.add_argument(
        "--snapshot-max-seq", type=_nonnegative_sequence,
        help="Граница состава из next_cursor.snapshot_max_seq; обязательна при продолжении.",
    )
    catalogue.add_argument(
        "--limit", type=_catalogue_limit, default=10,
        help="Число записей на странице, 1–20; по умолчанию 10.",
    )


def run_history_audit_command(args: argparse.Namespace) -> Any:
    """Run one advisory operation while preserving public proof keys."""
    action = args.history_audit_command
    if action not in {"start", "advance", "show", "recover", "list"}:
        raise WorkbenchError("unknown_history_audit_command", "Unknown history audit command")
    secrets: list[str] = []
    try:
        credential = None
        if action in {"start", "advance", "recover"}:
            credential = read_credential(args.credential)
            secrets.append(credential)
        database = _existing_database(args.workspace)
        store = Store(database)
        if action == "start":
            result = store.continuity_audit_start(
                args.decision, credential=credential,
                expected_policy_generation=args.expected_policy_generation,
                expected_target_packet_digest=args.expected_target_packet_digest,
                operation_id=args.operation,
            )
        elif action == "advance":
            result = store.continuity_audit_advance(
                args.audit, credential=credential,
                expected_checkpoint_id=args.expected_checkpoint,
                limit=args.limit, operation_id=args.operation,
            )
        elif action == "show":
            result = store.continuity_audit_show(args.audit, checkpoint_id=args.checkpoint)
        elif action == "recover":
            result = store.continuity_audit_recover(args.operation, credential=credential)
        else:
            result = store.continuity_audit_catalogue(
                after_seq=args.after_seq,
                snapshot_max_seq=args.snapshot_max_seq,
                limit=args.limit,
            )
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, sqlite3.Error) as error:
        raise WorkbenchError("history_audit_command_failed", _redact_text(str(error), secrets)) from error
    return _redact_value(result, secrets)
