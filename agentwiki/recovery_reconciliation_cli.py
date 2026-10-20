"""Read-only recovery assessment commands with private plans and safe output.

The source is preflighted without constructing Store. A plan contains private
fingerprints and never becomes a command response or permission to activate a
snapshot. Public results are projected through fixed nested field allowlists.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path
import re
import sqlite3
from typing import Any

from .governance_cli import (
    _credential_argument,
    _existing_database,
    _redact_text,
    _redact_value,
    _workspace_argument,
    read_credential,
)
from .store import WorkbenchError
from .store_backup_cli import _public_summary as _snapshot_summary


_RULES_VERSION = "recovery-reconciliation-1"
_FLAGS = {
    "advisory_only": True,
    "activation_allowed": False,
    "domain_writes_performed": False,
    "private_fingerprints_exported": False,
}
_METADATA_FIELDS = {"revision", "event_high_water", "event_count", "table_counts"}
_SNAPSHOT_FIELDS = {
    "rules_version", "kind", "backup_id", "created_at", "inspection_only",
    "domain_writes_allowed", "revision", "event_high_water", "event_count",
    "table_counts", "integrity", "foreign_keys",
}
_ASSESSMENT_FIELDS = {
    "schema_version", "rules_version", "plan_id", "created_at", "snapshot", "source",
    "administrator_grant_id", "event_prefix", "append_only_preserved", "differences",
    "source_rows_match_at_both_observations", "time_semantics", *_FLAGS,
}
_REVALIDATION_FIELDS = {
    "rules_version", "plan_id", "observed_at", "database_rows_unchanged_at_observation",
    "current_administrator_authorized", "elapsed_seconds", "source", *_FLAGS,
}
_DIFFERENCE_FIELDS = {
    "snapshot_rows", "source_rows", "added_count", "removed_count", "changed_count",
    "added_keys", "removed_keys", "changed", "details_truncated",
}
_TABLE_KEYS = {
    "meta": {"key"},
    "sources": {"id"},
    "artifacts": {"id"},
    "tasks": {"id"},
    "proposals": {"id"},
    "proposal_components": {"composite_id", "position"},
    "reviews": {"id"},
    "revisions": {"revision"},
    "events": {"seq"},
    "governance_settings": {"singleton"},
    "governance_policies": {"generation"},
    "governance_grants": {"grant_id"},
    "governance_revocations": {"grant_id"},
    "governance_decisions": {"seq"},
    "governance_heads": {"proposal_id", "artifact_id", "principal_id"},
    "governance_operations": {"operation_id"},
    "governance_receipts": {"proposal_id"},
    "authorship_settings": {"singleton"},
    "authorship_leases": {"task_id", "token"},
    "proposal_authorship": {"proposal_id"},
    "content_settings": {"singleton"},
    "content_policies": {"generation"},
    "content_packets": {"packet_id"},
    "review_continuity_settings": {"singleton"},
    "review_continuity_transitions": {"transition_id"},
    "review_history_audits": {"audit_id"},
    "review_history_audit_checkpoints": {"checkpoint_id"},
    "review_history_audit_heads": {"audit_id"},
    "review_history_audit_operations": {"operation_id"},
}
_PUBLIC_COLUMN_NAMES = {
    "key", "value", "id", "text", "digest", "kind", "scope", "body", "revision",
    "role", "payload", "dependencies", "state", "token", "worker", "lease_until",
    "result_id", "task_id", "author", "status", "accepted_revision", "composite_id",
    "position", "source_id", "source_digest", "writes", "proposal_id", "reviewer",
    "proposal_digest", "rules_version", "approved", "report", "state_digest", "seq",
    "type", "data", "singleton", "administrator_id", "enabled_at", "activation_revision",
    "generation", "created_at", "administrator_grant_id", "grant_id", "principal_id",
    "artifact_ids", "expires_at", "issuer_grant_id", "revoked_at", "decision_id",
    "artifact_id", "operation_id", "action", "receipt_id", "epoch", "policy_generation",
    "claimed_at", "authorship_id", "governance_policy_generation", "packet_id",
    "content_policy_generation", "activation_generation", "content_policy_digest",
    "transition_id", "from_governance_generation", "to_governance_generation",
    "audit_id", "checkpoint_id", "checkpoint_index", "private_fields",
}


def _require(condition: bool) -> None:
    if not condition:
        raise WorkbenchError("recovery_summary_invalid", "Recovery API returned an unsupported public response")


def _object(value: Any, fields: set[str]) -> dict[str, Any]:
    _require(isinstance(value, dict) and set(value) == fields)
    return value


def _integer(value: Any) -> bool:
    return type(value) is int and 0 <= value < 2**63


def _number(value: Any) -> bool:
    return type(value) in {int, float} and 0 <= value < 2**63 and math.isfinite(value)


def _metadata(value: Any) -> dict[str, Any]:
    value = _object(value, _METADATA_FIELDS)
    _require(all(_integer(value[key]) for key in ("revision", "event_high_water", "event_count")))
    counts = _object(value["table_counts"], set(_TABLE_KEYS))
    _require(all(_integer(count) for count in counts.values()))
    _require(value["event_count"] == counts["events"] <= value["event_high_water"])
    return {"revision": value["revision"], "event_high_water": value["event_high_water"],
            "event_count": value["event_count"], "table_counts": dict(sorted(counts.items()))}


def _key(value: Any, table: str) -> dict[str, Any]:
    value = _object(value, _TABLE_KEYS[table])
    for item in value.values():
        _require(_integer(item) or (
            isinstance(item, str) and 0 < len(item.encode("utf-8")) <= 512
            and item.isprintable() and not re.search(r"awg_[A-Za-z0-9_-]{43}", item)
            and re.fullmatch(r"[0-9a-f]{64}", item) is None))
    return dict(sorted(value.items()))


def _difference(value: Any, table: str) -> dict[str, Any]:
    value = _object(value, _DIFFERENCE_FIELDS)
    counts = ("snapshot_rows", "source_rows", "added_count", "removed_count", "changed_count")
    _require(all(_integer(value[key]) for key in counts) and type(value["details_truncated"]) is bool)
    result = {key: value[key] for key in counts}
    for label in ("added_keys", "removed_keys", "changed"):
        _require(isinstance(value[label], list) and len(value[label]) <= 20)
    _require(sum(len(value[label]) for label in ("added_keys", "removed_keys", "changed")) <= 20)
    result["added_keys"] = [_key(key, table) for key in value["added_keys"]]
    result["removed_keys"] = [_key(key, table) for key in value["removed_keys"]]
    result["changed"] = []
    for entry in value["changed"]:
        entry = _object(entry, {"key", "fields"})
        fields = entry["fields"]
        _require(isinstance(fields, list) and 0 < len(fields) <= len(_PUBLIC_COLUMN_NAMES)
                 and all(isinstance(field, str) and field in _PUBLIC_COLUMN_NAMES for field in fields)
                 and len(fields) == len(set(fields)))
        result["changed"].append({"key": _key(entry["key"], table), "fields": list(fields)})
    _require(len(result["added_keys"]) <= value["added_count"]
             and len(result["removed_keys"]) <= value["removed_count"]
             and len(result["changed"]) <= value["changed_count"])
    result["details_truncated"] = value["details_truncated"]
    return result


def _public_response(value: Any, action: str, secrets: list[str]) -> dict[str, Any]:
    """Validate and project each public layer without copying a private plan."""
    fields = _ASSESSMENT_FIELDS if action == "assess" else _REVALIDATION_FIELDS
    value = _object(value, fields)
    _require(value["rules_version"] == _RULES_VERSION
             and isinstance(value["plan_id"], str)
             and re.fullmatch(r"rcp_[0-9a-f]{32}", value["plan_id"]) is not None
             and all(type(value[key]) is bool and value[key] is expected for key, expected in _FLAGS.items()))
    result = {"rules_version": value["rules_version"], "plan_id": value["plan_id"], **_FLAGS,
              "source": _metadata(value["source"])}
    if action == "assess":
        from .recovery_reconciliation import TIME_SEMANTICS

        _require(type(value["schema_version"]) is int and value["schema_version"] == 1
                 and _number(value["created_at"])
                 and isinstance(value["administrator_grant_id"], str)
                 and re.fullmatch(r"gvg_[0-9a-f]{32}", value["administrator_grant_id"]) is not None
                 and value["append_only_preserved"] is True
                 and value["source_rows_match_at_both_observations"] is True
                 and value["time_semantics"] == TIME_SEMANTICS)
        snapshot = _object(value["snapshot"], _SNAPSHOT_FIELDS)
        _metadata({key: snapshot[key] for key in _METADATA_FIELDS})
        event_prefix = _object(value["event_prefix"], {"matches", "compared_events"})
        _require(event_prefix["matches"] is True and _integer(event_prefix["compared_events"])
                 and event_prefix["compared_events"] == snapshot["event_count"])
        differences = _object(value["differences"], set(_TABLE_KEYS))
        result.update({
            "schema_version": 1, "created_at": value["created_at"],
            "snapshot": _snapshot_summary(snapshot, secrets),
            "administrator_grant_id": value["administrator_grant_id"],
            "event_prefix": {"matches": True, "compared_events": event_prefix["compared_events"]},
            "append_only_preserved": True,
            "differences": {table: _difference(differences[table], table) for table in sorted(_TABLE_KEYS)},
            "source_rows_match_at_both_observations": True, "time_semantics": TIME_SEMANTICS,
        })
    else:
        _require(_number(value["observed_at"]) and _number(value["elapsed_seconds"])
                 and value["database_rows_unchanged_at_observation"] is True
                 and value["current_administrator_authorized"] is True)
        result.update({"observed_at": value["observed_at"], "elapsed_seconds": value["elapsed_seconds"],
                       "database_rows_unchanged_at_observation": True,
                       "current_administrator_authorized": True})
    return _redact_value(result, secrets)


def register_recovery_parser(subparsers: argparse._SubParsersAction) -> None:
    """Register assessment and revalidation without an activation operation."""
    parser = subparsers.add_parser(
        "store-recovery", help="Сверить частный снимок с действующим источником без активации копии.",
        description=(
            "Авторизованная read-only сверка с существующим живым источником. "
            "Assess сохраняет новый частный план 0600 в родительском каталоге 0700; "
            "revalidate заново проверяет строки базы и текущий административный допуск. "
            "План не активирует снимок и не предоставляет разрешения записи; частные отпечатки не выводятся."
        ),
    )
    commands = parser.add_subparsers(dest="recovery_command", required=True)
    assess = commands.add_parser("assess", help="Сравнить снимок и источник, создать новый частный план.")
    assess.add_argument("--bundle", required=True, type=Path, help="Частный завершённый bundle store-snapshot-1.")
    _workspace_argument(assess)
    _credential_argument(assess)
    assess.add_argument("--plan", required=True, type=Path,
                        help="Новый частный файл плана; существующий файл не перезаписывается.")
    revalidate = commands.add_parser("revalidate", help="Проверить неизменность строк относительно частного плана.")
    _workspace_argument(revalidate)
    _credential_argument(revalidate)
    revalidate.add_argument("--plan", required=True, type=Path, help="Частный файл ранее созданного плана.")


def run_recovery_command(args: argparse.Namespace) -> dict[str, Any]:
    """Execute with file credentials, preserving only safe public responses."""
    action = args.recovery_command
    if action not in {"assess", "revalidate"}:
        raise WorkbenchError("unknown_recovery_command", "Unknown Store recovery command")
    secrets: list[str] = []
    try:
        from . import recovery_reconciliation, store_backup

        credential = read_credential(args.credential)
        secrets.append(credential)
        try:
            store_backup._no_symlinks(store_backup._path(args.workspace) / store_backup.DATABASE_NAME)
        except WorkbenchError as error:
            raise WorkbenchError("recovery_source_invalid", "An existing source path without symlinks is required") from error
        source = _existing_database(args.workspace)
        if action == "assess":
            result = recovery_reconciliation.assess_recovery(
                args.bundle, source, args.plan, credential=credential)
        else:
            result = recovery_reconciliation.revalidate_recovery(args.plan, source, credential=credential)
        return _public_response(result, action, secrets)
    except WorkbenchError as error:
        raise WorkbenchError(_redact_text(error.code, secrets), _redact_text(str(error), secrets)) from error
    except (ValueError, OSError, RuntimeError, TypeError, KeyError, OverflowError, UnicodeError, sqlite3.Error) as error:
        raise WorkbenchError("recovery_command_failed", "Recovery command failed") from error
