"""Read a single agent's JSONL transcript for the inspector.

Understands both Claude Code subagent transcripts and ``qodercli -o
stream-json`` output (used by the DeepSeek swarm), which share the same
``assistant`` / ``user`` / ``result`` record shapes. Paths go through the same
translation, ``~/.claude`` confinement and size cap as every other transcript
reader in the backend.
"""

import json
import logging
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from app.core.path_utils import is_safe_transcript_path
from app.models.agents import AgentUsage, TranscriptEntry

__all__ = ["resolve_transcript", "read_entries", "summarize_usage"]

logger = logging.getLogger(__name__)

MAX_TRANSCRIPT_BYTES = 50_000_000
TEXT_LIMIT = 20_000
TOOL_IO_LIMIT = 4_000


def resolve_transcript(transcript_path: str | None) -> Path | None:
    """Translate and validate a transcript path; ``None`` if unusable."""
    if not transcript_path:
        return None
    from app.config import get_settings  # local import to avoid cycles

    translated = get_settings().translate_path(transcript_path)
    if not is_safe_transcript_path(translated):
        logger.warning("Rejected transcript path outside ~/.claude/: %s", translated)
        return None
    path = Path(translated).expanduser()
    try:
        if not path.is_file() or path.stat().st_size > MAX_TRANSCRIPT_BYTES:
            return None
    except OSError:
        return None
    return path


def _records(path: Path) -> Iterator[dict[str, Any]]:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a line still being written
                if isinstance(record, dict):
                    yield cast(dict[str, Any], record)
    except OSError as exc:
        logger.warning("Failed to read transcript %s: %s", path, exc)


def _clip(text: str, limit: int) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    return text[:limit].rstrip() + "\n…", True


def _timestamp(record: dict[str, Any]) -> datetime | None:
    raw = record.get("timestamp")
    if not isinstance(raw, str):
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list):
        return [cast(dict[str, Any], b) for b in cast(list[Any], content) if isinstance(b, dict)]
    return []


def _tool_result_text(block: dict[str, Any]) -> str:
    content = block.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in cast(list[Any], content):
            if isinstance(item, dict):
                item_dict = cast(dict[str, Any], item)
                if item_dict.get("type") == "text":
                    parts.append(str(item_dict.get("text", "")))
                else:
                    parts.append(f"[{item_dict.get('type', 'content')}]")
        return "\n".join(parts)
    return ""


def _format_tool_input(raw: Any) -> str:
    if isinstance(raw, str):
        return raw
    try:
        return json.dumps(raw, ensure_ascii=False, indent=2)
    except (TypeError, ValueError):
        return str(raw)


def _entries_from_record(record: dict[str, Any]) -> list[TranscriptEntry]:
    record_type = record.get("type")
    at = _timestamp(record)
    entries: list[TranscriptEntry] = []

    if record_type == "result":
        text = record.get("result")
        if isinstance(text, str) and text.strip():
            clipped, truncated = _clip(text, TEXT_LIMIT)
            entries.append(
                TranscriptEntry(
                    kind="result",
                    text=clipped,
                    truncated=truncated,
                    is_error=bool(record.get("is_error")),
                    timestamp=at,
                )
            )
        return entries

    message = record.get("message")
    if record_type not in ("user", "assistant") or not isinstance(message, dict):
        return entries
    message_dict = cast(dict[str, Any], message)

    for block in _blocks(message_dict):
        block_type = block.get("type")
        if block_type == "text":
            text = str(block.get("text", ""))
            if not text.strip():
                continue
            clipped, truncated = _clip(text, TEXT_LIMIT)
            kind = "prompt" if record_type == "user" else "text"
            entries.append(
                TranscriptEntry(kind=kind, text=clipped, truncated=truncated, timestamp=at)
            )
        elif block_type == "thinking":
            text = str(block.get("thinking", ""))
            if text.strip():
                clipped, truncated = _clip(text, TEXT_LIMIT)
                entries.append(
                    TranscriptEntry(
                        kind="thinking", text=clipped, truncated=truncated, timestamp=at
                    )
                )
        elif block_type == "tool_use":
            clipped, truncated = _clip(_format_tool_input(block.get("input", {})), TOOL_IO_LIMIT)
            entries.append(
                TranscriptEntry(
                    kind="tool_use",
                    text=clipped,
                    truncated=truncated,
                    tool_name=str(block.get("name") or "Tool"),
                    tool_use_id=block.get("id"),
                    timestamp=at,
                )
            )
        elif block_type == "tool_result":
            clipped, truncated = _clip(_tool_result_text(block), TOOL_IO_LIMIT)
            entries.append(
                TranscriptEntry(
                    kind="tool_result",
                    text=clipped,
                    truncated=truncated,
                    tool_use_id=block.get("tool_use_id"),
                    is_error=bool(block.get("is_error")),
                    timestamp=at,
                )
            )
    return entries


