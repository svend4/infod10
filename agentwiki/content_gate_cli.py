"""Commands for the opt-in transactional content gate on an existing Store.

Administrator credentials come only from private POSIX files. Status is public;
its packet digest can pin a subsequent authenticated governance decision.
"""

from __future__ import annotations

import argparse
from pathlib import Path
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
from .obligations import _read_json
from .store import Store, WorkbenchError


def _policy_arguments(parser: argparse.ArgumentParser) -> None:
    _workspace_argument(parser)
    _credential_argument(parser)
    parser.add_argument(
        "--policy", required=True, type=Path,
        help="Ограниченный строгий JSON: политика одного комплекта schema 1 или каталог разделов schema 2.",
    )
    parser.add_argument("--expected-policy-generation", required=True, type=_positive_generation)


def register_content_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register content policy administration and public packet inspection."""
    parser = subparsers.add_parser(
        "content", help="Включить обязательное рассмотрение заявленных полей содержания.",
        description=(
            "Отдельно включаемый режим после governance и authorship. "
            "Политика и действующие основания определяют точный пакет для решений. "
            "Совпадение заявленных полей не доказывает истинность свободного текста. "
            "Credential администратора читается из закрытого POSIX-файла."
        ),
    )
    commands = parser.add_subparsers(dest="content_command", required=True)

    enable = commands.add_parser("enable", help="Однократно включить content gate и повысить поколение governance-политики.")
    _policy_arguments(enable)

    policy = commands.add_parser("policy", help="Изменить политику содержания с проверкой текущего поколения governance.")
    _policy_arguments(policy)

    status = commands.add_parser("status", help="Показать публичную политику и точный пакет предложения без credential.")
    _workspace_argument(status)
    status.add_argument("--proposal", help="ID предложения для вычисления текущего пакета рассмотрения.")

    show = commands.add_parser("show", help="Прочитать сохранённый исторический пакет с проверкой его digest.")
    _workspace_argument(show)
    show.add_argument("--packet", required=True, help="ID неизменяемого пакета из решения или квитанции принятия.")


def run_content_command(args: argparse.Namespace) -> Any:
    """Run one content operation without exposing bearer credentials or hashes."""
    action = args.content_command
    if action not in {"enable", "policy", "status", "show"}:
        raise WorkbenchError("unknown_content_command", "Unknown content command")
    secrets: list[str] = []
    try:
        credential = None
        if action not in {"status", "show"}:
            credential = read_credential(args.credential)
            secrets.append(credential)
        database = _existing_database(args.workspace)
        policy = _read_json(args.policy.expanduser()) if action in {"enable", "policy"} else None
        store = Store(database)
        if action == "enable":
            result = store.content_enable(
                policy, credential=credential,
                expected_policy_generation=args.expected_policy_generation,
            )
        elif action == "policy":
            result = store.content_set_policy(
                policy, credential=credential,
                expected_policy_generation=args.expected_policy_generation,
            )
        elif action == "status":
            result = store.content_status(args.proposal)
        else:
            result = store.content_packet(args.packet)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, sqlite3.Error) as error:
        raise WorkbenchError("content_command_failed", _redact_text(str(error), secrets)) from error
    return _redact_value(result, secrets)
