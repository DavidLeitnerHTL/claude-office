"""Tests for the agent inspector endpoints and per-agent token isolation."""

import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.event_processor import event_processor
from app.core.state_machine import StateMachine
from app.main import app
from app.models.events import EventAdapter, EventType

client = TestClient(app)


def _process(session_id: str, event_type: EventType, **data: Any) -> None:
    event = EventAdapter.validate_python(
        {
            "event_type": event_type,
            "session_id": session_id,
            "timestamp": datetime.now(UTC),
            "data": data,
        }
    )
    asyncio.run(event_processor.process_event(event))


def _transcript(temp_dir: Path) -> Path:
    path = temp_dir / "f12-zauber-u2-besen.jsonl"
    records: list[dict[str, Any]] = [
        {
            "type": "assistant",
            "message": {
                "id": "m1",
                "role": "assistant",
                "model": "DeepSeek-Flash",
                "content": [{"type": "text", "text": "Done with the slide."}],
                "usage": {"input_tokens": 0, "output_tokens": 0, "credits": 0.5},
            },
        },
        {"type": "result", "num_turns": 1, "duration_ms": 1200, "result": "Done with the slide."},
    ]
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return path


def test_detail_and_transcript_of_finished_worker(temp_dir: Path) -> None:
    sid = f"test-{uuid4()}"
    path = _transcript(temp_dir)
    agent_id = "subagent_ds_f12-zauber-u2-besen"
    _process(sid, EventType.SESSION_START)
    _process(
        sid,
        EventType.SUBAGENT_START,
        agent_id=agent_id,
        agent_name="f12-zauber-u2-besen",
        task_description="Polish slide 12",
        keep_name=True,
        source="deepseek-swarm",
    )
    _process(
        sid,
        EventType.SUBAGENT_STOP,
        agent_id=agent_id,
        success=True,
        result_summary="Done with the slide.",
        agent_transcript_path=str(path),
        agent_usage={"credits": 0.5, "turns": 1},
    )

    detail = client.get(f"/api/v1/sessions/{sid}/agents/{agent_id}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["fullTask"] == "Polish slide 12"
    assert body["record"]["status"] == "done"
    assert body["record"]["label"] == "f12-zauber-u2-besen"
    assert body["record"]["usage"]["credits"] == 0.5
    assert "taskFull" not in body["record"]

    transcript = client.get(f"/api/v1/sessions/{sid}/agents/{agent_id}/transcript")
    assert transcript.status_code == 200
    log = transcript.json()
    assert log["available"] is True
    # qodercli does not echo the prompt: the task is prepended.
    assert [e["kind"] for e in log["entries"]] == ["prompt", "text", "result"]
    assert log["entries"][0]["text"] == "Polish slide 12"


def test_transcript_limit_returns_newest_entries(temp_dir: Path) -> None:
    sid = f"test-{uuid4()}"
    path = temp_dir / "many.jsonl"
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {"id": f"m{i}", "content": [{"type": "text", "text": f"step {i}"}]},
            }
        )
        for i in range(20)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _process(sid, EventType.SESSION_START)
    _process(sid, EventType.SUBAGENT_START, agent_id="a1", agent_transcript_path=str(path))

    log = client.get(f"/api/v1/sessions/{sid}/agents/a1/transcript?limit=5").json()
    assert log["total"] == 20
    assert [e["text"] for e in log["entries"]] == [f"step {i}" for i in range(15, 20)]


def test_unknown_agent_returns_404() -> None:
    sid = f"test-{uuid4()}"
    _process(sid, EventType.SESSION_START)
    assert client.get(f"/api/v1/sessions/{sid}/agents/nope").status_code == 404
    assert client.get(f"/api/v1/sessions/{sid}/agents/nope/transcript").status_code == 404


def test_subagent_events_do_not_overwrite_session_context(temp_dir: Path) -> None:
    """A subagent's transcript must not replace the boss's context meter."""
    path = temp_dir / "sub.jsonl"
    path.write_text(
        json.dumps(
            {
                "type": "assistant",
                "message": {"usage": {"input_tokens": 5, "output_tokens": 1}},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    sm = StateMachine()
    sm.token_tracker.total_input_tokens = 150_000
    sm.token_tracker.total_output_tokens = 2_000
    with patch("app.core.token_tracker.is_safe_transcript_path", return_value=True):
        for event_type in (EventType.SUBAGENT_START, EventType.SUBAGENT_STOP):
            sm.transition(
                EventAdapter.validate_python(
                    {
                        "event_type": event_type,
                        "session_id": "s",
                        "data": {"agent_id": "a1", "agent_transcript_path": str(path)},
                    }
                )
            )
    assert sm.token_tracker.total_input_tokens == 150_000
    assert sm.token_tracker.total_output_tokens == 2_000
