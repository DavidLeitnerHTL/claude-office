"""Swarm simulation scenario.

Mimics a DeepSeek swarm run (``~/.claude/scripts/ds-swarm.ps1``): many
parallel workers named ``fNN-<topic>-uN-<task>``, grouped by slide lane, each
reported through the office bridge with a live qodercli-style transcript.

Exercises the office at scale: desk rows in the annex below the boss area,
the express lane, the agent roster (finished and failed workers stay listed),
model-queue waits and the inspector chat log.

Transcripts are written to ``~/.claude/claude-office-sim/<session>/`` because
the backend only reads transcripts below ``~/.claude``.

Environment:
    SIM_SWARM_SIZE   number of workers (default 12)
    SIM_SWARM_SPEED  time scale, 2.0 = twice as fast (default 1.0)
"""

from __future__ import annotations

import json
import os
import random
import shutil
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from scripts.scenarios._base import SimulationContext

LANES = [
    ("f10", "intro"),
    ("f11", "agenda"),
    ("f12", "zauber"),
    ("f13", "diagramm"),
    ("f14", "zeitleiste"),
    ("f15", "zitat"),
    ("f16", "abschluss"),
]
TASKS = ["besen", "hut", "stab", "farben", "layout", "text", "bilder", "animation"]
TOOLS = [
    ("Read", {"file_path": "slides/{lane}/index.html"}),
    ("Grep", {"pattern": 'class="slide"', "path": "slides/{lane}"}),
    ("Edit", {"file_path": "slides/{lane}/index.html", "old_string": "…", "new_string": "…"}),
    ("Bash", {"command": "npx prettier --check slides/{lane}"}),
    ("Write", {"file_path": "slides/{lane}/notes.md", "content": "…"}),
]


@dataclass
class Worker:
    name: str
    group: str
    task: str
    start_at: float
    duration: float
    fails: bool
    queue_wait: float
    transcript: Path
    tool_times: list[float] = field(default_factory=list)
    tools_done: int = 0
    started: bool = False
    queued: bool = False
    stopped: bool = False
    credits: float = 0.0

    @property
    def agent_id(self) -> str:
        return f"subagent_ds_{self.name}"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _append(path: Path, record: dict[str, object]) -> None:
    record = {"timestamp": _now(), **record}
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


def _plan(count: int, speed: float, out_dir: Path) -> list[Worker]:
    rng = random.Random(42)
    workers: list[Worker] = []
    for i in range(count):
        lane, topic = LANES[i % len(LANES)]
        task = f"u{i // len(LANES) + 1}-{TASKS[i % len(TASKS)]}"
        name = f"{lane}-{topic}-{task}"
        duration = rng.uniform(40, 150) / speed
        start_at = rng.uniform(0, 25) / speed
        workers.append(
            Worker(
                name=name,
                group=f"{lane}-{topic}",
                task=(
                    f"Folie {lane[1:]} ({topic}), Auftrag {task}: Überarbeite die Folie "
                    f"und halte dich an das Stilblatt. Antworte mit einer kurzen "
                    f"Zusammenfassung der Änderungen."
                ),
                start_at=start_at,
                duration=duration,
                fails=rng.random() < 0.12,
                queue_wait=rng.uniform(0, 12) / speed if rng.random() < 0.35 else 0.0,
                transcript=out_dir / f"{name}.jsonl",
                tool_times=sorted(
                    start_at + rng.uniform(3, duration * 0.9) for _ in range(rng.randint(2, 6))
                ),
            )
        )
    return workers


def _start(ctx: SimulationContext, w: Worker) -> None:
    w.transcript.write_text("", encoding="utf-8")
    _append(w.transcript, {"type": "system", "subtype": "init", "model": "DeepSeek-Flash"})
    ctx.send_event(
        "subagent_start",
        {
            "agent_id": w.agent_id,
            "agent_name": w.name,
            "agent_type": "DeepSeek-Flash",
            "model": "DeepSeek-Flash",
            "task_description": w.task,
            "agent_group": w.group,
            "keep_name": True,
            "source": "deepseek-swarm",
            "agent_transcript_path": str(w.transcript),
            "idle_timeout_seconds": 900,
            "project_name": "DeepSeek-Swarm",
        },
    )
    if w.queue_wait:
        _append(
            w.transcript, {"type": "system", "subtype": "model_queue_status", "status": "queued"}
        )
        w.queued = True
    w.started = True


