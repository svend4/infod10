"""Conservative comparison of explicitly structured statements, never free prose.

Relations describe supplied data. They do not determine factual truth, infer
metadata from text, approve proposals, or change the canonical knowledge store.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from .store import WorkbenchError, digest


SEMANTIC_RULES_VERSION = "semantic-1"
STATEMENT_KEYS = {"subject", "property", "scope", "kind", "modality", "polarity", "value", "attribution", "evidence", "text"}
KINDS = {"specification", "measurement", "recommendation", "guarantee", "opinion"}
MODALITIES = {"asserted", "recommended", "required", "permitted", "prohibited"}
HASH = re.compile(r"[0-9a-f]{64}\Z")
IDENTIFIER = re.compile(r"[a-z][a-z0-9_.-]{0,100}\Z")
NUMBER = re.compile(r"[-+]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]{1,4})?\Z")
UNITS = {
    "ms": ("time", "s", Fraction(1, 1000)),
    "s": ("time", "s", Fraction(1)),
    "min": ("time", "s", Fraction(60)),
    "h": ("time", "s", Fraction(3600)),
    "B": ("information", "B", Fraction(1)),
    "KiB": ("information", "B", Fraction(1024)),
    "MiB": ("information", "B", Fraction(1048576)),
    "count": ("count", "count", Fraction(1)),
    "percent": ("ratio", "ratio", Fraction(1, 100)),
}


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise WorkbenchError("invalid_semantic_input", message)


def _text(value: Any, maximum: int = 20000, empty: bool = False) -> bool:
    return isinstance(value, str) and len(value) <= maximum and (empty or bool(value.strip()))


def _decimal(value: Any) -> Decimal:
    _require(isinstance(value, str) and len(value) <= 128 and bool(NUMBER.fullmatch(value)), "Quantity amount must be a bounded decimal string")
    try:
        number = Decimal(value)
    except InvalidOperation as error:
        raise WorkbenchError("invalid_semantic_input", "Invalid decimal amount") from error
    _require(number.is_finite() and abs(number.as_tuple().exponent) <= 1000 and abs(number.adjusted()) <= 1000,
             "Quantity must be finite and its exponent within ±1000")
    return number


def validate_statement(value: Any) -> None:
    _require(isinstance(value, dict) and set(value) == STATEMENT_KEYS, "Statement fields do not match the documented schema")
    for key in ["subject", "property"]:
        _require(_text(value[key], 256), f"Nonempty {key} required")
    _require(_text(value["attribution"], 256, empty=True), "Attribution must be text")
    _require(_text(value["text"]), "Original statement text required")
    scope = value["scope"]
    _require(isinstance(scope, dict) and len(scope) <= 20 and all(_text(key, 100) and _text(item, 512) for key, item in scope.items()), "Scope must contain bounded string keys and values")
    _require(isinstance(value["kind"], str) and value["kind"] in KINDS, "Unknown claim kind")
    _require(isinstance(value["modality"], str) and value["modality"] in MODALITIES, "Unknown modality")
    _require(isinstance(value["polarity"], str) and value["polarity"] in {"positive", "negative"}, "Unknown polarity")
    evidence = value["evidence"]
    if evidence is not None:
        _require(isinstance(evidence, dict) and set(evidence) == {"source_id", "source_digest", "quote"}, "Invalid evidence reference")
        _require(isinstance(evidence["source_id"], str) and bool(IDENTIFIER.fullmatch(evidence["source_id"])), "Invalid evidence source ID")
        _require(isinstance(evidence["source_digest"], str) and bool(HASH.fullmatch(evidence["source_digest"])), "Evidence needs a SHA-256 source digest")
        _require(_text(evidence["quote"], 100000), "Evidence quote required")
    item = value["value"]
    _require(isinstance(item, dict) and isinstance(item.get("type"), str), "A typed value is required")
    kind = item["type"]
    if kind == "quantity":
        _require(set(item) == {"type", "amount", "unit"} and _text(item["unit"], 40), "Invalid quantity fields")
        _decimal(item["amount"])
    elif kind == "set":
        _require(set(item) == {"type", "members", "complete"} and isinstance(item["members"], list), "Invalid set fields")
        _require(len(item["members"]) <= 200 and all(_text(member, 256) for member in item["members"]), "Set members must be bounded nonempty strings")
        _require(len(set(item["members"])) == len(item["members"]) and type(item["complete"]) is bool, "Set members must be unique and completeness boolean")
    elif kind == "boolean":
        _require(set(item) == {"type", "value"} and type(item["value"]) is bool, "Boolean value must be true or false, not a number")
    elif kind == "date":
        _require(set(item) == {"type", "value"} and isinstance(item["value"], str) and bool(re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", item["value"])), "Date must use YYYY-MM-DD")
        try:
            date.fromisoformat(item["value"])
        except ValueError as error:
            raise WorkbenchError("invalid_semantic_input", "Date is not a valid calendar day") from error
    elif kind == "text":
        _require(set(item) == {"type", "text"} and _text(item["text"]), "Opaque text value required")
    else:
        raise WorkbenchError("invalid_semantic_input", "Unsupported value type")


def validate_sources(value: Any) -> None:
    _require(isinstance(value, dict) and len(value) <= 100, "Sources must be an object of up to 100 snapshots")
    total = 0
    for identifier, source in value.items():
        _require(isinstance(identifier, str) and bool(IDENTIFIER.fullmatch(identifier)), "Invalid source ID")
        _require(isinstance(source, dict) and set(source) == {"text", "sha256"}, "Source needs text and sha256")
        _require(_text(source["text"], 1000000), "Source text required")
        _require(isinstance(source["sha256"], str) and bool(HASH.fullmatch(source["sha256"])), "Invalid source digest")
        data = source["text"].encode("utf-8")
        total += len(data)
        _require(total <= 5000000, "Total source content limit is 5 MB")
        _require(hashlib.sha256(data).hexdigest() == source["sha256"], "Source text does not match its SHA-256")


def _normalize(value: dict[str, Any]) -> dict[str, Any]:
    kind = value["type"]
    if kind == "quantity":
        rule = UNITS.get(value["unit"])
        if rule is None:
            return {"supported": False, "reason": "unsupported_unit", "input": value}
        dimension, base_unit, multiplier = rule
        number = Fraction(_decimal(value["amount"])) * multiplier
        return {
            "supported": True,
            "canonical": {"type": kind, "dimension": dimension, "unit": base_unit, "numerator": str(number.numerator), "denominator": str(number.denominator)},
            "conversion": {"input_unit": value["unit"], "factor_numerator": str(multiplier.numerator), "factor_denominator": str(multiplier.denominator)},
        }
    if kind == "set":
        return {"supported": True, "canonical": {"type": kind, "members": sorted(value["members"]), "complete": value["complete"]}}
    if kind in {"boolean", "date"}:
        return {"supported": True, "canonical": value}
    return {"supported": False, "reason": "opaque_text", "input": value}


def _evidence(statement: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
    reference = statement["evidence"]
    if reference is None:
        return {"status": "missing", "verified": False}
    source = sources.get(reference["source_id"])
    if source is None:
        return {"status": "snapshot_missing", "verified": False, "source_id": reference["source_id"]}
    if reference["source_digest"] != source["sha256"]:
        return {"status": "digest_mismatch", "verified": False, "source_id": reference["source_id"]}
    present = reference["quote"] in source["text"]
    return {"status": "quote_present" if present else "quote_absent", "verified": present, "source_id": reference["source_id"], "source_digest": source["sha256"], "check": "Literal quote presence only, not entailment or truth"}


def compare_statements(before: dict[str, Any], after: dict[str, Any], sources: dict[str, Any]) -> dict[str, Any]:
    """Describe a pair, preserving human review for every possible relation.

    This is not a three-way merge. ``before`` need not be a true statement and
    ``after`` need not be an invalid change: the relation reports what changed.
    """
    validate_statement(before)
    validate_statement(after)
    validate_sources(sources)
    findings = []

    def finding(code: str, explanation: str, **details: Any) -> None:
        findings.append({"code": code, "explanation": explanation, **details})

    normalized = {"before": _normalize(before["value"]), "after": _normalize(after["value"])}
    evidence = {"before": _evidence(before, sources), "after": _evidence(after, sources)}
    incomplete = False
    for side, result in evidence.items():
        if not result["verified"]:
            incomplete = True
            finding("evidence_unverified", "Основание не удалось проверить по сохранённому снимку; вывод требует редактора.", side=side, status=result["status"])
    for side, statement in [("before", before), ("after", after)]:
        if "version" not in statement["scope"]:
            incomplete = True
            finding("scope_version_missing", "Версия предмета не указана; одинаковая область применимости не установлена.", side=side)
        if not statement["attribution"].strip():
            incomplete = True
            finding("attribution_missing", "Автор или носитель утверждения не указан.", side=side)
        if not normalized[side]["supported"]:
            incomplete = True
            finding(normalized[side]["reason"], "Значение вне поддерживаемых формальных правил; смысл свободного текста не выводится.", side=side)

    context_changed = False
    for field in ["subject", "property"]:
        if before[field] != after[field]:
            context_changed = True
            finding(field + "_changed", "Идентификатор предмета или свойства изменён; алиасы автоматически не разрешаются.", before=before[field], after=after[field])
    if set(before["scope"]) != set(after["scope"]):
        incomplete = True
        finding("scope_keys_changed", "Набор условий различается; отсутствие условия не означает совпадение или непересечение областей.", before=before["scope"], after=after["scope"])
    elif before["scope"] != after["scope"]:
        context_changed = True
        finding("scope_changed", "Указаны разные условия; их совместимость и пересечение требуют отдельного рассмотрения.", before=before["scope"], after=after["scope"])

    semantic_metadata_changed = False
    for field in ["kind", "modality", "polarity", "attribution"]:
        if before[field] != after[field]:
            semantic_metadata_changed = True
            finding(field + "_changed", "Изменение типа, обязательности, отрицания или атрибуции влияет на содержание.", before=before[field], after=after[field])
    if before["kind"] == "measurement" and after["kind"] == "guarantee":
        finding("measurement_to_guarantee", "Результат измерения превращён в гарантию; числовое совпадение не обосновывает такое усиление.")
    if before["modality"] in {"recommended", "permitted"} and after["modality"] == "required":
        finding("normative_strengthening", "Рекомендация или разрешение заменены обязательным требованием.")
    if before["evidence"] != after["evidence"]:
        finding("evidence_changed", "Основание изменено; совпадение формальных значений не переносит редакционное одобрение на новый источник.")
    if before["text"] != after["text"]:
        finding("wording_changed", "Исходные тексты различаются; соответствие свободной формулировки полям не проверяется автоматически.")

    comparable = normalized["before"]["supported"] and normalized["after"]["supported"]
    same_value = False
    if comparable:
        old, new = normalized["before"]["canonical"], normalized["after"]["canonical"]
        if old["type"] != new["type"]:
            incomplete = True
            finding("value_type_changed", "Значения имеют разные типы; правило сравнения между ними не задано.")
        elif old["type"] == "quantity" and old["dimension"] != new["dimension"]:
            incomplete = True
            finding("incompatible_dimensions", "Единицы принадлежат разным размерностям; числовое сравнение не применяется.")
        else:
            same_value = digest(old) == digest(new)
            finding("equal_structured_value" if same_value else "structured_value_changed", "Нормализованные значения совпадают точно." if same_value else "Формальные значения различаются; это не определяет истинность сторон или допустимость обновления.")
            if old["type"] == "quantity" and before["value"]["unit"] != after["value"]["unit"]:
                finding("units_normalized", "Применено явное точное преобразование единиц без округления двоичных чисел.", before=normalized["before"]["conversion"], after=normalized["after"]["conversion"])
            if old["type"] == "set":
                added = sorted(set(new["members"]) - set(old["members"]))
                removed = sorted(set(old["members"]) - set(new["members"]))
                if added or removed:
                    finding("set_members_changed", "Изменился состав множества; полнота сведений о мире этим не устанавливается.", added=added, removed=removed)
                if old["complete"] != new["complete"]:
                    finding("completeness_changed", "Изменено заявление о полноте перечня; для него требуется исчерпывающий источник в указанной области.")
                if old["complete"] or new["complete"]:
                    finding("closed_world_annotation", "Флаг complete является входной аннотацией; полномочность источника задавать полный перечень автоматически не проверена.")

    if incomplete:
        relation, action = "undetermined", "supply_context_or_editorial_review"
    elif context_changed:
        relation, action = "different_context", "compare_contexts_without_assuming_disjointness"
    elif same_value and not semantic_metadata_changed:
        relation, action = "structured_equivalent", "review_wording_and_evidence"
    else:
        relation, action = "review_required", "review_change_or_preserve_attributed_disagreement"
    return {
        "schema_version": 1, "rules_version": SEMANTIC_RULES_VERSION,
        "input_digest": digest({"before": before, "after": after, "sources": sources}),
        "relation": relation, "review_action": action,
        "requires_human_review": True, "auto_accept": False,
        "findings": findings, "normalized": normalized, "evidence": evidence,
        "limitations": [
            "Only supplied structure is compared; fields are not inferred from or checked against free prose.",
            "Literal quote presence does not establish entailment, factual truth or source independence.",
            "A pair comparison does not implement three-way merge, readset checking or Store acceptance.",
            "Different scope metadata does not prove scopes are disjoint.",
            "No corpus annotation is an input to this function; no model or network is used.",
        ],
    }


def load_pair_file(path: str | Path) -> dict[str, Any]:
    """Capture and validate one explicit pair without changing its contents."""
    def pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result = {}
        for key, value in items:
            _require(key not in result, "Duplicate JSON object key")
            result[key] = value
        return result

    def bad_constant(value: str) -> None:
        raise WorkbenchError("invalid_semantic_input", "Non-finite JSON constant")

    with Path(path).open("rb") as stream:
        data = stream.read(5000001)
    _require(len(data) <= 5000000, "Pair file exceeds 5 MB")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=bad_constant)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise WorkbenchError("invalid_semantic_input", "Invalid pair JSON") from error
    _require(isinstance(value, dict) and set(value) == {"before", "after", "sources"}, "Pair file needs exactly before, after and sources")
    validate_statement(value["before"])
    validate_statement(value["after"])
    validate_sources(value["sources"])
    return value


def analyze_pair_file(path: str | Path) -> dict[str, Any]:
    """Read an explicit pair file, rejecting ambiguous JSON and oversized input."""
    value = load_pair_file(path)
    return compare_statements(value["before"], value["after"], value["sources"])
