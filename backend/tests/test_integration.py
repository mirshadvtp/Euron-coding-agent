"""End-to-end integration tests.

Drives the REAL FastAPI server + WebSocket protocol + agent loop, with a
deterministic scripted LLM injected via monkeypatch (so no API key/network is
needed). Also covers REST, auth, and (network-permitting) the live web tools.
"""
import os

import pytest
from fastapi.testclient import TestClient

from euron_agent.llm import LLMResponse, ToolCall


class ScriptedClient:
    def __init__(self, responses):
        self.responses = list(responses)

    def chat(self, messages, tools=None, stream_cb=None, stream=True):
        if stream_cb:
            stream_cb("thinking… ")
        return self.responses.pop(0) if self.responses else LLMResponse(content="done", tool_calls=[])


def _patch_client(monkeypatch, script):
    import euron_agent.loop as loopmod
    monkeypatch.setattr(loopmod, "build_client", lambda p, a=None: ScriptedClient(list(script)))


def _drive(ws, max_events=80):
    """Collect events until 'done', auto-approving any approval requests."""
    types = []
    for _ in range(max_events):
        ev = ws.receive_json()
        types.append(ev["type"])
        if ev["type"] == "approval_request":
            ws.send_json({"type": "approval", "id": ev["id"], "approved": True})
        if ev["type"] == "done":
            break
    return types


def test_ws_end_to_end(tmp_path, monkeypatch):
    (tmp_path / "hello.py").write_text("print('hi')\n", encoding="utf-8")
    _patch_client(monkeypatch, [
        LLMResponse(content="reading", tool_calls=[ToolCall("1", "read_file", {"path": "hello.py"})]),
        LLMResponse(content="editing", tool_calls=[
            ToolCall("2", "edit_file", {"path": "hello.py", "old_string": "hi", "new_string": "hello"})]),
        LLMResponse(content="all done", tool_calls=[]),
    ])
    from euron_agent.server import app
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "init", "workspace_path": str(tmp_path), "provider": "openai", "api_key": "x"})
        assert ws.receive_json()["type"] == "status"
        ws.send_json({"type": "run", "task": "make it say hello"})
        types = _drive(ws)
    assert "tool_start" in types and "diff" in types and "tool_result" in types
    assert "usage" in types and "done" in types
    assert (tmp_path / "hello.py").read_text() == "print('hello')\n"


def test_ws_undo_and_cancel(tmp_path, monkeypatch):
    (tmp_path / "f.txt").write_text("orig\n", encoding="utf-8")
    _patch_client(monkeypatch, [
        LLMResponse(content="", tool_calls=[
            ToolCall("1", "write_file", {"path": "f.txt", "content": "new\n"})]),
        LLMResponse(content="done", tool_calls=[]),
    ])
    from euron_agent.server import app
    client = TestClient(app)
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "init", "workspace_path": str(tmp_path), "provider": "openai", "api_key": "x"})
        ws.receive_json()
        ws.send_json({"type": "run", "task": "change it"})
        _drive(ws)
        assert (tmp_path / "f.txt").read_text() == "new\n"
        ws.send_json({"type": "undo"})
        info = ws.receive_json()
        assert info["type"] == "info"
    assert (tmp_path / "f.txt").read_text() == "orig\n"


def test_rest_run(tmp_path, monkeypatch):
    _patch_client(monkeypatch, [
        LLMResponse(content="", tool_calls=[
            ToolCall("1", "create_file", {"path": "made.py", "content": "x=1\n"})]),
        LLMResponse(content="created it", tool_calls=[]),
    ])
    from euron_agent.server import app
    client = TestClient(app)
    r = client.post("/agent/run", json={
        "task": "create made.py", "workspace_path": str(tmp_path), "auto_approve": True})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["final"] == "created it"
    assert (tmp_path / "made.py").exists()


def test_auth_enforced(monkeypatch):
    from euron_agent import server
    monkeypatch.setattr(server.app.state, "token", "secret")
    client = TestClient(server.app)
    # REST without token -> 401
    assert client.post("/agent/run", json={"task": "x", "workspace_path": "."}).status_code == 401
    # WS with bad token -> error
    with client.websocket_connect("/ws") as ws:
        ws.send_json({"type": "init", "workspace_path": ".", "token": "nope"})
        assert ws.receive_json()["type"] == "error"


def test_health_and_providers():
    from euron_agent.server import app
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    provs = client.get("/providers").json()["providers"]
    assert "euri" in provs and "openai" in provs


# --------------------------------------------------------------------------- #
# Live web tools (network) — skipped automatically when offline.
# --------------------------------------------------------------------------- #
@pytest.mark.skipif(os.getenv("EURON_SKIP_NET") == "1", reason="network disabled")
def test_web_fetch_live():
    from euron_agent.webtools import web_fetch
    ok, text = web_fetch("https://example.com")
    if not ok:
        pytest.skip("network unavailable")
    assert "example" in text.lower()


@pytest.mark.skipif(os.getenv("EURON_SKIP_NET") == "1", reason="network disabled")
def test_web_search_live():
    from euron_agent.webtools import web_search
    ok, text = web_search("python programming language", provider="duckduckgo")
    if not ok or text == "(no results)":
        pytest.skip("search backend unavailable")
    assert "http" in text
