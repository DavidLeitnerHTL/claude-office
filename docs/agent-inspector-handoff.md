# Agent inspector — handoff

Branch `feature/agent-inspector` on the fork `DavidLeitnerHTL/claude-office`.
Goal: make the office usable for monitoring many parallel agents (Claude
subagents and DeepSeek swarm workers). The user approved the plan below on
2026-10-25; realistic swarm size is about 12 agents.

## Done

**Backend** (commit `dbde717`)

- Agent roster (`app/core/agent_roster.py`): one record per subagent, kept
  after it leaves the floor. Label, group, source, model, start/end, last tool
  call, model-queue wait, outcome, usage. Exposed as `GameState.agentRoster`.
- Desks: lowest free desk is reused; up to 48 desks; agents beyond that wait in
  the roster and get seated when a desk frees up.
- Inspector API (`app/api/routes/agents.py`):
  `GET /api/v1/sessions/{id}/agents/{agent_id}` (full task, fresh usage) and
  `.../transcript?limit=N` (prompt, replies, thinking, tool calls + results).
  Parser in `app/core/agent_transcript.py` handles Claude subagent JSONL and
  `qodercli -o stream-json`.
- New `AgentEventData` fields: `model`, `agent_group`, `keep_name`,
  `idle_timeout_seconds`, `agent_usage`, `queued`.
- Transcript poller: per-agent idle timeout, `model_queue_status` → `queued`,
  explicit-timeout zombies reported as failed.
- Token tracker no longer overwrites the session context meter with a
  subagent's usage.

**Frontend + simulation** (commit after `dbde717`)

- Office grows downward: desk rows 3+ sit in an annex below the boss area
  (`src/constants/officeLayout.ts`, single source for desk geometry, nav grid
  sized for 48 desks and walled off below the current height). The canvas is
  CSS-scaled with `object-fit: contain`, so the whole office always fits;
  the zoom reset button (now a fit icon) returns to that view.
- Express lane: with 3+ agents already waiting for the boss, newcomers walk
  straight to their desk / the elevator (`agentMachine.ts`).
- Simulation `python scripts/simulate_events.py swarm --session sim_swarm`
  (12 workers by default, `SIM_SWARM_SIZE`, `SIM_SWARM_SPEED`), writes live
  qodercli-style transcripts to `~/.claude/claude-office-sim/<session>/`.
  Needs `CLAUDE_OFFICE_API_URL` and `CLAUDE_OFFICE_API_KEY` in the env; run
  from the repo root with `uv run` (the backend venv has no `requests`).

## Open, in this order

1. **Store wiring**: put `agentRoster` from `GameState` into `gameStore`
   (`systems/stateReconciler.ts`), plus a small UI store for selection,
   inspector/chat-log open, main view, filters, collapsed groups.
2. **Inspector (layer 2)**: panel sliding over the right edge of the main area.
   Name, type/model, status, live runtime (the one prominent element, large
   tabular figures), tokens in/out or credits + context % for DeepSeek, full
   task, desk, last tool call, result, success/failure. Opens on click in the
   office (`AgentSprite` currently opens `AgentPopup` via `openFocusPopup`) and
   in the list. Fetch detail via the API above (header `X-API-Key`; check how
   other frontend fetches pass the token).
3. **Chat log (layer 3)**: expanding the inspector widens it and shows the
   transcript; tool calls as collapsed one-liners; poll every ~3 s while the
   agent is active.
4. **Roster list** replacing `components/game/AgentStatus.tsx` in
   `RightSidebar.tsx`: status chips with counts (= legend + filter), search,
   groups by `group` or prefix `fNN-topic`, "x/y done" progress per group,
   failed first and in red, finished agents stay listed.
5. **Table view** and **timeline (Gantt)** as main-area modes next to the
   office (switch Office / Table / Timeline).
6. **Notifications**: toast on done/failed (failed sticky), "group complete",
   optional desktop notification (opt-in).
7. **Keyboard**: ↑/↓ select, Enter open, Esc close, `/` search, `F` fit,
   `T` switch view, `L` chat log.
8. **Stalled agents**: badge when an active agent with a transcript has had no
   activity for 5 min (`lastActivityAt`); show `queued` as "waiting for model".
9. **Swarm scripts** (`~/.claude/scripts`, outside the repo, test only in a
   separate folder, never with the running swarm):
   - `ds-swarm.ps1`: run `qodercli ... -o stream-json`, write every line live
     (with an added `timestamp`) to `~/.claude/ds-swarm/<run>/<name>.jsonl`,
     keep writing the final answer (`result` field) to `out/<name>.md`; keep
     the other session's retry loop working (its busy check must look at the
     result text, since stream-json output is never empty).
   - `office-bridge.py`: stay compatible with the old positional calls (running
     workers already call the new bridge). Add optional `--transcript`,
     `--task-file`, `--group`; send `keep_name`, `agent_group`, `model`,
     `idle_timeout_seconds` (900), `agent_transcript_path` on start; on stop
     parse the JSONL for `agent_usage` (credits sum, turns, duration, context
     ratio; qodercli reports 0 tokens) and the result text.
10. **i18n**: new strings in `en`, `es`, `pt-BR` (optionally a German locale).
11. Screenshots at 8 and ~12 agents, then the final summary for the user.

## Environment notes

- Backend on 8010 (`uv run python -m uvicorn app.main:app --host 127.0.0.1
  --port 8010` in `backend/`, no `--reload`; restart + health check after
  backend changes), frontend `bun run dev` on 3000. Both were started detached
  with `Invoke-CimMethod Win32_Process Create`; logs in `logs/`.
- Port 8000 belongs to another process: never touch it.
- Backend tests: `CLAUDE_OFFICE_API_KEY= uv run python -m pytest -q`
  (`backend/.env` sets a key). Three `test_path_utils` failures are
  pre-existing and Windows-only. Use `python -m pyright`, not `pyright`.
- Screenshot helper (Playwright, reads the key from `backend/.env`): see the
  session scratchpad `shot.js`; click the session name to switch sessions.
