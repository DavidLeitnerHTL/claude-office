"""Tests for per-agent transcript parsing (Claude subagents and qodercli stream-json)."""

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.core.agent_transcript import read_entries, resolve_transcript, summarize_usage


def _write(path: Path, records: list[dict[str, Any]]) -> Path:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


# Shapes taken from a real `qodercli -p -o stream-json` run (DeepSeek-Flash).
QODER_STREAM: list[dict[str, Any]] = [
    {"type": "system", "subtype": "init", "session_id": "s"},
    {
        "type": "assistant",
        "timestamp": "2026-10-25T21:26:40Z",
        "message": {
            "id": "m1",
            "role": "assistant",
            "model": "DeepSeek-Flash",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call_1",
                    "name": "Read",
                    "input": {"file_path": "a.txt"},
                }
            ],
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "credits": 0.17,
                "context_usage_ratio": 0.0202,
            },
        },
    },
    {
        "type": "user",
        "timestamp": "2026-10-25T21:27:06Z",
        "message": {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": "1\thello"}],
        },
    },
    {"type": "system", "subtype": "model_queue_status", "status": "queued"},
    {
        "type": "assistant",
        "message": {
            "id": "m2",
            "role": "assistant",
            "model": "DeepSeek-Flash",
            "content": [{"type": "text", "text": "The file says hello."}],
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
                "credits": 0.01,
                "context_usage_ratio": 0.0204,
            },
        },
    },
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "num_turns": 2,
        "duration_ms": 34494,
        "result": "The file says hello.",
        "total_credits": 0,
    },
]


def test_qodercli_stream_entries(temp_dir: Path) -> None:
    path = _write(temp_dir / "worker.jsonl", QODER_STREAM)
    entries = read_entries(path)
    assert [e.kind for e in entries] == ["tool_use", "tool_result", "text", "result"]
    tool_use, tool_result = entries[0], entries[1]
    assert tool_use.tool_name == "Read"
    assert json.loads(tool_use.text) == {"file_path": "a.txt"}
    assert tool_use.timestamp is not None
    # The result inherits the tool name of its call.
    assert tool_result.tool_name == "Read"
    assert tool_result.text == "1\thello"


def test_qodercli_usage_has_credits_but_no_tokens(temp_dir: Path) -> None:
    path = _write(temp_dir / "worker.jsonl", QODER_STREAM)
    usage = summarize_usage(path)
    assert usage is not None
    assert usage.input_tokens is None and usage.output_tokens is None
    assert usage.credits == 0.18
    assert usage.turns == 2
    assert usage.duration_ms == 34494
    assert usage.context_ratio == 0.0204
    assert usage.model == "DeepSeek-Flash"


def test_claude_transcript_usage_is_deduplicated_per_message(temp_dir: Path) -> None:
    usage_m1 = {
        "input_tokens": 10,
        "cache_read_input_tokens": 1000,
        "cache_creation_input_tokens": 50,
        "output_tokens": 40,
    }
    usage_m2 = {"input_tokens": 5, "cache_read_input_tokens": 1200, "output_tokens": 60}
    records: list[dict[str, Any]] = [
        {"type": "user", "message": {"role": "user", "content": "Find the bug"}},
        # Claude Code writes one line per content block and repeats usage.
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "model": "claude-opus-5-5",
                "content": [{"type": "thinking", "thinking": "Let me look"}],
                "usage": usage_m1,
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "model": "claude-opus-5-5",
                "content": [{"type": "tool_use", "id": "t1", "name": "Grep", "input": {}}],
                "usage": usage_m1,
            },
        },
        {
            "type": "assistant",
            "message": {
                "id": "m2",
                "model": "claude-opus-5-5",
                "content": [{"type": "text", "text": "Found it"}],
                "usage": usage_m2,
            },
        },
    ]
    path = _write(temp_dir / "agent-abc.jsonl", records)

    usage = summarize_usage(path)
    assert usage is not None
    assert usage.output_tokens == 100  # 40 + 60, m1 counted once
    assert usage.input_tokens == 1205  # latest context: 5 + 1200
    assert usage.turns == 2
    assert usage.credits is None
    assert usage.model == "claude-opus-5-5"

    kinds = [e.kind for e in read_entries(path)]
    assert kinds == ["prompt", "thinking", "tool_use", "text"]


def test_long_tool_output_is_clipped(temp_dir: Path) -> None:
    records: list[dict[str, Any]] = [
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [{"type": "tool_result", "tool_use_id": "x", "content": "y" * 10_000}],
            },
        }
    ]
    entry = read_entries(_write(temp_dir / "t.jsonl", records))[0]
    assert entry.truncated is True
    assert len(entry.text) < 5_000


def test_unsafe_or_missing_paths_are_rejected(temp_dir: Path) -> None:
    assert resolve_transcript(None) is None
    assert resolve_transcript(str(temp_dir / "missing.jsonl")) is None
    path = _write(temp_dir / "ok.jsonl", QODER_STREAM)
    assert resolve_transcript(str(path)) == path
    with patch("app.core.agent_transcript.is_safe_transcript_path", return_value=False):
        assert resolve_transcript(str(path)) is None


def test_partial_last_line_is_ignored(temp_dir: Path) -> None:
    path = _write(temp_dir / "live.jsonl", QODER_STREAM[:2])
    with open(path, "a", encoding="utf-8") as f:
        f.write('{"type": "assistant", "message": {"content": [')  # still being written
    assert [e.kind for e in read_entries(path)] == ["tool_use"]
