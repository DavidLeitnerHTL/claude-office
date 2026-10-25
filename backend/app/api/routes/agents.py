"""Agent inspector endpoints: roster detail and per-agent chat log."""

import asyncio

from fastapi import APIRouter, HTTPException, Query

from app.core.agent_transcript import read_entries, resolve_transcript, summarize_usage
from app.core.event_processor import get_event_processor
from app.models.agents import (
    AgentDetail,
    AgentRecord,
    AgentTranscript,
    RosterStatus,
    TranscriptEntry,
)

router = APIRouter(prefix="/sessions", tags=["agents"])

MAX_ENTRIES = 2000


async def _record_or_404(session_id: str, agent_id: str) -> AgentRecord:
    record = await get_event_processor().get_agent_record(session_id, agent_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Agent not found")
    return record


@router.get("/{session_id}/agents/{agent_id}")
async def get_agent_detail(session_id: str, agent_id: str) -> AgentDetail:
    """Return one roster record with its full task and up-to-date usage.

    Usage of a running agent is read fresh from its transcript; finished
    agents keep the usage captured at stop time.
    """
    record = await _record_or_404(session_id, agent_id)
    if record.status == RosterStatus.ACTIVE or record.usage is None:
        path = resolve_transcript(record.transcript_path)
        if path is not None:
            usage = await asyncio.to_thread(summarize_usage, path)
            if usage is not None:
                record = record.model_copy(update={"usage": usage})
                if usage.model and not record.model:
                    record.model = usage.model
    return AgentDetail(record=record, full_task=record.task_full)


@router.get("/{session_id}/agents/{agent_id}/transcript")
async def get_agent_transcript(
    session_id: str,
    agent_id: str,
    limit: int = Query(500, ge=1, le=MAX_ENTRIES, description="Newest N entries"),
) -> AgentTranscript:
    """Return the agent's chat log: prompt, replies, thinking and tool calls.

    qodercli stream output does not echo the prompt, so the task from the
    start event is used as the opening entry when the transcript has none.
    """
    record = await _record_or_404(session_id, agent_id)
    path = resolve_transcript(record.transcript_path)
    entries = await asyncio.to_thread(read_entries, path) if path is not None else []

    if record.task_full and not any(e.kind == "prompt" for e in entries):
        entries.insert(
            0, TranscriptEntry(kind="prompt", text=record.task_full, timestamp=record.started_at)
        )

    return AgentTranscript(
        agent_id=agent_id,
        available=path is not None,
        total=len(entries),
        entries=entries[-limit:],
    )
