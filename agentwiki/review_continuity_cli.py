"""Commands for explicitly enabled continuity of local review decisions.

Credentials are read only from private files. Public output preserves packet
and policy keys so that redaction does not invalidate inspectable digests.
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


def register_continuity_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register activation and public observations on an existing Store."""
    parser = subparsers.add_parser(
        "continuity", help="Сохранять силу рассмотрения при изменении несвязанных разделов.",
        description=(
            "Отдельно включаемый режим после governance, authorship и content schema 2. "
            "Включение повышает поколение governance и требует свежих исходных решений. "
            "Дальнейшая пригодность старого решения зависит от неизменности его оснований "
            "на каждом разрешённом переходе каталога и от текущих полномочий проверяющего. "
            "Локальный bearer credential не удостоверяет человека или независимость моделей."
        ),
    )
    commands = parser.add_subparsers(dest="continuity_command", required=True)

    enable = commands.add_parser(
        "enable", help="Однократно включить сохранение рассмотрения; повышает поколение governance.",
    )
    _workspace_argument(enable)
    _credential_argument(enable)
    enable.add_argument("--expected-policy-generation", required=True, type=_positive_generation)

    status = commands.add_parser(
        "status", help="Показать режим и историю переходов; наблюдение не разрешает принятие.",
    )
    _workspace_argument(status)
    status.add_argument(
        "--proposal", help="ID предложения для публичного наблюдения его текущих оснований.",
    )


def run_continuity_command(args: argparse.Namespace) -> Any:
    """Run one command without returning credentials or their SHA digests."""
    action = args.continuity_command
    if action not in {"enable", "status"}:
        raise WorkbenchError("unknown_continuity_command", "Unknown continuity command")
    secrets: list[str] = []
    try:
        credential = None
        if action == "enable":
            credential = read_credential(args.credential)
            secrets.append(credential)
        database = _existing_database(args.workspace)
        store = Store(database)
        if action == "enable":
            result = store.continuity_enable(
                credential=credential,
                expected_policy_generation=args.expected_policy_generation,
            )
        else:
            result = store.continuity_status(args.proposal)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, sqlite3.Error) as error:
        raise WorkbenchError("continuity_command_failed", _redact_text(str(error), secrets)) from error
    return _redact_value(result, secrets)