def _tool_call(w: Worker, rng: random.Random) -> None:
    tool, template = TOOLS[rng.randrange(len(TOOLS))]
    lane = w.group
    tool_input = {k: v.replace("{lane}", lane) for k, v in template.items()}
    call_id = f"call_{w.name}_{w.tools_done}"
    credits = round(rng.uniform(0.01, 0.2), 4)
    w.credits += credits
    _append(
        w.transcript,
        {
            "type": "assistant",
            "message": {
                "id": f"msg_{call_id}",
                "role": "assistant",
                "model": "DeepSeek-Flash",
                "content": [
                    {"type": "text", "text": f"Ich prüfe {tool_input.get('file_path', lane)}."},
                    {"type": "tool_use", "id": call_id, "name": tool, "input": tool_input},
                ],
                "usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "credits": credits,
                    "context_usage_ratio": round(0.01 + 0.02 * w.tools_done, 4),
                },
            },
        },
    )
    _append(
        w.transcript,
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": call_id,
                        "content": f"ok ({tool} on {lane})",
                    }
                ],
            },
        },
    )
    w.tools_done += 1


def _stop(ctx: SimulationContext, w: Worker) -> None:
    summary = (
        "Abgebrochen: Stilblatt-Konflikt in der Kopfzeile, Folie bleibt unverändert."
        if w.fails
        else f"{w.group}: Layout angeglichen, {w.tools_done} Dateien geprüft, Texte gekürzt."
    )
    _append(
        w.transcript,
        {
            "type": "assistant",
            "message": {
                "id": f"msg_{w.name}_final",
                "role": "assistant",
                "model": "DeepSeek-Flash",
                "content": [{"type": "text", "text": summary}],
                "usage": {"input_tokens": 0, "output_tokens": 0, "credits": 0.01},
            },
        },
    )
    _append(
        w.transcript,
        {
            "type": "result",
            "subtype": "error" if w.fails else "success",
            "is_error": w.fails,
            "num_turns": w.tools_done + 1,
            "duration_ms": int(w.duration * 1000),
            "result": summary,
        },
    )
    ctx.send_event(
        "subagent_stop",
        {
            "agent_id": w.agent_id,
            "success": not w.fails,
            "result_summary": summary,
            "agent_transcript_path": str(w.transcript),
            "model": "DeepSeek-Flash",
            "agent_usage": {
                "credits": round(w.credits + 0.01, 4),
                "turns": w.tools_done + 1,
                "duration_ms": int(w.duration * 1000),
                "model": "DeepSeek-Flash",
            },
            "project_name": "DeepSeek-Swarm",
        },
    )
    w.stopped = True


def run(ctx: SimulationContext) -> None:
    """Execute the swarm scenario against *ctx*."""
    count = int(os.environ.get("SIM_SWARM_SIZE", "12"))
    speed = float(os.environ.get("SIM_SWARM_SPEED", "1.0"))
    out_dir = Path.home() / ".claude" / "claude-office-sim" / ctx.session_id
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True, exist_ok=True)

    ctx.reset(initial_fraction=0.1)
    ctx.log(f"[swarm] {count} workers, speed x{speed}, transcripts in {out_dir}")
    ctx.send_event("session_start", {"project_name": "DeepSeek-Swarm"})
    ctx.send_event(
        "user_prompt_submit",
        {"prompt": "Überarbeite die Präsentation Folie für Folie mit dem DeepSeek-Schwarm."},
    )

    rng = random.Random(7)
    workers = _plan(count, speed, out_dir)
    t0 = time.monotonic()
    while not all(w.stopped for w in workers):
        elapsed = time.monotonic() - t0
        for w in workers:
            if not w.started and elapsed >= w.start_at:
                _start(ctx, w)
                ctx.log(f"[swarm] start {w.name}")
            if not w.started or w.stopped:
                continue
            if w.queued and elapsed >= w.start_at + w.queue_wait:
                _append(
                    w.transcript,
                    {"type": "system", "subtype": "model_queue_status", "status": "ready"},
                )
                w.queued = False
            while (
                not w.queued
                and w.tools_done < len(w.tool_times)
                and elapsed >= w.tool_times[w.tools_done]
            ):
                _tool_call(w, rng)
            if elapsed >= w.start_at + w.duration:
                _stop(ctx, w)
                ctx.log(f"[swarm] {'FAIL' if w.fails else 'done'} {w.name}")
        time.sleep(0.25)

    time.sleep(2)
    ctx.send_event("stop", {})
    ctx.log("[swarm] all workers finished")
