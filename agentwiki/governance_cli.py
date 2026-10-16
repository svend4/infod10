"""Local governance commands with credentials kept in private POSIX files.

Credential files and SQLite do not share a transaction. New secrets are saved
before registration; a failed registration leaves its prepared file for retry.
Neither a credential nor its digest belongs in a command's JSON output.
"""

from __future__ import annotations

import argparse
import hashlib
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import time
from typing import Any

from . import governance
from .obligations import _read_json
from .store import Store, WorkbenchError


MAX_CREDENTIAL_BYTES = 256
MAX_TTL_SECONDS = 365 * 24 * 60 * 60
_TOKEN = re.compile(r"awg_[A-Za-z0-9_-]{43}\Z")
_STORE_COLUMNS = {
    "meta": {"key", "value"},
    "sources": {"id", "text", "digest"},
    "artifacts": {"id", "kind", "scope", "body", "digest", "revision"},
    "tasks": {"id", "role", "payload", "dependencies", "state", "token", "worker", "lease_until", "result_id"},
    "proposals": {"id", "task_id", "author", "token", "payload", "digest", "status", "accepted_revision"},
    "proposal_components": {"composite_id", "position", "source_id", "source_digest", "writes"},
    "reviews": {"id", "proposal_id", "reviewer", "proposal_digest", "rules_version", "approved", "report"},
    "revisions": {"revision", "proposal_id", "state", "state_digest"},
    "events": {"seq", "type", "data"},
}


def _private_file_support() -> None:
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise WorkbenchError("credential_files_unsupported", "Private credential files require POSIX O_NOFOLLOW")


def _credential_value(data: bytes) -> str:
    if not 1 <= len(data) <= MAX_CREDENTIAL_BYTES:
        raise WorkbenchError("invalid_credential_file", "Credential file must contain at most 256 bytes")
    if data.endswith(b"\n"):
        data = data[:-1]
    try:
        value = data.decode("ascii")
    except UnicodeError as error:
        raise WorkbenchError("invalid_credential_file", "Credential file must contain one ASCII token") from error
    if not _TOKEN.fullmatch(value):
        raise WorkbenchError("invalid_credential_file", "Credential file must contain one awg_ token and an optional final LF")
    return value


def read_credential(path: str | Path) -> str:
    """Read one private regular file without following its final symlink.

    The return value is a secret for the Store API, never display metadata.
    Only a single final LF is normalized; other whitespace is rejected.
    """
    _private_file_support()
    location = Path(path).expanduser()
    try:
        descriptor = os.open(
            location,
            os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        try:
            details = os.fstat(descriptor)
            if not stat.S_ISREG(details.st_mode) or details.st_mode & 0o077:
                raise WorkbenchError("unsafe_credential_file", "Credential must be a regular file without group or other permissions")
            if details.st_size > MAX_CREDENTIAL_BYTES:
                raise WorkbenchError("invalid_credential_file", "Credential file exceeds 256 bytes")
            chunks = bytearray()
            while len(chunks) <= MAX_CREDENTIAL_BYTES:
                block = os.read(descriptor, MAX_CREDENTIAL_BYTES + 1 - len(chunks))
                if not block:
                    break
                chunks.extend(block)
            return _credential_value(bytes(chunks))
        finally:
            os.close(descriptor)
    except OSError as error:
        raise WorkbenchError("credential_file_unavailable", "Cannot open a private regular credential file") from error


def prepare_credential(path: str | Path) -> str:
    """Generate a secret and save it exclusively as POSIX 0600, then fsync.

    Parent directories must already exist. Existing files are never replaced.
    A registration failure must leave this prepared file available for retry.
    """
    _private_file_support()
    location = Path(path).expanduser()
    value = governance.new_credential()
    data = (value + "\n").encode("ascii")
    _credential_value(data)
    try:
        descriptor = os.open(
            location,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0),
            0o600,
        )
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "wb", closefd=False) as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            os.close(descriptor)
    except OSError as error:
        raise WorkbenchError("credential_file_unavailable", "Cannot exclusively prepare a new private credential file") from error
    return value


