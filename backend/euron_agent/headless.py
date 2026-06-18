"""Headless execution — run the agent non-interactively (CI, scripts, schedules).

Auto-approves all actions and optionally streams every event as JSON lines for
command chaining (`euron-agent run "..." --json`).
"""
from __future__ import annotations

import json

from .config import load_config
from .events import AgentIO, ApprovalDecision
from .loop import AgentSession


class HeadlessIO(AgentIO):
    def __init__(self, json_stream: bool = False):
        self.events: list[dict] = []
        self.final = ""
        self.json_stream = json_stream

    def _out(self, event: dict) -> None:
        if self.json_stream:
            print(json.dumps(event), flush=True)

    def emit_sync(self, event: dict) -> None:
        self.events.append(event)
        self._out(event)

    async def emit(self, event: dict) -> None:
        self.events.append(event)
        if event.get("type") == "assistant_message":
            self.final = event["text"]
        self._out(event)

    async def request_approval(self, request: dict) -> ApprovalDecision:
        self._out(request)
        return ApprovalDecision(approved=True)


async def run_headless(
    task: str,
    workspace: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    api_key: str | None = None,
    json_stream: bool = False,
    team: str | None = None,
) -> dict:
    cfg = load_config(provider=provider, model=model, api_key=api_key)
    cfg.agent.auto_approve_writes = True
    cfg.agent.auto_approve_commands = True
    io = HeadlessIO(json_stream)
    session = AgentSession(workspace, cfg, io, team=team)
    await session.run(task)
    return {
        "final": io.final,
        "tokens": session.session_tokens,
        "cost": round(session.session_cost, 4),
    }
