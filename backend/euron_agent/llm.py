"""Provider-agnostic LLM client.

The agent loop keeps its conversation in **OpenAI message format** regardless of
provider. Each client converts that format to whatever the underlying API needs
and returns a normalized `LLMResponse` (assistant text + tool calls).

Two client types:
  * OpenAICompatClient  — any server speaking the OpenAI Chat Completions API
                          (Euron/Euri, OpenAI, OpenRouter, Groq, Together,
                           DeepSeek, Ollama, vLLM, LM Studio, llama.cpp ...).
  * AnthropicClient     — native Anthropic Messages API (best-in-class tools).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .config import ProviderConfig

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


class LLMError(RuntimeError):
    pass


# --------------------------------------------------------------------------- #
# Factory
# --------------------------------------------------------------------------- #
def build_client(provider: ProviderConfig):
    if provider.type == "anthropic":
        return AnthropicClient(provider)
    return OpenAICompatClient(provider)


def _safe_json_loads(s: str) -> dict:
    if not s:
        return {}
    try:
        return json.loads(s)
    except Exception:
        # Some small/local models emit trailing junk or single quotes.
        try:
            return json.loads(s[: s.rfind("}") + 1])
        except Exception:
            return {"__raw__": s}


# --------------------------------------------------------------------------- #
# OpenAI-compatible
# --------------------------------------------------------------------------- #
class OpenAICompatClient:
    def __init__(self, provider: ProviderConfig):
        from openai import OpenAI  # imported lazily so anthropic-only setups work

        self.provider = provider
        # api_key may be None for local servers; the SDK requires a non-empty
        # string, so pass a placeholder.
        self.client = OpenAI(
            api_key=provider.api_key or "sk-no-key-required",
            base_url=provider.base_url,
            default_headers=provider.extra_headers or None,
        )

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream_cb: StreamCallback = None,
        stream: bool = True,
    ) -> LLMResponse:
        kwargs: dict[str, Any] = {
            "model": self.provider.model,
            "messages": messages,
            "temperature": self.provider.temperature,
            "max_tokens": self.provider.max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"

        try:
            if stream:
                return self._chat_stream(kwargs, stream_cb)
            return self._chat_once(kwargs)
        except Exception as e:  # noqa: BLE001 - surface a clean error upward
            raise LLMError(f"{type(e).__name__}: {e}") from e

    def _chat_once(self, kwargs: dict) -> LLMResponse:
        resp = self.client.chat.completions.create(**kwargs)
        msg = resp.choices[0].message
        calls = []
        for tc in msg.tool_calls or []:
            calls.append(
                ToolCall(
                    id=tc.id,
                    name=tc.function.name,
                    arguments=_safe_json_loads(tc.function.arguments or "{}"),
                )
            )
        return LLMResponse(content=msg.content or "", tool_calls=calls)

    def _chat_stream(self, kwargs: dict, stream_cb: StreamCallback) -> LLMResponse:
        kwargs = {**kwargs, "stream": True}
        content_parts: list[str] = []
        # index -> partial tool call
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
                slot = partial.setdefault(
                    tc.index, {"id": None, "name": "", "args": ""}
                )
                if tc.id:
                    slot["id"] = tc.id
                if tc.function and tc.function.name:
                    slot["name"] += tc.function.name
                if tc.function and tc.function.arguments:
                    slot["args"] += tc.function.arguments

        calls = [
            ToolCall(
                id=slot["id"] or f"call_{idx}",
                name=slot["name"],
                arguments=_safe_json_loads(slot["args"]),
            )
            for idx, slot in sorted(partial.items())
            if slot["name"]
        ]
        return LLMResponse(content="".join(content_parts), tool_calls=calls)


# --------------------------------------------------------------------------- #
# Anthropic native
# --------------------------------------------------------------------------- #
class AnthropicClient:
    def __init__(self, provider: ProviderConfig):
        import anthropic  # lazy import

        self.provider = provider
        self.client = anthropic.Anthropic(
            api_key=provider.api_key, base_url=provider.base_url or None
        )

    @staticmethod
    def _to_anthropic_tools(tools: Optional[list[dict]]) -> list[dict]:
        out = []
        for t in tools or []:
            fn = t["function"]
            out.append(
                {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "input_schema": fn.get("parameters", {"type": "object"}),
                }
            )
        return out

    @staticmethod
    def _to_anthropic_messages(messages: list[dict]):
        """Convert OpenAI-format history -> (system, anthropic_messages)."""
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
                push("user", {"type": "text", "text": m.get("content") or ""})
            elif role == "assistant":
                if m.get("content"):
                    push("assistant", {"type": "text", "text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc["function"]
                    push(
                        "assistant",
                        {
                            "type": "tool_use",
                            "id": tc["id"],
                            "name": fn["name"],
                            "input": _safe_json_loads(fn.get("arguments") or "{}"),
                        },
                    )
            elif role == "tool":
                # tool results live inside a *user* turn for Anthropic
                push(
                    "user",
                    {
                        "type": "tool_result",
                        "tool_use_id": m.get("tool_call_id"),
                        "content": m.get("content") or "",
                    },
                )
        return "\n".join(p for p in system_parts if p), out

    def chat(
        self,
        messages: list[dict],
        tools: Optional[list[dict]] = None,
        stream_cb: StreamCallback = None,
        stream: bool = True,
    ) -> LLMResponse:
        system, conv = self._to_anthropic_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.provider.model,
            "system": system,
            "messages": conv,
            "max_tokens": self.provider.max_tokens,
            "temperature": self.provider.temperature,
        }
        if tools:
            kwargs["tools"] = self._to_anthropic_tools(tools)

        try:
            if stream:
                return self._chat_stream(kwargs, stream_cb)
            return self._chat_once(kwargs)
        except Exception as e:  # noqa: BLE001
            raise LLMError(f"{type(e).__name__}: {e}") from e

    def _collect(self, message) -> LLMResponse:
        content = ""
        calls = []
        for block in message.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                calls.append(
                    ToolCall(id=block.id, name=block.name, arguments=block.input or {})
                )
        return LLMResponse(content=content, tool_calls=calls)

    def _chat_once(self, kwargs: dict) -> LLMResponse:
        return self._collect(self.client.messages.create(**kwargs))

    def _chat_stream(self, kwargs: dict, stream_cb: StreamCallback) -> LLMResponse:
        with self.client.messages.stream(**kwargs) as stream:
            if stream_cb:
                for text in stream.text_stream:
                    stream_cb(text)
            else:
                for _ in stream.text_stream:
                    pass
            return self._collect(stream.get_final_message())