def _positive_generation(value: str) -> int:
    try:
        result = int(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите положительное целое поколение политики.") from error
    if result < 1:
        raise argparse.ArgumentTypeError("Поколение политики должно быть положительным.")
    return result


def _ttl(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите положительное число секунд не более 365 дней.") from error
    if not math.isfinite(seconds) or not 0 < seconds <= MAX_TTL_SECONDS:
        raise argparse.ArgumentTypeError("Срок должен быть положительным и не превышать 365 дней.")
    return seconds


def _expiry(value: str) -> float:
    try:
        timestamp = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("Укажите конечное положительное время Unix в секундах.") from error
    if not math.isfinite(timestamp) or timestamp <= 0:
        raise argparse.ArgumentTypeError("Время окончания должно быть конечным положительным числом.")
    return timestamp


def _workspace_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", required=True, type=Path, help="Каталог существующей state.sqlite3; новая база не создаётся.")


def _credential_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--credential", required=True, type=Path, help="Закрытый POSIX-файл credential; секрет не передаётся аргументом.")


def _new_credential_arguments(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--credential-out", type=Path, help="Новый файл 0600: сохранить credential до регистрации; не перезаписывается.")
    group.add_argument("--prepared-credential", type=Path, help="Ранее подготовленный закрытый файл для повтора регистрации.")


def _generation_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--expected-policy-generation", required=True, type=_positive_generation)


def register_governance_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register authenticated local policy operations on an existing Store."""
    parser = subparsers.add_parser(
        "governance", help="Управлять локальными полномочиями и обязательной политикой принятия.",
        description=("Однократно включаемая политика существующего Store. Credential хранится в закрытом POSIX-файле. "
                     "Доступ к файлу SQLite позволяет обойти этот локальный API."),
    )
    commands = parser.add_subparsers(dest="governance_command", required=True)

    enable = commands.add_parser("enable", help="Однократно включить политику и зарегистрировать администратора.")
    _workspace_argument(enable)
    enable.add_argument("--administrator", required=True)
    enable.add_argument("--policy", required=True, type=Path, help="JSON: schema_version, minimum_reviewers, separate_coordinator.")
    _new_credential_arguments(enable)

    grant = commands.add_parser("grant", help="Выдать ограниченный по файлам и сроку допуск.")
    _workspace_argument(grant)
    _credential_argument(grant)
    grant.add_argument("--principal", required=True)
    grant.add_argument(
        "--role", required=True,
        choices=["reviewer", "coordinator", "analyst", "doc_author", "code_author", "integrator"],
        help="Роль допуска; роли авторов связываются с principal при включённом authorship.",
    )
    grant.add_argument("--artifact", action="append", required=True, dest="artifact_ids", help="Точный ID артефакта; повторяйте для нескольких файлов.")
    expiry = grant.add_mutually_exclusive_group(required=True)
    expiry.add_argument("--ttl-seconds", type=_ttl, help="Срок от момента вызова; для точного повтора используйте --expires-at.")
    expiry.add_argument("--expires-at", type=_expiry, help="Точное время Unix; сохраните значение для повтора с тем же ID операции.")
    grant.add_argument("--operation", required=True, help="ID операции выдачи для повторного запроса.")
    _new_credential_arguments(grant)

    revoke = commands.add_parser("revoke", help="Отозвать конкретный grant; прежние решения не оживают после новой выдачи.")
    _workspace_argument(revoke)
    _credential_argument(revoke)
    revoke.add_argument("--grant", required=True)

    policy = commands.add_parser("policy", help="Изменить политику с проверкой её текущего поколения.")
    _workspace_argument(policy)
    _credential_argument(policy)
    policy.add_argument("--policy", required=True, type=Path)
    policy.add_argument("--expected-generation", required=True, type=_positive_generation)

    decide = commands.add_parser("decide", help="Записать мнение principal по точному предложению и артефакту.")
    _workspace_argument(decide)
    _credential_argument(decide)
    decide.add_argument("--proposal", required=True)
    decide.add_argument("--artifact", required=True)
    decide.add_argument("--verdict", required=True, choices=["approve", "request_changes", "abstain"])
    decide.add_argument("--rationale", required=True)
    decide.add_argument("--expected-proposal-digest", required=True)
    decide.add_argument(
        "--content-packet-digest",
        help="Точный digest пакета: обязателен для нового решения при активном content gate; исторический повтор сохраняет прежнюю привязку.",
    )
    _generation_argument(decide)
    decide.add_argument("--previous-decision", help="Ожидаемый ID предыдущего решения; отсутствие означает первое мнение.")
    decide.add_argument("--operation", required=True)

    status = commands.add_parser("status", help="Показать публичную политику и полномочия без секретов и их хешей.")
    _workspace_argument(status)
    status.add_argument("--proposal")

    for name, description in [
        ("observe", "Наблюдать текущие препятствия принятию в одной транзакции."),
        ("review", "Выполнить формальную проверку с полномочиями reviewer."),
        ("accept", "Попробовать принять предложение под действующей политикой."),
    ]:
        action = commands.add_parser(name, help=description)
        _workspace_argument(action)
        _credential_argument(action)
        action.add_argument("--proposal", required=True)
        if name != "review":
            _generation_argument(action)


def _existing_database(workspace: Path) -> Path:
    """Reject missing or foreign files before Store can create its schema."""
    location = workspace.expanduser().resolve() / "state.sqlite3"
    if not location.is_file():
        raise WorkbenchError("workspace_unavailable", "Existing workspace state.sqlite3 is required")
    connection = None
    try:
        connection = sqlite3.connect(location.as_uri() + "?mode=ro", uri=True, timeout=30)
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not set(_STORE_COLUMNS).issubset(tables):
            raise WorkbenchError("foreign_workspace", "Database does not contain the existing Store schema")
        for table, expected in _STORE_COLUMNS.items():
            columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")')}
            if not expected.issubset(columns):
                raise WorkbenchError("foreign_workspace", "Database does not contain the existing Store schema")
        revision = connection.execute("SELECT value FROM meta WHERE key='revision'").fetchone()
        if not revision or not isinstance(revision[0], str) or not revision[0].isdigit():
            raise WorkbenchError("foreign_workspace", "Database has no valid Store revision")
    except sqlite3.Error as error:
        raise WorkbenchError("foreign_workspace", "Cannot open a valid existing Store database") from error
    finally:
        if connection is not None:
            connection.close()
    return location


def _redact_text(value: str, secrets: list[str]) -> str:
    for secret in secrets:
        value = value.replace(secret, "[redacted credential]")
        value = value.replace(hashlib.sha256(secret.encode("ascii")).hexdigest(), "[redacted credential digest]")
    return value


def _redact_value(value: Any, secrets: list[str]) -> Any:
    """Redact known bearer values while retaining public content packet keys.

    Validated content policies permit arbitrary scope keys. Filtering names
    such as ``token`` inside those mappings would alter a packet's hashed body.
    Store's public content packet schema itself contains no credential fields.
    """
    if isinstance(value, dict):
        return {
            _redact_text(str(key), secrets): _redact_value(child, secrets)
            for key, child in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact_value(child, secrets) for child in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def _content_packet_field(name: str, value: Any) -> bool:
    """Identify the Store-generated public packet or its immutable pin."""
    return (
        name in {"content", "content_packet"}
        and isinstance(value, dict)
        and {"packet_id", "packet_digest", "content_policy_generation", "content_policy_digest"}.issubset(value)
        and isinstance(value["packet_id"], str)
        and isinstance(value["packet_digest"], str)
        and bool(governance.HASH.fullmatch(value["packet_digest"]))
        and type(value["content_policy_generation"]) is int
        and value["content_policy_generation"] >= 1
    )


def _public_result(value: Any, secrets: list[str]) -> Any:
    """Filter ordinary output fields and preserve validated public packets."""
    if isinstance(value, dict):
        return {
            _redact_text(str(key), secrets): (
                _redact_value(child, secrets) if _content_packet_field(str(key), child)
                else _public_result(child, secrets)
            )
            for key, child in value.items()
            if str(key) == "credential_file" or not any(part in str(key).lower() for part in ("credential", "token", "secret", "password"))
        }
    if isinstance(value, (list, tuple)):
        return [_public_result(child, secrets) for child in value]
    if isinstance(value, str):
        return _redact_text(value, secrets)
    return value


def run_governance_command(args: argparse.Namespace) -> dict[str, Any]:
    """Run one operation without returning a credential or credential digest."""
    action = args.governance_command
    if action not in {"enable", "grant", "revoke", "policy", "decide", "status", "observe", "review", "accept"}:
        raise WorkbenchError("unknown_governance_command", "Unknown governance command")
    database = _existing_database(args.workspace)
    policy = _read_json(args.policy.expanduser()) if action in {"enable", "policy"} else None
    secrets: list[str] = []
    credential = None
    new_credential = None
    credential_file = None
    if action not in {"enable", "status"}:
        credential = read_credential(args.credential)
        secrets.append(credential)
    if action in {"enable", "grant"}:
        credential_file = args.credential_out or args.prepared_credential
        if args.credential_out is not None:
            new_credential = prepare_credential(args.credential_out)
        else:
            new_credential = read_credential(args.prepared_credential)
        secrets.append(new_credential)

    try:
        store = Store(database)
        if action == "enable":
            result = store.governance_enable(policy, args.administrator, new_credential)
        elif action == "grant":
            expires_at = args.expires_at if args.expires_at is not None else time.time() + args.ttl_seconds
            result = store.governance_grant(
                args.principal, args.role, args.artifact_ids, expires_at,
                credential=credential, new_credential=new_credential, operation_id=args.operation,
            )
        elif action == "revoke":
            result = store.governance_revoke(args.grant, credential=credential)
        elif action == "policy":
            result = store.governance_set_policy(policy, credential=credential, expected_generation=args.expected_generation)
        elif action == "decide":
            content_pin = {}
            if getattr(args, "content_packet_digest", None) is not None:
                content_pin["expected_content_packet_digest"] = args.content_packet_digest
            result = store.governance_decide(
                args.proposal, args.artifact, args.verdict, args.rationale,
                credential=credential, expected_proposal_digest=args.expected_proposal_digest,
                expected_policy_generation=args.expected_policy_generation,
                expected_previous_decision=args.previous_decision, operation_id=args.operation,
                **content_pin,
            )
        elif action == "status":
            result = store.governance_status(args.proposal)
        elif action == "observe":
            result = store.governance_observe(
                args.proposal, credential=credential,
                expected_policy_generation=args.expected_policy_generation,
            )
        elif action == "review":
            result = store.review(args.proposal, credential=credential)
        else:
            result = store.accept(
                args.proposal, credential=credential,
                expected_policy_generation=args.expected_policy_generation,
            )
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    public = _public_result(result, secrets)
    if credential_file is not None:
        public = {**public, "credential_file": _redact_text(str(credential_file.expanduser()), secrets)}
    return public
