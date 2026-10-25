from datetime import datetime
from enum import StrEnum
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.models.common import BubbleContent

__all__ = [
    "AgentState",
    "BossState",
    "Agent",
    "Boss",
    "ElevatorState",
    "PhoneState",
    "OfficeState",
    "RosterStatus",
    "ToolActivity",
    "AgentUsage",
    "AgentRecord",
    "TranscriptEntry",
    "AgentTranscript",
    "AgentDetail",
]


class AgentState(StrEnum):
    """Visual states for agent characters."""

    ARRIVING = "arriving"
    REPORTING = "reporting"
    WALKING_TO_DESK = "walking_to_desk"
    WORKING = "working"
    THINKING = "thinking"
    WAITING_PERMISSION = "waiting_permission"
    COMPLETED = "completed"
    WAITING = "waiting"
    REPORTING_DONE = "reporting_done"
    LEAVING = "leaving"
    IN_ELEVATOR = "in_elevator"
    IDLE = "idle"


class BossState(StrEnum):
    """Visual states for the boss character."""

    IDLE = "idle"
    PHONE_RINGING = "phone_ringing"
    ON_PHONE = "on_phone"
    RECEIVING = "receiving"
    WORKING = "working"
    DELEGATING = "delegating"
    WAITING_PERMISSION = "waiting_permission"
    REVIEWING = "reviewing"
    COMPLETING = "completing"


class Agent(BaseModel):
    """Represents a subagent in the office visualization."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    native_id: str | None = None  # Native Claude agent ID (e.g., "a5a60c7")
    name: str | None = None
    color: str
    number: int
    state: AgentState
    desk: int | None = None
    bubble: BubbleContent | None = None
    current_task: str | None = None
    position: dict[str, int] = {"x": 0, "y": 0}
    # Agent Teams character hierarchy (Phase 4)
    character_type: str | None = None  # "lead" | "teammate" | "subagent"
    parent_session_id: str | None = None  # session that owns this character
    parent_id: str | None = None  # for subagents: parent lead/teammate id


class RosterStatus(StrEnum):
    """Lifecycle outcome of a roster entry."""

    ACTIVE = "active"
    DONE = "done"
    FAILED = "failed"


class ToolActivity(BaseModel):
    """The most recent tool call of an agent."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    name: str
    summary: str
    at: datetime


class AgentUsage(BaseModel):
    """Resource usage of one agent.

    ``input_tokens`` is the latest context size (input + cache), ``output_tokens``
    the sum over all replies. Token fields stay ``None`` when the producer does
    not report tokens (qodercli reports zeros for DeepSeek-Flash).
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    input_tokens: int | None = None
    output_tokens: int | None = None
    credits: float | None = None
    turns: int | None = None
    context_ratio: float | None = None
    duration_ms: int | None = None
    model: str | None = None


class AgentRecord(BaseModel):
    """Roster entry for every subagent seen in a session, live or finished.

    Unlike ``Agent`` (a character on the floor, removed once it leaves), a
    record survives the agent's departure so its outcome stays inspectable.
    """

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    id: str
    name: str
    label: str  # name as given by the producer, never AI-shortened
    color: str
    source: str = "claude"
    group: str | None = None
    agent_type: str | None = None
    model: str | None = None
    status: RosterStatus = RosterStatus.ACTIVE
    state: AgentState | None = None  # visual state while on the floor
    desk: int | None = None
    task: str | None = None  # truncated for broadcast; full text via the detail API
    started_at: datetime
    ended_at: datetime | None = None
    last_activity_at: datetime
    last_tool: ToolActivity | None = None
    queued: bool = False  # waiting for a model slot
    result_summary: str | None = None
    success: bool | None = None
    usage: AgentUsage | None = None
    has_transcript: bool = False

    # Backend-only fields, excluded from every serialized state.
    task_full: str | None = Field(default=None, exclude=True)
    transcript_path: str | None = Field(default=None, exclude=True)


class TranscriptEntry(BaseModel):
    """One step of an agent's conversation, flattened for display."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    kind: Literal["prompt", "text", "thinking", "tool_use", "tool_result", "result"]
    text: str
    tool_name: str | None = None
    tool_use_id: str | None = None
    is_error: bool = False
    truncated: bool = False
    timestamp: datetime | None = None


class AgentTranscript(BaseModel):
    """Chat log of one agent, newest ``limit`` entries."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    agent_id: str
    available: bool  # False when the agent has no readable transcript
    total: int = 0  # entries before the limit was applied
    entries: list[TranscriptEntry] = Field(default_factory=lambda: cast(list[TranscriptEntry], []))


class AgentDetail(BaseModel):
    """Inspector payload: roster record plus data too large to broadcast."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    record: AgentRecord
    full_task: str | None = None


class Boss(BaseModel):
    """Represents the main Claude agent (boss) in the office visualization."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    state: BossState
    current_task: str | None = None
    bubble: BubbleContent | None = None
    position: dict[str, int] = {"x": 640, "y": 830}


class ElevatorState(StrEnum):
    """Visual states for the elevator."""

    CLOSED = "closed"
    ARRIVING = "arriving"
    OPEN = "open"
    DEPARTING = "departing"


class PhoneState(StrEnum):
    """Visual states for the boss's phone."""

    IDLE = "idle"
    RINGING = "ringing"
    IN_USE = "in_use"


class OfficeState(BaseModel):
    """Represents the overall state of the office environment."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)

    desk_count: int = 8
    elevator_state: ElevatorState = ElevatorState.CLOSED
    phone_state: PhoneState = PhoneState.IDLE
    context_utilization: float = 0.0
    tool_uses_since_compaction: int = 0
    print_report: bool = False
