"""Session roster: one record per subagent, kept after the agent leaves.

``StateMachine.agents`` only holds characters currently on the floor and is
capped at ``MAX_AGENTS`` desks. The roster tracks every subagent of the session
(including ones that never got a desk) together with timing, last tool call,
outcome and usage, so the frontend can list and inspect agents of large swarms.

All mutations are driven by ``StateMachine.transition`` and use event
timestamps, so replaying a session's events rebuilds the same roster.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import cast

from app.models.agents import Agent, AgentRecord, AgentUsage, RosterStatus, ToolActivity
from app.models.events import AgentEventData

__all__ = ["AgentRoster", "MAX_ROSTER", "TASK_PREVIEW_LEN"]

MAX_ROSTER = 500
TASK_PREVIEW_LEN = 300
DEFAULT_COLOR = "#64748B"


def _empty_records() -> dict[str, AgentRecord]:
    return cast(dict[str, AgentRecord], {})


def _preview(text: str | None) -> str | None:
    if not text:
        return None
    text = text.strip()
    return text if len(text) <= TASK_PREVIEW_LEN else text[: TASK_PREVIEW_LEN - 1] + "…"


@dataclass
class AgentRoster:
    """Insertion-ordered roster of a session's subagents."""

    records: dict[str, AgentRecord] = field(default_factory=_empty_records)

    def get(self, agent_id: str | None) -> AgentRecord | None:
        """Return the record for *agent_id*, if any."""
        return self.records.get(agent_id) if agent_id else None

    def __contains__(self, agent_id: object) -> bool:
        return agent_id in self.records

    def record_start(
        self, data: AgentEventData, at: datetime, agent: Agent | None
    ) -> AgentRecord | None:
        """Create (or restart) the record for a SUBAGENT_START event.

        *agent* is the floor character when one was created; ``None`` means the
        office was full and the agent only lives in the roster for now.
        """
        if not data.agent_id:
            return None
        label = data.agent_name or (agent.name if agent else None) or data.agent_id
        task = data.task_description
        record = AgentRecord(
            id=data.agent_id,
            name=(agent.name if agent and agent.name else label),
            label=label,
            color=agent.color if agent else DEFAULT_COLOR,
            source=(data.source or "claude"),
            group=data.agent_group,
            agent_type=data.agent_type,
            model=data.model,
            state=agent.state if agent else None,
            desk=agent.desk if agent else None,
            task=_preview(task),
            task_full=task,
            started_at=at,
            last_activity_at=at,
            transcript_path=data.agent_transcript_path,
            has_transcript=bool(data.agent_transcript_path),
        )
        # Re-inserting moves a restarted id to the end (newest).
        self.records.pop(data.agent_id, None)
        self.records[data.agent_id] = record
        self._trim()
        return record

    def record_tool(self, agent_id: str, name: str, summary: str, at: datetime) -> None:
        """Remember the latest tool call of an agent."""
        record = self.records.get(agent_id)
        if record is None:
            return
        record.last_tool = ToolActivity(name=name, summary=summary, at=at)
        record.last_activity_at = at
        record.queued = False

    def record_activity(self, agent_id: str, at: datetime) -> None:
        """Bump the activity clock (thinking/text output, tool results)."""
        record = self.records.get(agent_id)
        if record is not None and at > record.last_activity_at:
            record.last_activity_at = at

    def apply_update(self, agent_id: str, data: AgentEventData, at: datetime) -> None:
        """Merge the roster fields of an AGENT_UPDATE event."""
        record = self.records.get(agent_id)
        if record is None:
            return
        if data.queued is not None:
            record.queued = data.queued
        else:
            self.record_activity(agent_id, at)
        if data.agent_usage is not None:
            record.usage = _merge_usage(record.usage, data.agent_usage)
            if data.agent_usage.model and not record.model:
                record.model = data.agent_usage.model
        if data.result_summary and not record.result_summary:
            record.result_summary = data.result_summary
        self.attach_transcript(agent_id, data.agent_transcript_path)

    def attach_transcript(self, agent_id: str, path: str | None) -> None:
        """Remember where an agent's JSONL transcript lives."""
        record = self.records.get(agent_id)
        if record is not None and path:
            record.transcript_path = path
            record.has_transcript = True

    def record_stop(self, agent_id: str, data: AgentEventData, at: datetime) -> None:
        """Close a record on SUBAGENT_STOP. Idempotent for repeated stops."""
        record = self.records.get(agent_id)
        if record is None:
            return
        if record.ended_at is None:
            record.ended_at = at
        record.last_activity_at = max(record.last_activity_at, at)
        record.queued = False
        if data.success is not None:
            record.success = data.success
        elif record.success is None:
            record.success = True  # native hooks only fire SubagentStop on success
        record.status = RosterStatus.DONE if record.success else RosterStatus.FAILED
        if data.result_summary:
            record.result_summary = data.result_summary
        if data.agent_usage is not None:
            record.usage = _merge_usage(record.usage, data.agent_usage)
            if data.agent_usage.model and not record.model:
                record.model = data.agent_usage.model
        if data.model and not record.model:
            record.model = data.model
        self.attach_transcript(agent_id, data.agent_transcript_path)

    def overflow_ids(self) -> list[str]:
        """Active records that have no character on the floor, oldest first."""
        return [
            r.id
            for r in self.records.values()
            if r.status == RosterStatus.ACTIVE and r.desk is None and r.state is None
        ]

    def snapshot(self, live_agents: dict[str, Agent]) -> list[AgentRecord]:
        """Return copies of all records with live floor state merged in."""
        return [self._merge_live(r, live_agents.get(r.id)) for r in self.records.values()]

    def view(self, agent_id: str, agent: Agent | None) -> AgentRecord | None:
        """Return a copy of one record with live floor state merged in."""
        record = self.records.get(agent_id)
        return self._merge_live(record, agent) if record is not None else None

    @staticmethod
    def _merge_live(record: AgentRecord, agent: Agent | None) -> AgentRecord:
        update: dict[str, object] = {}
        if agent is not None:
            update = {
                "state": agent.state,
                "desk": agent.desk,
                "color": agent.color,
                "name": agent.name or record.name,
            }
        elif record.status != RosterStatus.ACTIVE:
            update = {"state": None}
        return record.model_copy(update=update)

    def link_agent(self, agent: Agent) -> None:
        """Copy floor identity (desk, colour, state) onto the agent's record."""
        record = self.records.get(agent.id)
        if record is None:
            return
        record.desk = agent.desk
        record.color = agent.color
        record.state = agent.state

    def _trim(self) -> None:
        """Drop the oldest finished records once the roster exceeds MAX_ROSTER."""
        excess = len(self.records) - MAX_ROSTER
        if excess <= 0:
            return
        for agent_id in [r.id for r in self.records.values() if r.status != RosterStatus.ACTIVE][
            :excess
        ]:
            del self.records[agent_id]


def _merge_usage(old: AgentUsage | None, new: AgentUsage) -> AgentUsage:
    """Overlay the non-empty fields of *new* onto *old*."""
    if old is None:
        return new.model_copy()
    merged = old.model_copy()
    for name, value in new.model_dump(exclude_none=True).items():
        setattr(merged, name, value)
    return merged
