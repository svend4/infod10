"""Credential-aware task commands for opt-in local authorship attribution.

Bearer credentials come only from private files. Integer fencing tokens are
public attempt counters, so claim output retains them for heartbeat/submission.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import sqlite3
from typing import Any

from .governance_cli import (
    _credential_argument,
    _existing_database,
    _positive_generation,
    _redact_text,
    _workspace_argument,
    read_credential,
)
from .obligations import _read_json
from .store import ROLE_KINDS, Store, WorkbenchError


def _fencing_token(value: str) -> int:
    try:
        token = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите положительный целый fencing token аренды.") from error
    if token <= 0:
        raise argparse.ArgumentTypeError("Fencing token аренды должен быть положительным целым.")
    return token


def _lease_seconds(value: str, *, allow_zero: bool) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите конечное число секунд аренды.") from error
    if not math.isfinite(seconds) or (seconds < 0 if allow_zero else seconds <= 0):
        description = "неотрицательным" if allow_zero else "положительным"
        raise argparse.ArgumentTypeError(f"Срок аренды должен быть конечным {description} числом.")
    return seconds


def _claim_lease(value: str) -> float:
    return _lease_seconds(value, allow_zero=True)


def _heartbeat_lease(value: str) -> float:
    return _lease_seconds(value, allow_zero=False)


def _attempt_arguments(parser: argparse.ArgumentParser) -> None:
    _workspace_argument(parser)
    _credential_argument(parser)
    parser.add_argument("--task", required=True, help="ID существующего задания.")
    parser.add_argument("--worker", required=True, help="Principal ID, совпадающий с владельцем действующего допуска.")
    parser.add_argument("--token", required=True, type=_fencing_token, help="Целый fencing token из результата claim; это счётчик, а не credential.")


def register_authorship_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register attribution and authenticated task operations on an existing DB."""
    parser = subparsers.add_parser(
        "authorship", help="Связать аренды и происхождение предложений с локальными допусками.",
        description=(
            "Однократно включаемый режим после governance. Worker должен совпадать с principal допуска. "
            "В активном режиме heartbeat, submit и compose требуют исходного действующего допуска аренды. "
            "Повтор завершённого submit/compose требует этого же действующего допуска, но не новой аренды; "
            "отозванный или истёкший допуск не разрешает повтор. Локальный credential не удостоверяет личность человека."
        ),
    )
    commands = parser.add_subparsers(dest="authorship_command", required=True)

    enable = commands.add_parser("enable", help="Однократно включить атрибуцию; повышает поколение governance-политики.")
    _workspace_argument(enable)
    _credential_argument(enable)
    enable.add_argument("--expected-policy-generation", required=True, type=_positive_generation)

    status = commands.add_parser("status", help="Показать публичные сведения о режиме и происхождении без credential и их хешей.")
    _workspace_argument(status)
    status.add_argument("--proposal", help="Проверить происхождение одного предложения и его предков.")

    claim = commands.add_parser("claim", help="Получить задание и публичный целый fencing token аренды.")
    _workspace_argument(claim)
    _credential_argument(claim)
    claim.add_argument("--worker", required=True, help="Principal ID, совпадающий с владельцем действующего допуска.")
    claim.add_argument("--role", action="append", choices=sorted(ROLE_KINDS), dest="roles", help="Ограничить роль задания; можно повторить. Без фильтра роль определяется допуском в активном режиме.")
    claim.add_argument("--lease-seconds", type=_claim_lease, default=60.0, help="Конечный неотрицательный срок; ноль создаёт сразу истёкшую аренду.")

    heartbeat = commands.add_parser("heartbeat", help="Продлить текущую аренду исходным действующим допуском.")
    _attempt_arguments(heartbeat)
    heartbeat.add_argument("--lease-seconds", type=_heartbeat_lease, default=60.0, help="Конечный положительный срок продления.")

    submit = commands.add_parser("submit", help="Сохранить предложение и происхождение; завершённый повтор требует исходного действующего допуска.")
    _attempt_arguments(submit)
    submit.add_argument("--proposal", required=True, type=Path, help="Файл ограниченного строгого JSON; Store ограничивает каноническую нагрузку 1 MB.")

    compose = commands.add_parser("compose", help="Создать композицию с происхождением всех компонентов по аренде integrator.")
    _attempt_arguments(compose)


def _public_authorship_result(value: Any, secrets: list[str]) -> Any:
    """Hide bearer-bearing fields, while preserving exact integer lease tokens.

    Authorship and proposal digests are public content identifiers. Credential
    hashes and any other hash-labelled fields are excluded defensively.
    """
    if isinstance(value, dict):
        public = {}
        for key, child in value.items():
            name = str(key)
            lowered = name.lower()
            if any(part in lowered for part in ("credential", "secret", "password", "bearer", "hash")):
                continue
            if "token" in lowered and not (
                lowered in {"token", "fencing_token", "lease_token"} and type(child) is int
            ):
                continue
            public[_redact_text(name, secrets)] = _public_authorship_result(child, secrets)
        return public
    if isinstance(value, (list, tuple)):
        return [_public_authorship_result(child, secrets) for child in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def run_authorship_command(args: argparse.Namespace) -> Any:
    """Execute one operation without exposing bearer credentials or their hashes."""
    action = args.authorship_command
    if action not in {"enable", "status", "claim", "heartbeat", "submit", "compose"}:
        raise WorkbenchError("unknown_authorship_command", "Unknown authorship command")
    secrets: list[str] = []
    try:
        credential = None
        if action != "status":
            credential = read_credential(args.credential)
            secrets.append(credential)
        database = _existing_database(args.workspace)
        proposal = _read_json(args.proposal.expanduser()) if action == "submit" else None
        store = Store(database)
        if action == "enable":
            result = store.authorship_enable(
                credential=credential, expected_policy_generation=args.expected_policy_generation,
            )
        elif action == "status":
            result = store.authorship_status(args.proposal)
        elif action == "claim":
            result = store.claim_task(
                args.worker, roles=args.roles, lease_seconds=args.lease_seconds, credential=credential,
            )
        elif action == "heartbeat":
            store.heartbeat(
                args.task, args.worker, args.token, lease_seconds=args.lease_seconds, credential=credential,
            )
            result = {"task_id": args.task, "worker": args.worker, "lease_token": args.token, "extended": True}
        elif action == "submit":
            result = store.submit(args.task, args.worker, args.token, proposal, credential=credential)
        else:
            result = store.compose(args.task, args.worker, args.token, credential=credential)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, sqlite3.Error) as error:
        raise WorkbenchError("authorship_command_failed", _redact_text(str(error), secrets)) from error
    return _public_authorship_result(result, secrets)
