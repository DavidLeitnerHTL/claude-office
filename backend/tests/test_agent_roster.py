"""Tests for the session agent roster (records kept beyond an agent's departure)."""

# pyright: reportPrivateUsage=false

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

from app.core.agent_roster import MAX_ROSTER, TASK_PREVIEW_LEN, AgentRoster
from app.core.state_machine import StateMachine
from app.models.agents import AgentUsage, RosterStatus
from app.models.events import AgentEventData, AnyEvent, EventAdapter, EventType

T0 = datetime(2026, 10, 25, 12, 0, tzinfo=UTC)


def _event(event_type: EventType, seconds: int = 0, **data: Any) -> AnyEvent:
    return EventAdapter.validate_python(
        {
            "event_type": event_type,
            "session_id": "roster-test",
            "timestamp": T0 + timedelta(seconds=seconds),
            "data": data,
        }
    )


def _start(sm: StateMachine, agent_id: str, seconds: int = 0, **data: Any) -> None:
    sm.transition(_event(EventType.SUBAGENT_START, seconds, agent_id=agent_id, **data))


def _stop(sm: StateMachine, agent_id: str, seconds: int = 0, **data: Any) -> None:
    sm.transition(_event(EventType.SUBAGENT_STOP, seconds, agent_id=agent_id, **data))
    sm.transition(_event(EventType.CLEANUP, seconds, agent_id=agent_id))


class TestRecordLifecycle:
    def test_start_creates_record_with_producer_metadata(self) -> None:
        sm = StateMachine()
        _start(
            sm,
            "subagent_ds_f12-zauber-u2-besen",
            agent_name="f12-zauber-u2-besen",
            agent_type="DeepSeek-Flash",
            task_description="x" * 1000,
            source="deepseek-swarm",
            agent_group="f12-zauber",
            model="DeepSeek-Flash",
            keep_name=True,
        )
        record = sm.roster.get("subagent_ds_f12-zauber-u2-besen")
        assert record is not None
        assert record.label == "f12-zauber-u2-besen"
        assert record.name == "f12-zauber-u2-besen"  # keep_name skips renaming
        assert record.group == "f12-zauber"
        assert record.source == "deepseek-swarm"
        assert record.model == "DeepSeek-Flash"
        assert record.status == RosterStatus.ACTIVE
        assert record.started_at == T0
        assert record.desk == 1
        assert record.task is not None and len(record.task) == TASK_PREVIEW_LEN
        assert record.task_full == "x" * 1000

    def test_stop_marks_done_and_keeps_record_after_cleanup(self) -> None:
        sm = StateMachine()
        _start(sm, "a1", agent_name="worker")
        _stop(sm, "a1", seconds=90, result_summary="All slides fixed")
        assert "a1" not in sm.agents
        record = sm.roster.get("a1")
        assert record is not None
        assert record.status == RosterStatus.DONE
        assert record.success is True
        assert record.ended_at == T0 + timedelta(seconds=90)
        assert record.result_summary == "All slides fixed"

    def test_failed_stop_marks_failed_and_merges_usage(self) -> None:
        sm = StateMachine()
        _start(sm, "a1", agent_name="worker")
        _stop(
            sm,
            "a1",
            seconds=5,
            success=False,
            agent_usage={"credits": 0.25, "turns": 3, "model": "DeepSeek-Flash"},
        )
        record = sm.roster.get("a1")
        assert record is not None
        assert record.status == RosterStatus.FAILED
        assert record.usage == AgentUsage(credits=0.25, turns=3, model="DeepSeek-Flash")
        assert record.model == "DeepSeek-Flash"

    def test_tool_use_sets_last_tool_and_activity(self) -> None:
        sm = StateMachine()
        _start(sm, "a1", agent_name="worker")
        sm.transition(
            _event(
                EventType.PRE_TOOL_USE,
                30,
                agent_id="a1",
                tool_name="Bash",
                tool_input={"command": "npm test"},
            )
        )
        record = sm.roster.get("a1")
        assert record is not None and record.last_tool is not None
        assert record.last_tool.name == "Bash"
        assert record.last_tool.summary == "npm test"
        assert record.last_activity_at == T0 + timedelta(seconds=30)

    def test_queue_update_flags_without_leaving_arrival_queue(self) -> None:
        sm = StateMachine()
        _start(sm, "a1", agent_name="worker")
        sm.transition(_event(EventType.AGENT_UPDATE, 1, agent_id="a1", queued=True))
        record = sm.roster.get("a1")
        assert record is not None and record.queued is True
        assert "a1" in sm.arrival_queue

        sm.transition(_event(EventType.AGENT_UPDATE, 2, agent_id="a1", queued=False))
        assert record.queued is False

    def test_activity_update_moves_agent_out_of_arrival_queue(self) -> None:
        sm = StateMachine()
        _start(sm, "a1", agent_name="worker")
        sm.transition(_event(EventType.AGENT_UPDATE, 4, agent_id="a1", thinking="hmm"))
        assert "a1" not in sm.arrival_queue
        record = sm.roster.get("a1")
        assert record is not None
        assert record.last_activity_at == T0 + timedelta(seconds=4)

    def test_duplicate_start_keeps_existing_character(self) -> None:
        sm = StateMachine()
        _start(sm, "a1", agent_name="worker")
        _start(sm, "a1", seconds=3, agent_name="worker")
        assert len(sm.agents) == 1
        assert sm.arrival_queue == ["a1"]