def read_entries(path: Path) -> list[TranscriptEntry]:
    """Flatten a transcript into display entries, oldest first.

    Tool results inherit the tool name of the matching ``tool_use`` so the
    frontend can label them without a second lookup.
    """
    entries: list[TranscriptEntry] = []
    tool_names: dict[str, str] = {}
    for record in _records(path):
        for entry in _entries_from_record(record):
            if entry.kind == "tool_use" and entry.tool_use_id and entry.tool_name:
                tool_names[entry.tool_use_id] = entry.tool_name
            elif entry.kind == "tool_result" and entry.tool_use_id:
                entry.tool_name = tool_names.get(entry.tool_use_id)
            entries.append(entry)
    return entries


def _int(value: Any) -> int:
    return int(value) if isinstance(value, int | float) else 0


def summarize_usage(path: Path) -> AgentUsage | None:
    """Aggregate token, credit and turn counts from a transcript.

    Claude Code writes one line per content block and repeats the message's
    ``usage`` on each, so usage is de-duplicated by message id. Token fields
    stay ``None`` when every reported count is zero.
    """
    usage_by_message: dict[str, dict[str, Any]] = {}
    model: str | None = None
    result: dict[str, Any] | None = None

    for index, record in enumerate(_records(path)):
        if record.get("type") == "result":
            result = record
            continue
        message = record.get("message")
        if record.get("type") != "assistant" or not isinstance(message, dict):
            continue
        message_dict = cast(dict[str, Any], message)
        usage = message_dict.get("usage")
        if isinstance(usage, dict):
            key = str(message_dict.get("id") or f"line-{index}")
            usage_by_message[key] = cast(dict[str, Any], usage)
        if isinstance(message_dict.get("model"), str):
            model = message_dict["model"]

    if not usage_by_message and result is None:
        return None

    usages = list(usage_by_message.values())
    output_tokens = sum(_int(u.get("output_tokens")) for u in usages)
    last = usages[-1] if usages else {}
    context_tokens = (
        _int(last.get("input_tokens"))
        + _int(last.get("cache_creation_input_tokens"))
        + _int(last.get("cache_read_input_tokens"))
    )
    has_tokens = output_tokens > 0 or context_tokens > 0
    credits = sum(float(u["credits"]) for u in usages if isinstance(u.get("credits"), int | float))
    has_credits = any(isinstance(u.get("credits"), int | float) for u in usages)
    ratio = last.get("context_usage_ratio")

    turns = len(usages) or None
    duration_ms: int | None = None
    if result is not None:
        if isinstance(result.get("num_turns"), int):
            turns = result["num_turns"]
        if isinstance(result.get("duration_ms"), int):
            duration_ms = result["duration_ms"]

    return AgentUsage(
        input_tokens=context_tokens if has_tokens else None,
        output_tokens=output_tokens if has_tokens else None,
        credits=round(credits, 4) if has_credits else None,
        turns=turns,
        context_ratio=float(ratio) if isinstance(ratio, int | float) else None,
        duration_ms=duration_ms,
        model=model,
    )
