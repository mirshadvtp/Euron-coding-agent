"""The agentic loop.

A single `AgentSession` holds one conversation. Call `run(task)` to process a
user turn; the loop drives the LLM, executes tool calls (gating mutations behind
the AgentIO approval mechanism), and streams everything through AgentIO. The
conversation history persists on the session so follow-up turns keep context.
"""
from __future__ import annotations

import asyncio
import json

from . import events as ev
from .config import Config
from .events import AgentIO
from .llm import LLMError, build_client
from .prompts import system_prompt
from .tool_schemas import MUTATING_TOOLS, TOOL_SCHEMAS
from .tools import ToolContext, execute, list_files, preview_for


class AgentSession:
    def __init__(self, workspace: str, config: Config, io: AgentIO):
        self.workspace = workspace
        self.config = config
        self.io = io
        self.client = build_client(config.provider)
        self.ctx = ToolContext(workspace, config.agent, config.ignore)
        self.messages: list[dict] = []
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------ #
    def _ensure_system(self) -> None:
        if not self.messages:
            tree = list_files(self.ctx).output
            self.messages.append(
                {"role": "system", "content": system_prompt(self.workspace, tree)}
            )

    def _auto_approved(self, name: str) -> bool:
        if name not in MUTATING_TOOLS:
            return self.config.agent.auto_approve_reads
        if name == "run_command":
            return self.config.agent.auto_approve_commands
        return self.config.agent.auto_approve_writes

    def _stream_token(self, text: str) -> None:
        """Thread-safe token sink: the LLM client runs in a worker thread."""
        self.io.on_token(text)

    # ------------------------------------------------------------------ #
    async def run(self, task: str) -> None:
        self._loop = asyncio.get_running_loop()
        self._ensure_system()
        self.messages.append({"role": "user", "content": task})

        try:
            await self._agent_loop()
        except LLMError as e:
            await self.io.emit(ev.error(f"LLM error: {e}"))
            await self.io.emit(ev.done("failed"))
        except Exception as e:  # noqa: BLE001
            await self.io.emit(ev.error(f"Agent error: {type(e).__name__}: {e}"))
            await self.io.emit(ev.done("failed"))

    async def _agent_loop(self) -> None:
        for step in range(self.config.agent.max_steps):
            await self.io.emit(ev.status(f"thinking (step {step + 1})"))

            # Run the (blocking) LLM call off the event loop; tokens stream back
            # through the thread-safe sink.
            resp = await asyncio.to_thread(
                self.client.chat,
                self.messages,
                TOOL_SCHEMAS,
                self._stream_token,
                self.config.agent.stream,
            )

            if resp.content:
                await self.io.emit(ev.assistant_message(resp.content))

            # No tool calls => the model is done with this turn.
            if not resp.tool_calls:
                self.messages.append({"role": "assistant", "content": resp.content})
                await self.io.emit(ev.done(resp.content[:280]))
                return

            # Record the assistant turn (with its tool calls) in OpenAI format.
            self.messages.append(
                {
                    "role": "assistant",
                    "content": resp.content or None,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments),
                            },
                        }
                        for tc in resp.tool_calls
                    ],
                }
            )

            for tc in resp.tool_calls:
                await self._handle_tool_call(tc)

        await self.io.emit(ev.error("Reached max steps without finishing."))
        await self.io.emit(ev.done("max_steps"))

    async def _handle_tool_call(self, tc) -> None:
        await self.io.emit(ev.tool_start(tc.id, tc.name, tc.arguments))

        # Approval gate for mutating tools.
        if tc.name in MUTATING_TOOLS and not self._auto_approved(tc.name):
            preview = preview_for(self.ctx, tc.name, tc.arguments)
            decision = await self.io.request_approval(
                ev.approval_request(tc.id, tc.name, tc.arguments, preview)
            )
            if not decision.approved:
                note = decision.feedback or "no reason given"
                msg = f"User REJECTED this action. Reason: {note}. Do not retry it as-is."
                await self.io.emit(ev.tool_result(tc.id, tc.name, False, msg))
                self._append_tool_result(tc.id, msg)
                return

        outcome = await asyncio.to_thread(execute, self.ctx, tc.name, tc.arguments)

        if outcome.diff:
            await self.io.emit(ev.diff(tc.arguments.get("path", ""), outcome.diff, outcome.is_new))
        await self.io.emit(ev.tool_result(tc.id, tc.name, outcome.ok, outcome.output))
        self._append_tool_result(tc.id, outcome.output or "(no output)")

    def _append_tool_result(self, call_id: str, content: str) -> None:
        self.messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": content}
        )
