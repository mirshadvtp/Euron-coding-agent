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
  * BedrockClient       — native Amazon Bedrock Converse API (streaming).
"""
from __future__ import annotations

import json
import os
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


def _make_client(provider: ProviderConfig, agent: Optional[AgentConfig] = None):
    attempts = agent.retry_attempts if agent else 3
    backoff = agent.retry_backoff if agent else 1.5
    thinking = agent.thinking if agent else False
    effort = agent.reasoning_effort if agent else None
    if provider.type == "anthropic":
        return AnthropicClient(provider, attempts, backoff, thinking=thinking)
    if provider.type == "bedrock":
        return BedrockClient(provider, attempts, backoff)
    return OpenAICompatClient(provider, attempts, backoff, reasoning_effort=effort)


def build_client(provider: ProviderConfig, agent: Optional[AgentConfig] = None):
    primary = _make_client(provider, agent)
    fallbacks = (agent.fallback_models if agent else []) or []
    if not fallbacks:
        return primary
    from dataclasses import replace

    clients = [primary] + [_make_client(replace(provider, model=m), agent) for m in fallbacks]
    return FallbackClient(clients)


class FallbackClient:
    """Tries each underlying client in order; falls through on LLMError."""

    def __init__(self, clients: list):
        self.clients = clients
        self.provider = clients[0].provider

    def chat(self, messages, tools=None, stream_cb=None, stream=True) -> "LLMResponse":
        last: Optional[Exception] = None
        for client in self.clients:
            try:
                return client.chat(messages, tools, stream_cb, stream)
            except LLMError as e:
                last = e
                continue
        raise last if last else LLMError("no clients available")


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
    if code is None:
        resp = getattr(e, "response", None)
        if isinstance(resp, dict):
            code = (resp.get("ResponseMetadata") or {}).get("HTTPStatusCode")
    if code in (400, 401, 403, 404, 422):
        return False
    return True


def _detect_param_fix(errmsg: str, kwargs: dict) -> Optional[str]:
    """Inspect a 400 'unsupported parameter' error and decide how to adapt the
    request so it works with newer models (gpt-5.x, o-series, etc.) - generically,
    with no per-model configuration."""
    m = errmsg.lower()
    if "max_completion_tokens" in m and "max_tokens" in m and "max_tokens" in kwargs:
        return "max_completion_tokens"
    if "temperature" in m and "max_tokens" not in m and any(
        s in m for s in ("unsupported", "not supported", "does not support",
                         "only the default", "only supports", "is not supported")
    ):
        return "drop_temperature"
    return None


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
        self._compat: set = set()  # learned param fixes (e.g. max_completion_tokens)
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

    def _apply_compat(self, kwargs: dict) -> dict:
        k = dict(kwargs)
        if "max_completion_tokens" in self._compat and "max_tokens" in k:
            k["max_completion_tokens"] = k.pop("max_tokens")
        if "drop_temperature" in self._compat:
            k.pop("temperature", None)
        return k

    def _completions_create(self, kwargs: dict):
        """Call the API, auto-adapting unsupported parameters (and remembering the
        fix so later calls in this session skip the failed attempt)."""
        last: Optional[Exception] = None
        for _ in range(4):
            attempt = self._apply_compat(kwargs)
            try:
                return self.client.chat.completions.create(**attempt)
            except Exception as e:  # noqa: BLE001
                fix = _detect_param_fix(str(e), attempt)
                if not fix or fix in self._compat:
                    raise
                self._compat.add(fix)
                last = e
        if last:
            raise last

    def _chat_once(self, kwargs: dict) -> LLMResponse:
        resp = self._completions_create(kwargs)
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
        content_parts: list[str] = []
        partial: dict[int, dict] = {}
        for chunk in self._completions_create({**kwargs, "stream": True}):
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
        # Prompt caching: the system prompt (repo map, AGENTS.md, tool docs) is large
        # and static across a turn — mark it ephemeral so Anthropic reuses it from
        # cache instead of re-billing the full prefix every step.
        system_field: Any = system
        if system and len(system) > 2000:
            system_field = [{
                "type": "text", "text": system,
                "cache_control": {"type": "ephemeral"},
            }]
        kwargs: dict[str, Any] = {
            "model": self.provider.model,
            "system": system_field,
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


# --------------------------------------------------------------------------- #
# Amazon Bedrock (Converse API)
# --------------------------------------------------------------------------- #
def bedrock_bearer_token(provider: ProviderConfig) -> Optional[str]:
    """Bedrock API key (ABSK…) from settings or AWS_BEARER_TOKEN_BEDROCK."""
    raw = provider.api_key or os.getenv("AWS_BEARER_TOKEN_BEDROCK") or ""
    raw = raw.strip()
    if not raw:
        return None
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    # IAM access keys are not bearer tokens.
    if raw.startswith("AKIA") or ":" in raw:
        return None
    return raw


def bedrock_iam_credentials(provider: ProviderConfig) -> Optional[tuple[str, str, Optional[str]]]:
    """(access_key_id, secret_access_key, session_token) when using IAM."""
    access = (provider.api_key or os.getenv("AWS_ACCESS_KEY_ID") or "").strip()
    secret = (provider.api_secret or os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
    token = os.getenv("AWS_SESSION_TOKEN")
    if access.lower().startswith("bearer "):
        access = access[7:].strip()
    if access.startswith("ABSK"):
        return None
    if ":" in access and not secret:
        access, secret = access.split(":", 1)
    if access and secret:
        return access, secret, token
    return None


def bedrock_credentials_ready(provider: ProviderConfig) -> bool:
    if bedrock_bearer_token(provider):
        return True
    if bedrock_iam_credentials(provider):
        return True
    if os.getenv("AWS_BEARER_TOKEN_BEDROCK"):
        return True
    try:
        import boto3
        return boto3.Session().get_credentials() is not None
    except Exception:
        return False


class BedrockClient(_RetryMixin):
    """Native Amazon Bedrock client using the Converse / ConverseStream APIs."""

    def __init__(self, provider: ProviderConfig, attempts: int = 3, backoff: float = 1.5):
        try:
            import boto3
        except ImportError as e:
            raise LLMError(
                "boto3 is required for the bedrock provider. "
                "Install with: pip install 'euron-coding-agent[bedrock]'"
            ) from e

        self.provider = provider
        self.retry_attempts = attempts
        self.retry_backoff = backoff
        region = (
            provider.region
            or os.getenv("AWS_REGION")
            or os.getenv("AWS_DEFAULT_REGION")
            or "us-east-1"
        )
        client_kwargs: dict[str, Any] = {
            "service_name": "bedrock-runtime",
            "region_name": region,
        }
        if provider.base_url:
            client_kwargs["endpoint_url"] = provider.base_url

        bearer = bedrock_bearer_token(provider)
        iam = bedrock_iam_credentials(provider)
        if bearer:
            # boto3 reads AWS_BEARER_TOKEN_BEDROCK (no per-client param yet).
            os.environ["AWS_BEARER_TOKEN_BEDROCK"] = bearer
            self.client = boto3.client(**client_kwargs)
        elif iam:
            access, secret, token = iam
            session_kwargs: dict[str, Any] = {
                "aws_access_key_id": access,
                "aws_secret_access_key": secret,
                "region_name": region,
            }
            if token:
                session_kwargs["aws_session_token"] = token
            self.client = boto3.Session(**session_kwargs).client(
                "bedrock-runtime",
                endpoint_url=provider.base_url or None,
            )
        else:
            self.client = boto3.client(**client_kwargs)

    @staticmethod
    def _to_bedrock_tools(tools):
        out = []
        for t in tools or []:
            fn = t.get("function", {})
            out.append({
                "toolSpec": {
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "inputSchema": {"json": fn.get("parameters", {"type": "object"})},
                }
            })
        return out

    @staticmethod
    def _image_block(url: str) -> Optional[dict]:
        if not url.startswith("data:") or "," not in url:
            return None
        header, data = url.split(",", 1)
        media = header.split(";")[0].split(":")[-1]
        fmt = media.split("/")[-1].lower()
        if fmt == "jpg":
            fmt = "jpeg"
        if fmt not in ("png", "jpeg", "gif", "webp"):
            return None
        import base64

        try:
            raw = base64.b64decode(data, validate=False)
        except Exception:
            return None
        return {
            "image": {
                "format": fmt,
                "source": {"bytes": raw},
            }
        }

    @staticmethod
    def _to_bedrock_messages(messages):
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
                            push("user", {"text": block.get("text", "")})
                        elif block.get("type") == "image_url":
                            img = BedrockClient._image_block(
                                block.get("image_url", {}).get("url", "")
                            )
                            if img:
                                push("user", img)
                else:
                    push("user", {"text": content or ""})
            elif role == "assistant":
                if m.get("content"):
                    push("assistant", {"text": m["content"]})
                for tc in m.get("tool_calls") or []:
                    fn = tc["function"]
                    push("assistant", {
                        "toolUse": {
                            "toolUseId": tc["id"],
                            "name": fn["name"],
                            "input": _safe_json_loads(fn.get("arguments") or "{}"),
                        }
                    })
            elif role == "tool":
                push("user", {
                    "toolResult": {
                        "toolUseId": m.get("tool_call_id"),
                        "content": [{"text": m.get("content") or ""}],
                    }
                })
        system = "\n".join(p for p in system_parts if p)
        return system, out

    def _build_request(self, messages, tools):
        system, conv = self._to_bedrock_messages(messages)
        req: dict[str, Any] = {
            "modelId": self.provider.model,
            "messages": conv,
            "inferenceConfig": {
                "maxTokens": self.provider.max_tokens,
                "temperature": self.provider.temperature,
            },
        }
        if system:
            req["system"] = [{"text": system}]
        if tools:
            req["toolConfig"] = {"tools": self._to_bedrock_tools(tools)}
        return req

    def chat(self, messages, tools=None, stream_cb=None, stream=True) -> LLMResponse:
        req = self._build_request(messages, tools)

        def run(cb):
            return self._chat_stream(req, cb) if stream else self._chat_once(req)

        resp = self._with_retry(run, stream_cb)
        if not resp.prompt_tokens:
            resp.prompt_tokens = sum(
                _estimate(str(m.get("content") or "")) for m in messages
            )
        if not resp.completion_tokens:
            resp.completion_tokens = _estimate(resp.content)
        return resp

    @staticmethod
    def _collect_blocks(blocks) -> LLMResponse:
        content, calls = "", []
        for block in blocks or []:
            if "text" in block:
                content += block["text"]
            elif "toolUse" in block:
                tu = block["toolUse"]
                calls.append(ToolCall(
                    tu.get("toolUseId") or "",
                    tu.get("name") or "",
                    tu.get("input") or {},
                ))
        return LLMResponse(content=content, tool_calls=calls)

    def _chat_once(self, req: dict) -> LLMResponse:
        resp = self.client.converse(**req)
        output = resp.get("output", {}).get("message", {})
        usage = resp.get("usage") or {}
        collected = self._collect_blocks(output.get("content"))
        collected.prompt_tokens = int(usage.get("inputTokens") or 0)
        collected.completion_tokens = int(usage.get("outputTokens") or 0)
        return collected

    def _chat_stream(self, req: dict, stream_cb: StreamCallback) -> LLMResponse:
        content_parts: list[str] = []
        partial: dict[int, dict] = {}
        blocks: dict[int, dict] = {}
        usage: dict[str, int] = {}

        for event in self.client.converse_stream(**req).get("stream", []):
            if "contentBlockStart" in event:
                start = event["contentBlockStart"]
                idx = start.get("contentBlockIndex", 0)
                tool = (start.get("start") or {}).get("toolUse")
                if tool:
                    partial[idx] = {
                        "id": tool.get("toolUseId"),
                        "name": tool.get("name") or "",
                        "args": "",
                    }
            elif "contentBlockDelta" in event:
                delta = event["contentBlockDelta"]["delta"]
                idx = event["contentBlockDelta"].get("contentBlockIndex", 0)
                if "text" in delta:
                    text = delta["text"]
                    content_parts.append(text)
                    blocks.setdefault(idx, {"text": ""})
                    blocks[idx]["text"] += text
                    if stream_cb:
                        stream_cb(text)
                tool_delta = delta.get("toolUse")
                if tool_delta:
                    slot = partial.setdefault(idx, {"id": None, "name": "", "args": ""})
                    if tool_delta.get("input"):
                        slot["args"] += tool_delta["input"]
            elif "contentBlockStop" in event:
                idx = event["contentBlockStop"].get("contentBlockIndex", 0)
                slot = partial.get(idx)
                if slot and slot.get("name"):
                    blocks[idx] = {
                        "toolUse": {
                            "toolUseId": slot["id"] or f"call_{idx}",
                            "name": slot["name"],
                            "input": _safe_json_loads(slot["args"]),
                        }
                    }
            elif "metadata" in event:
                u = (event["metadata"] or {}).get("usage") or {}
                usage = {
                    "inputTokens": int(u.get("inputTokens") or 0),
                    "outputTokens": int(u.get("outputTokens") or 0),
                }

        ordered_blocks = [blocks[i] for i in sorted(blocks)]
        resp = self._collect_blocks(ordered_blocks)
        if not resp.content and content_parts:
            resp.content = "".join(content_parts)
        resp.prompt_tokens = usage.get("inputTokens", 0)
        resp.completion_tokens = usage.get("outputTokens", 0)
        return resp
