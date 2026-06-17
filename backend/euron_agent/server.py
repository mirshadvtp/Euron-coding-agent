"""FastAPI server: WebSocket (streaming + approvals) and a REST fallback.

WebSocket protocol (JSON messages both directions)
--------------------------------------------------
Client -> server:
  {"type": "init", "workspace_path": "...", "provider": "euri"?, "model": "..."?}
  {"type": "run",  "task": "..."}
  {"type": "approval", "id": "...", "approved": true|false, "feedback": "..."?}

Server -> client (see euron_agent.events):
  status | token | assistant_message | tool_start | diff | tool_result |
  approval_request | done | error
"""
from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from . import events as ev
from .config import Config, load_config
from .events import AgentIO, ApprovalDecision
from .loop import AgentSession

app = FastAPI(title="Euron Agent", version="0.1.0")


# --------------------------------------------------------------------------- #
# WebSocket IO
# --------------------------------------------------------------------------- #
class WebSocketIO(AgentIO):
    def __init__(self, ws: WebSocket, loop: asyncio.AbstractEventLoop):
        self.ws = ws
        self.loop = loop
        self.pending: dict[str, asyncio.Future] = {}

    def on_token(self, text: str) -> None:
        # Called from the LLM worker thread -> hop back onto the event loop.
        asyncio.run_coroutine_threadsafe(self.ws.send_json(ev.token(text)), self.loop)

    async def emit(self, event: dict) -> None:
        await self.ws.send_json(event)

    async def request_approval(self, request: dict) -> ApprovalDecision:
        fut: asyncio.Future = self.loop.create_future()
        self.pending[request["id"]] = fut
        await self.ws.send_json(request)
        return await fut

    def resolve_approval(self, data: dict) -> None:
        fut = self.pending.pop(data.get("id"), None)
        if fut and not fut.done():
            fut.set_result(
                ApprovalDecision(
                    approved=bool(data.get("approved", False)),
                    feedback=data.get("feedback"),
                )
            )


def _config_for(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Config:
    return load_config(
        provider=provider, model=model, api_key=api_key, base_url=base_url
    )


@app.websocket("/ws")
async def ws_endpoint(ws: WebSocket):
    await ws.accept()
    loop = asyncio.get_running_loop()
    io = WebSocketIO(ws, loop)
    session: Optional[AgentSession] = None
    running: Optional[asyncio.Task] = None

    try:
        while True:
            data = await ws.receive_json()
            kind = data.get("type")

            if kind == "init":
                cfg = _config_for(
                    data.get("provider"),
                    data.get("model"),
                    data.get("api_key"),
                    data.get("base_url"),
                )
                session = AgentSession(data["workspace_path"], cfg, io)
                await ws.send_json(
                    ev.status(
                        f"ready · provider={cfg.provider.name} · model={cfg.provider.model}"
                    )
                )

            elif kind == "run":
                if session is None:
                    await ws.send_json(ev.error("Send an 'init' message first."))
                    continue
                if running and not running.done():
                    await ws.send_json(ev.error("A task is already running."))
                    continue
                running = asyncio.create_task(session.run(data["task"]))

            elif kind == "approval":
                io.resolve_approval(data)

            elif kind == "ping":
                await ws.send_json({"type": "pong"})

            else:
                await ws.send_json(ev.error(f"Unknown message type: {kind}"))

    except WebSocketDisconnect:
        if running and not running.done():
            running.cancel()


# --------------------------------------------------------------------------- #
# REST fallback (one-shot, non-interactive — auto-approves per config)
# --------------------------------------------------------------------------- #
class BufferIO(AgentIO):
    """Collects events for a single non-interactive request."""

    def __init__(self, auto_approve: bool):
        self.events: list[dict] = []
        self.auto_approve = auto_approve

    def on_token(self, text: str) -> None:  # ignored; REST returns final state
        pass

    async def emit(self, event: dict) -> None:
        self.events.append(event)

    async def request_approval(self, request: dict) -> ApprovalDecision:
        self.events.append(request)
        return ApprovalDecision(approved=self.auto_approve, feedback=None)


class RunRequest(BaseModel):
    task: str
    workspace_path: str
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    auto_approve: bool = False


@app.post("/agent/run")
async def agent_run(req: RunRequest):
    cfg = _config_for(req.provider, req.model, req.api_key, req.base_url)
    io = BufferIO(auto_approve=req.auto_approve)
    session = AgentSession(req.workspace_path, cfg, io)
    await session.run(req.task)
    final = next(
        (e["text"] for e in reversed(io.events) if e["type"] == "assistant_message"),
        "",
    )
    return {
        "status": "ok",
        "provider": cfg.provider.name,
        "model": cfg.provider.model,
        "final": final,
        "events": io.events,
    }


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/providers")
async def providers():
    cfg = load_config()
    return {
        "active": cfg.provider.name,
        "providers": {
            name: {"type": p.type, "model": p.model, "base_url": p.base_url}
            for name, p in cfg.all_providers.items()
        },
    }


def _free_port(host: str) -> int:
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((host, 0))
    port = s.getsockname()[1]
    s.close()
    return port


def serve(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    import uvicorn

    if port == 0:
        port = _free_port(host)

    # Announce the bound port on stdout so a parent process (the VS Code
    # extension) can discover it when --port 0 was requested.
    print(f"EURON_AGENT_LISTENING http://{host}:{port}", flush=True)

    uvicorn.run(
        "euron_agent.server:app" if reload else app,
        host=host,
        port=port,
        reload=reload,
    )
