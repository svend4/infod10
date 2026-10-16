#!/usr/bin/env python3
"""Render and verify a conversation archive using only the standard library."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SESSION = REPO_ROOT / "docs" / "sessions" / "session-001"


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def read_records(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            raise ValueError(f"{path.name}:{line_number}: empty JSONL record")
        record = json.loads(line)
        expected_id = f"m{line_number:04d}"
        if record.get("id") != expected_id:
            raise ValueError(f"Expected message ID {expected_id}")
        if record.get("role") not in {"user", "assistant"}:
            raise ValueError(f"{expected_id}: unsupported role")
        if record.get("channel") not in {"final", "commentary"}:
            raise ValueError(f"{expected_id}: unsupported visible channel")
        if record["role"] == "user" and record["channel"] != "final":
            raise ValueError(f"{expected_id}: user channel must be final")
        if not isinstance(record.get("content"), str) or not record["content"]:
            raise ValueError(f"{expected_id}: missing content")
        if not isinstance(record.get("source_part"), int):
            raise ValueError(f"{expected_id}: missing source_part")
        records.append(record)
    if not records:
        raise ValueError("Archive is empty")
    return records


def validate_counts(records: list[dict[str, Any]], metadata: dict[str, Any]) -> None:
    if len(records) != metadata["expected_message_count"]:
        raise ValueError("Message count differs from metadata")
    roles = Counter(record["role"] for record in records)
    if dict(roles) != metadata["expected_role_counts"]:
        raise ValueError("Role counts differ from metadata")
    channels = Counter(
        record["channel"] for record in records if record["role"] == "assistant"
    )
    if dict(channels) != metadata["expected_assistant_channel_counts"]:
        raise ValueError("Assistant channel counts differ from metadata")
    parts = Counter(record["source_part"] for record in records)
    expected_parts = {
        part["part"]: part["expected_message_count"] for part in metadata["parts"]
    }
    if dict(parts) != expected_parts:
        raise ValueError("Part counts differ from metadata")
    order = [record["source_part"] for record in records]
    if order != sorted(order):
        raise ValueError("Archive parts are out of order")


def literal_block(content: str) -> str:
    runs = re.findall(re.escape(chr(96)) + "+", content)
    fence = chr(96) * max(3, max((len(run) + 1 for run in runs), default=3))
    return f"{fence}text\n{content}\n{fence}"


def render(records: list[dict[str, Any]], metadata: dict[str, Any]) -> bytes:
    roles = Counter(record["role"] for record in records)
    result = [
        f"# Переписка — {metadata['session_id']}",
        "",
        "Полные доступные сообщения пользователя и видимые ответы ассистента.",
        "Граница снимка: пользовательский запрос сохранить сессию включительно.",
        "Точные временные метки сообщений недоступны.",
        "",
        f"Сообщений: {len(records)}; пользователь: {roles['user']}; "
        f"ассистент: {roles['assistant']}.",
        "",
        "[Границы архива](README.md) · [JSONL](transcript.jsonl) · "
        "[Метаданные](metadata.json) · [Контрольные суммы](manifest.json)",
        "",
        "Пользовательские сообщения показаны как буквальный текст; Markdown "
        "ассистента сохранён для чтения. Исходный текст каждого сообщения "
        "находится в поле content JSONL.",
        "",
    ]
    titles = {part["part"]: part["title"] for part in metadata["parts"]}
    current_part = None
    for record in records:
        if current_part != record["source_part"]:
            current_part = record["source_part"]
            result.extend([f"## Часть {current_part}. {titles[current_part]}", ""])
        role = "Пользователь" if record["role"] == "user" else "Ассистент"
        channel = "промежуточное сообщение" if record["channel"] == "commentary" else "сообщение"
        result.extend(
            [
                f'<a id="{record["id"]}"></a>',
                f"### {record['id']} · {role} · {channel}",
                "",
                literal_block(record["content"])
                if record["role"] == "user"
                else record["content"],
                "",
                "---",
                "",
            ]
        )
    return ("\n".join(result).rstrip() + "\n").encode("utf-8")


def make_manifest(
    records: list[dict[str, Any]], metadata: dict[str, Any], files: dict[str, bytes]
) -> bytes:
    role_counts = dict(Counter(record["role"] for record in records))
    channel_counts = dict(
        Counter(record["channel"] for record in records if record["role"] == "assistant")
    )
    manifest = {
        "schema_version": 1,
        "session_id": metadata["session_id"],
        "message_count": len(records),
        "role_counts": role_counts,
        "assistant_channel_counts": channel_counts,
        "content_characters": sum(len(record["content"]) for record in records),
        "first_message_id": records[0]["id"],
        "last_message_id": records[-1]["id"],
        "message_timestamps_available": metadata["message_timestamps_available"],
        "files": {
            name: {"bytes": len(data), "sha256": sha256(data)}
            for name, data in files.items()
        },
        "messages": [
            {
                "id": record["id"],
                "role": record["role"],
                "channel": record["channel"],
                "source_part": record["source_part"],
                "content_characters": len(record["content"]),
                "content_sha256": sha256(record["content"].encode("utf-8")),
            }
            for record in records
        ],
    }
    return json_bytes(manifest)


def extract_links(records: list[dict[str, Any]], session_id: str) -> bytes:
    """List cited HTTP(S) URLs without fetching or endorsing their contents."""
    links: dict[str, list[str]] = {}
    for record in records:
        urls = re.findall(r"https?://[^\s<>\"')\]]+", record["content"])
        for url in dict.fromkeys(url.rstrip(".,;:!?") for url in urls):
            links.setdefault(url, []).append(record["id"])
    return json_bytes(
        {
            "schema_version": 1,
            "session_id": session_id,
            "purpose": "References extracted from archived messages; not a verification report",
            "url_count": len(links),
            "links": [
                {"url": url, "message_ids": message_ids}
                for url, message_ids in links.items()
            ],
        }
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, default=DEFAULT_SESSION)
    parser.add_argument(
        "--check", action="store_true", help="Check generated files without changing them"
    )
    args = parser.parse_args()
    session = args.session.resolve()
    transcript_path = session / "transcript.jsonl"
    metadata_path = session / "metadata.json"
    records = read_records(transcript_path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    validate_counts(records, metadata)
    markdown = render(records, metadata)
    links = extract_links(records, metadata["session_id"])
    manifest = make_manifest(
        records,
        metadata,
        {
            "transcript.jsonl": transcript_path.read_bytes(),
            "transcript.md": markdown,
            "metadata.json": metadata_path.read_bytes(),
            "links.json": links,
        },
    )
    generated = {
        "transcript.md": markdown,
        "links.json": links,
        "manifest.json": manifest,
    }
    for name, data in generated.items():
        path = session / name
        if args.check:
            if not path.exists() or path.read_bytes() != data:
                raise SystemExit(f"OUTDATED: {path}; rerun this script without --check")
        else:
            path.write_bytes(data)
    print(
        f"{'Verified' if args.check else 'Built'} {metadata['session_id']}: "
        f"{len(records)} messages; "
        f"{sum(len(record['content']) for record in records)} content characters"
    )


if __name__ == "__main__":
    main()
