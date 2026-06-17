"""Event protocol and the AgentIO interface.

The agent loop is transport-agnostic: it talks to the outside world only
through an `AgentIO` implementation. The CLI implements it with the terminal;
the FastAPI server implements it over a WebSocket. Both speak the same set of
event dicts so the VS Code webview and the CLI render identical information.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from typing import Optional


# --------------------------------------------------------------------------- #
# Event constructors (plain dicts so they serialize straight to JSON)
# --------------------------------------------------------------------------- #
def status(message: str) -> dict:
    return {"type": "status", "message": message}


def token(text: str) -> dict:
    """A streamed chunk of assistant text."""
    return {"type": "token", "text": text}


def assistant_message(text: str) -> dict:
    """A complete assistant message (sent once a turn finishes)."""
    return {"type": "assistant_message", "text": text}


def tool_start(call_id: str, name: str, args: dict) -> dict:
    return {"type": "tool_start", "id": call_id, "name": name, "args": args}


def tool_result(call_id: str, name: str, ok: bool, output: str) -> dict:
    return {"type": "tool_result", "id": call_id, "name": name, "ok": ok, "output": output}


def diff(path: str, patch: str, is_new: bool = False) -> dict:
    return {"type": "diff", "path": path, "patch": patch, "is_new": is_new}


def approval_request(call_id: str, name: str, args: dict, preview: Optional[str]) -> dict:
    return {
        "type": "approval_request",
        "id": call_id,
        "name": name,
        "args": args,
        "preview": preview,
    }


def done(summary: str = "") -> dict:
    return {"type": "done", "summary": summary}


def error(message: str) -> dict:
    return {"type": "error", "message": message}


# --------------------------------------------------------------------------- #
# Approval decision + IO interface
# --------------------------------------------------------------------------- #
@dataclass
class ApprovalDecision:
    approved: bool
    feedback: Optional[str] = None  # optional user note fed back to the model


class AgentIO(abc.ABC):
    """How the agent loop emits output and requests human approval."""

    @abc.abstractmethod
    def on_token(self, text: str) -> None:
        """Called for every streamed token. MAY be invoked from a worker
        thread, so implementations must be thread-safe / non-blocking."""

    @abc.abstractmethod
    async def emit(self, event: dict) -> None:
        """Emit a structured event (always from the event loop)."""

    @abc.abstractmethod
    async def request_approval(self, request: dict) -> ApprovalDecision:
        """Ask the human to approve a gated action and block until answered."""