class TestDesks:
    def test_freed_desk_is_reused(self) -> None:
        sm = StateMachine()
        for i in range(3):
            _start(sm, f"a{i}", agent_name=f"w{i}")
        _stop(sm, "a1")
        _start(sm, "a3", agent_name="w3")
        assert sm.agents["a3"].desk == 2
        assert sorted(a.desk or 0 for a in sm.agents.values()) == [1, 2, 3]

    def test_desk_count_covers_highest_occupied_desk(self) -> None:
        sm = StateMachine()
        for i in range(9):
            _start(sm, f"a{i}", agent_name=f"w{i}")
        for i in range(8):
            _stop(sm, f"a{i}")
        # Only desk 9 is occupied, which lives in the third row.
        assert sm.to_game_state("s").office.desk_count == 12

    def test_overflow_agent_waits_in_roster_then_takes_freed_desk(self) -> None:
        sm = StateMachine()
        with patch.object(StateMachine, "MAX_AGENTS", 2):
            _start(sm, "a1", agent_name="w1")
            _start(sm, "a2", agent_name="w2")
            _start(sm, "a3", agent_name="f13-wartet-u1", task_description="late")
            assert "a3" not in sm.agents
            waiting = sm.roster.get("a3")
            assert waiting is not None
            assert waiting.status == RosterStatus.ACTIVE and waiting.desk is None

            # A tool call from the waiting agent must not spawn a ghost character.
            sm.transition(_event(EventType.PRE_TOOL_USE, 2, agent_id="a3", tool_name="Read"))
            assert "a3" not in sm.agents
            assert waiting.last_tool is not None

            _stop(sm, "a1")
            assert "a3" in sm.agents
            assert sm.agents["a3"].desk == 1
            assert sm.agents["a3"].name == "f13-wartet-u1"
            assert "a3" in sm.arrival_queue

    def test_overflow_agent_finishing_before_seating_is_closed(self) -> None:
        sm = StateMachine()
        with patch.object(StateMachine, "MAX_AGENTS", 1):
            _start(sm, "a1", agent_name="w1")
            _start(sm, "a2", agent_name="w2")
            _stop(sm, "a2", seconds=10, success=False)
            _stop(sm, "a1", seconds=20)
        record = sm.roster.get("a2")
        assert record is not None and record.status == RosterStatus.FAILED
        assert "a2" not in sm.agents  # never seated after finishing


class TestSnapshot:
    def test_game_state_exposes_roster_without_backend_fields(self) -> None:
        sm = StateMachine()
        _start(sm, "a1", agent_name="w1", agent_transcript_path="/x/.claude/a.jsonl")
        dumped = sm.to_game_state("s").model_dump(mode="json", by_alias=True)
        entry = dumped["agentRoster"][0]
        assert entry["id"] == "a1"
        assert entry["hasTranscript"] is True
        assert entry["state"] == "arriving"
        assert "taskFull" not in entry and "transcriptPath" not in entry

    def test_replay_rebuilds_identical_roster(self) -> None:
        def run() -> list[dict[str, Any]]:
            sm = StateMachine()
            _start(sm, "a1", agent_name="w1", task_description="t1")
            _start(sm, "a2", seconds=1, agent_name="w2")
            sm.transition(_event(EventType.PRE_TOOL_USE, 2, agent_id="a2", tool_name="Edit"))
            _stop(sm, "a1", seconds=3, success=False)
            # "name" comes from the fallback name generator, which is random.
            return [
                r.model_dump(mode="json", exclude={"name"})
                for r in sm.to_game_state("s").agent_roster
            ]

        assert run() == run()

    def test_roster_trims_oldest_finished_records(self) -> None:
        roster = AgentRoster()
        for i in range(MAX_ROSTER + 5):
            agent_id = f"a{i}"
            roster.record_start(AgentEventData(agent_id=agent_id), T0, None)
            if i < 10:
                roster.record_stop(agent_id, AgentEventData(agent_id=agent_id), T0)
        assert len(roster.records) == MAX_ROSTER
        assert all(f"a{i}" not in roster for i in range(5))
        assert "a5" in roster  # finished, but within the cap
