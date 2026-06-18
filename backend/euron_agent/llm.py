"""Provider-agnostic LLM client.

The agent loop keeps its conversation in **OpenAI message format** regardless of
provider. Each client converts that to whatever the underlying API needs and
returns a normalized `LLMResponse` (assistant text + tool calls + token usage).

Adds resilience (retry with backoff on transient errors) and best-effort token
usage accounting.

Two client types:
  * OpenAICompatClient  — any OpenAI Chat Completions API (Euri, OpenAI,
                          OpenRouter, Ollama, vLLM, LM Studio, …).
  * AnthropicClient     — native Anthropic Messages API.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import AgentConfig, ProviderConfig

StreamCallback = Optional[Callable[[str], None]]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    prompt_tokens: int = 0
    completion_tokens: int = 0


class LLMError(RuntimeError):
    pass


import re

_REASONING_MODEL = re.compile(r"(^|[-/_])(o1|o3|o4|gpt-5|reason)", re.IGNORECASE)


def build_client(provider: ProviderConfig, agent: Optional[AgentConfig] = None):
    attempts = agent.retry_attempts if agent else 3
    backoff = agent.retry_backoff if agent else 1.5
    thinking = agent.thinking if agent else False
    effort = agent.reasoning_effort if agent else None
    if provider.type == "anthropic":
        return AnthropicClient(provider, attempts, backoff, thinking=thinking)
    return OpenAICompatClient(provider, attempts, backoff, reasoning_effort=effort)


def _safe_json_loads(s: str) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        try:
            return json.loads(s[: s.rfind("}") + 1])
        except Exception:
            return {"__raw__": s}


def _retryable(e: Exception) -> bool:
    """Retry connection/timeout/5xx/429; never retry auth/bad-request (4xx)."""
    code = getattr(e, "status_code", None)
    if code is None:
        code = getattr(getattr(e, "response", None), "status_code", None)
    if code in (400, 401, 403, 404, 422):
        return False
    return True


def _estimate(text: str) -> int:
    return max(0, len(text) // 4)


class _RetryMixin:
    retry_attempts: int
    retry_backoff: float

    def _with_retry(self, fn, stream_cb: StreamCallback):
        last: Optional[Exception] = None
        for attempt in range(1, self.retry_attempts + 1):
            streamed = {"n": 0}

            def cb(t: str):
                streamed["n"] += 1
                if stream_cb:
                    stream_cb(t)

            try:
                return fn(cb if stream_cb else None)
            except Exception as e:  # noqa: BLE001
                last = e
                # can't safely retry once tokens have been emitted to the user
                if (
                    attempt >= self.retry_attempts
                    or streamed["n"] > 0
                    or not _retryable(e)
                ):
                    break
                time.sleep(self.retry_backoff ** attempt)
        raise LLMError(f"{type(last).__name__}: {last}")


# --------------------------------------------------------------------------- #
# OpenAI-compatible
# --------------------------------------------------------------------------- #
class OpenAICompatClient(_RetryMixin):
    def __init__(
        self,
        provider: ProviderConfig,
        attempts: int = 3,
        backoff: float = 1.5,
        reasoning_effort: Optional[str] = None,
    ):
        from openai import OpenAI

        self.provider = provider
        self.retry_attempts = attempts
        self.retry_backoff = backoff
        self.reasoning_effort = reasoning_effort
        self.client = OpenAI(
            api_key=provider.api_key or "sk-no-key-required",
            base_url=provider.base_url,
            default_headers=provider.extra_headers or None,
        )

    def chat(self, messages, tools=None, stream_cb=None, stream=True) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.provider.model,
            "messages": messages,
            "temperature": self.provider.temperature,
            "max_tokens": self.provider.max_tokens,
        }
        # Reasoning models take reasoning_effort and reject custom temperature.
        if self.reasoning_effort and _REASONING_MODEL.search(self.provider.model):
            kwargs["reasoning_effort"] = self.reasoning_effort
            kwargs.pop("temperature", None)
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        def run(cb):
            return self._chat_stream(kwargs, cb) if stream else self._chat_once(kwargs)

        resp = self._with_retry(run, stream_cb)
        if not resp.prompt_tokens:  # estimate when the server didn't report usage
            resp.prompt_tokens = sum(
                _estimate(str(m.get("content") or "")) for m in messages
            )
        if not resp.completion_tokens:
            resp.completion_tokens = _estimate(resp.content)
        return resp

    def _chat_once(self, kwargs: dict) -> LLMResponse:
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = [
            ToolCall(tc.id, tc.function.name, _safe_json_loads(tc.function.arguments or "{}"))
            for tc in (msg.tool_calls or [])
        ]
        u = getattr(resp, "usage", None)
        return LLMResponse(
            content=msg.content or "",
            tool_calls=calls,
            prompt_tokens=getattr(u, "prompt_tokens", 0) or 0,
            completion_tokens=getattr(u, "completion_tokens", 0) or 0,
        )

    def _chat_stream(self, kwargs: dict, stream_cb: StreamCallback) -> LLMResponse:
        kwargs = {**kwargs, "stream": True}
        content_parts: list[str] = []
        partial: dict[int, dict] = {}
        for chunk in self.client.chat.completions.create(**kwargs):
            if not chunk.choices:
                continue
            delta = chunk.choices[0].delta
            if getattr(delta, "content", None):
                content_parts.append(delta.content)
                if stream_cb:
                    stream_cb(delta.content)
            for tc in getattr(delta, "tool_calls", None) or []:
                slot = partial.setdefault(tc.index, {"id": None, "name": "", "args": ""})
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments
        calls = [
            ToolCall(slot["id"] or f"call_{idx}", slot["name"], _safe_json_loads(slot["args"]))
            for idx, slot in sorted(partial.items())
            if slot["name"]
        ]
        return LLMResponse(content="".join(content_parts), tool_calls=calls)


# --------------------------------------------------------------------------- #
# Anthropic native
# --------------------------------------------------------------------------- #
class AnthropicClient(_RetryMixin):
    def __init__(
        self,
        provider: ProviderConfig,
        attempts: int = 3,
        backoff: float = 1.5,
        thinking: bool = False,
    ):
        import anthropic

        self.provider = provider
        self.retry_attempts = attempts
        self.retry_backoff = backoff
        self.thinking = thinking
        self.client = anthropic.Anthropic(
            api_key=provider.api_key, base_url=provider.base_url or None
        )

    @staticmethod
    def _to_anthropic_tools(tools):
        return [
            {
                "name": t["function"]["name"],
                "description": t["function"].get("description", ""),
                "input_schema": t["function"].get("parameters", {"type": "object"}),
            }
            for t in (tools or [])
        ]

    @staticmethod
    def _to_anthropic_messages(messages):
        system_parts: list[str] = []
        out: list[dict] = []

        def push(role: str, block: dict):
            if out and out[-1]["role"] == role:
                out[-1]["content"].append(block)
            else:
                out.append({"role": role, "content": [block]})

        for m in messages:
            role = m["role"]
            if role == "system":
                system_parts.append(m.get("content") or "")
            elif role == "user":
                content = m.get("content")
                if isinstance(content, list):
                    for block in content:
                        if block.get("type") == "text":
                            push("user", {"type": "text", "text": block.get("text", "")})
                        elif block.get("type") == "image_url":
                            url = block.get("image_url", {}).get("url", "")
                            if url.startswith("data:") and "," in url:
                                header, data = url.split(",", 1)
                                media = header.split(";")[0].split(":")[-1]
                                push("user", {
                                    "type": "image",
                                    "source": {"type": "base64", "media_type": media, "data": data},
                                })
                else:
                    push("user", {"type": "text", "text": content or ""})
            elif role == "assistant":
                if m.get("content"):
                    push("assistant", {"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc["function"]
                    push("assistant", {
                        "type": "tool_use",
                        "id": tc["id"],
                        "name": fn["name"],
                        "input": _safe_json_loads(fn.get("arguments") or "{}"),
                    })
            elif role == "tool":
                push("user", {
                    "type": "tool_result",
                    "tool_use_id": m.get("tool_call_id"),
                    "content": m.get("content") or "",
                })
        return "\n".join(p for p in system_parts if p), out

    def chat(self, messages, tools=None, stream_cb=None, stream=True) -> LLMResponse:
        system, conv = self._to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.provider.model,
            "system": system,
            "messages": conv,
            "max_tokens": self.provider.max_tokens,
            "temperature": self.provider.temperature,
        }
        if self.thinking:
            budget = min(8000, max(1024, self.provider.max_tokens // 2))
            kwargs["max_tokens"] = max(self.provider.max_tokens, budget + 1024)
            kwargs["temperature"] = 1  # required when thinking is enabled
            kwargs["thinking"] = {"type": "enabled", "budget_tokens": budget}
        if tools:
            kwargs["tools"] = self._to_anthropic_tools(tools)

        def run(cb):
            return self._chat_stream(kwargs, cb) if stream else self._chat_once(kwargs)

        return self._with_retry(run, stream_cb)

    def _collect(self, message) -> LLMResponse:
        content, calls = "", []
        for block in message.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                calls.append(ToolCall(block.id, block.name, block.input or {}))
        u = getattr(message, "usage", None)
        return LLMResponse(
            content=content,
            tool_calls=calls,
            prompt_tokens=getattr(u, "input_tokens", 0) or 0,
            completion_tokens=getattr(u, "output_tokens", 0) or 0,
        )

    def _chat_once(self, kwargs: dict) -> LLMResponse:
        return self._collect(self.client.messages.create(**kwargs))

    def _chat_stream(self, kwargs: dict, stream_cb: StreamCallback) -> LLMResponse:
        with self.client.messages.stream(**kwargs) as s:
            if stream_cb:
                for text in s.text_stream:
                    stream_cb(text)
            else:
                for _ in s.text_stream:
                    pass
            return self._collect(s.get_final_message())
