"""Test suite for the Euron Agent backend.

Run from backend/:  pytest -q
Uses a scripted FakeClient so no network/API key is needed.
"""
import asyncio
import json
from pathlib import Path

import pytest

from euron_agent.checkpoints import Checkpointer
from euron_agent.config import AgentConfig, load_config
from euron_agent.context import compact_history, estimate_tokens, expand_mentions
from euron_agent.events import AgentIO, ApprovalDecision
from euron_agent.gitignore import load_gitignore_patterns
from euron_agent.llm import LLMResponse, ToolCall, _retryable, _safe_json_loads
from euron_agent.loop import AgentSession
from euron_agent import history
from euron_agent.tools import ToolContext, edit_file, execute, read_file, run_command


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def ctx_for(tmp_path) -> ToolContext:
    return ToolContext(str(tmp_path), AgentConfig(), [".env", ".git/**"])


class CollectIO(AgentIO):
    """Auto-approving IO that records every event."""

    def __init__(self, approve=True):
        self.events = []
        self.approve = approve

    def emit_sync(self, event):
        self.events.append(event)

    async def emit(self, event):
        self.events.append(event)

    async def request_approval(self, request):
        self.events.append(request)
        return ApprovalDecision(self.approve)

    def types(self):
        return [e["type"] for e in self.events]


class ScriptedClient:
    """Returns a queued list of LLMResponses, one per chat() call."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def chat(self, messages, tools=None, stream_cb=None, stream=True):
        self.calls += 1
        if stream_cb:
            stream_cb("…")
        return self.responses.pop(0)


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------- #
# config
# --------------------------------------------------------------------------- #
def test_builtin_providers_and_overrides():
    cfg = load_config(provider="euri", api_key="k", base_url="https://x/v1", model="m")
    assert cfg.provider.name == "euri"
    assert cfg.provider.api_key == "k"
    assert cfg.provider.base_url == "https://x/v1"
    assert cfg.provider.model == "m"
    assert {"euri", "openai", "ollama", "anthropic", "custom"} <= set(cfg.all_providers)


# --------------------------------------------------------------------------- #
# tools: sandbox / edit / binary / command / ignore
# --------------------------------------------------------------------------- #
def test_sandbox_blocks_escape(tmp_path):
    ctx = ctx_for(tmp_path)
    with pytest.raises(PermissionError):
        ctx.resolve("../outside.txt")


def test_edit_file_unique_and_missing(tmp_path):
    ctx = ctx_for(tmp_path)
    (tmp_path / "a.py").write_text("x = 1\nx = 1\n", encoding="utf-8")
    miss = edit_file(ctx, "a.py", "nope", "y")
    assert not miss.ok and "not found" in miss.output
    dup = edit_file(ctx, "a.py", "x = 1", "y")
    assert not dup.ok and "not unique" in dup.output
    ok = edit_file(ctx, "a.py", "x = 1", "y", replace_all=True)
    assert ok.ok and (tmp_path / "a.py").read_text() == "y\ny\n"
    assert ok.diff


def test_read_binary_refused(tmp_path):
    ctx = ctx_for(tmp_path)
    (tmp_path / "b.bin").write_bytes(b"\x00\x01\x02ELF")
    out = read_file(ctx, "b.bin")
    assert not out.ok and "binary" in out.output


def test_run_command_streams(tmp_path):
    ctx = ctx_for(tmp_path)
    chunks = []
    out = run_command(ctx, "echo hello", on_output=chunks.append)
    assert out.ok
    assert "hello" in out.output
    assert any("hello" in c for c in chunks)


def test_ignore_blocks_env(tmp_path):
    ctx = ctx_for(tmp_path)
    (tmp_path / ".env").write_text("SECRET=1", encoding="utf-8")
    out = read_file(ctx, ".env")
    assert not out.ok


def test_execute_unknown_tool(tmp_path):
    out = execute(ctx_for(tmp_path), "nope", {})
    assert not out.ok


# --------------------------------------------------------------------------- #
# gitignore
# --------------------------------------------------------------------------- #
def test_gitignore_parsing(tmp_path):
    (tmp_path / ".gitignore").write_text("node_modules/\n*.log\n!keep.log\n", encoding="utf-8")
    pats = load_gitignore_patterns(tmp_path)
    assert "node_modules" in pats
    assert any("*.log" == p for p in pats)
    assert all(not p.startswith("!") for p in pats)


# --------------------------------------------------------------------------- #
# context management
# --------------------------------------------------------------------------- #
def test_estimate_and_compact():
    msgs = [{"role": "system", "content": "s"}]
    msgs += [{"role": "tool", "tool_call_id": str(i), "content": "x" * 4000} for i in range(10)]
    msgs += [{"role": "user", "content": "recent"}]
    assert estimate_tokens(msgs) > 1000
    out, changed = compact_history(msgs, max_tokens=500, keep_recent=2)
    assert changed
    assert estimate_tokens(out) < estimate_tokens(msgs)
    assert out[0]["content"] == "s"  # system preserved


def test_expand_mentions(tmp_path):
    ctx = ctx_for(tmp_path)
    (tmp_path / "note.txt").write_text("HELLO_CONTENT", encoding="utf-8")
    out = expand_mentions("look at @note.txt please", ctx)
    assert "HELLO_CONTENT" in out
    # non-existent mention is left as-is
    assert expand_mentions("just @nothing here", ctx) == "just @nothing here"


# --------------------------------------------------------------------------- #
# checkpoints
# --------------------------------------------------------------------------- #
def test_checkpoint_undo(tmp_path):
    cp = Checkpointer()
    f = tmp_path / "c.txt"
    f.write_text("original", encoding="utf-8")
    cp.begin_turn()
    cp.record(f)
    f.write_text("changed", encoding="utf-8")
    newfile = tmp_path / "new.txt"
    cp.record(newfile)
    newfile.write_text("created", encoding="utf-8")
    reverted = cp.undo_last_turn()
    assert f.read_text() == "original"
    assert not newfile.exists()
    assert len(reverted) == 2


# --------------------------------------------------------------------------- #
# llm helpers
# --------------------------------------------------------------------------- #
def test_safe_json_loads():
    assert _safe_json_loads('{"a":1}') == {"a": 1}
    assert _safe_json_loads("") == {}
    assert "__raw__" in _safe_json_loads("not json")


def test_retryable_classification():
    class E(Exception):
        status_code = 401

    class F(Exception):
        status_code = 500

    assert not _retryable(E())
    assert _retryable(F())
    assert _retryable(ConnectionError("boom"))


# --------------------------------------------------------------------------- #
# history persistence
# --------------------------------------------------------------------------- #
def test_history_roundtrip(tmp_path, monkeypatch):
    import euron_agent.history as h
    monkeypatch.setattr(h, "SESSIONS_DIR", tmp_path / "sessions")
    msgs = [{"role": "user", "content": "hi"}]
    h.save_history(str(tmp_path), msgs)
    assert h.load_history(str(tmp_path)) == msgs
    h.clear_history(str(tmp_path))
    assert h.load_history(str(tmp_path)) == []


# --------------------------------------------------------------------------- #
# end-to-end loop with a scripted client
# --------------------------------------------------------------------------- #
def make_session(tmp_path, responses, approve=True, **kw):
    cfg = load_config(provider="openai", api_key="x")
    io = CollectIO(approve=approve)
    sess = AgentSession(str(tmp_path), cfg, io, **kw)
    sess.client = ScriptedClient(responses)
    return sess, io


def test_loop_create_edit_run(tmp_path):
    responses = [
        LLMResponse(content="creating", tool_calls=[
            ToolCall("1", "create_file", {"path": "hi.py", "content": "print('hi')\n"})]),
        LLMResponse(content="editing", tool_calls=[
            ToolCall("2", "edit_file", {"path": "hi.py", "old_string": "hi", "new_string": "yo"})]),
        LLMResponse(content="running", tool_calls=[
            ToolCall("3", "run_command", {"command": "echo done"})]),
        LLMResponse(content="all set", tool_calls=[]),
    ]
    sess, io = make_session(tmp_path, responses)
    run(sess.run("do the thing"))
    assert (tmp_path / "hi.py").read_text() == "print('yo')\n"
    assert "done" in "".join(e.get("text", "") for e in io.events if e["type"] == "command_output")
    assert "diff" in io.types() and "done" in io.types()
    assert any(e["type"] == "usage" for e in io.events)


def test_loop_rejection(tmp_path):
    responses = [
        LLMResponse(content="", tool_calls=[
            ToolCall("1", "write_file", {"path": "x.py", "content": "bad"})]),
        LLMResponse(content="ok I won't", tool_calls=[]),
    ]
    sess, io = make_session(tmp_path, responses, approve=False)
    run(sess.run("write x"))
    assert not (tmp_path / "x.py").exists()
    assert any("REJECTED" in m.get("content", "") for m in sess.messages if m.get("role") == "tool")


def test_loop_undo(tmp_path):
    (tmp_path / "f.py").write_text("orig\n", encoding="utf-8")
    responses = [
        LLMResponse(content="", tool_calls=[
            ToolCall("1", "write_file", {"path": "f.py", "content": "new\n"})]),
        LLMResponse(content="done", tool_calls=[]),
    ]
    sess, io = make_session(tmp_path, responses)
    run(sess.run("change f"))
    assert (tmp_path / "f.py").read_text() == "new\n"
    reverted = sess.undo()
    assert (tmp_path / "f.py").read_text() == "orig\n"
    assert reverted


def test_loop_mentions_inlined(tmp_path):
    (tmp_path / "ctx.txt").write_text("MENTIONED_DATA", encoding="utf-8")
    sess, io = make_session(tmp_path, [LLMResponse(content="seen", tool_calls=[])])
    run(sess.run("use @ctx.txt"))
    user_msg = [m for m in sess.messages if m["role"] == "user"][-1]
    assert "MENTIONED_DATA" in user_msg["content"]


def test_loop_persist_roundtrip(tmp_path, monkeypatch):
    import euron_agent.sessions as s
    monkeypatch.setattr(s, "SESSIONS_DIR", tmp_path / "sessions")
    sess, io = make_session(tmp_path, [LLMResponse(content="hello", tool_calls=[])], persist=True)
    run(sess.run("hi there"))
    # a new persisted session for the same workspace resumes the latest one
    sess2, _ = make_session(tmp_path, [LLMResponse(content="again", tool_calls=[])], persist=True)
    assert any(m.get("content") == "hello" for m in sess2.messages)


def test_sessions_list_and_search(tmp_path, monkeypatch):
    import euron_agent.sessions as s
    monkeypatch.setattr(s, "SESSIONS_DIR", tmp_path / "sessions")
    sid = s.new_id()
    s.save(sid, str(tmp_path), [{"role": "user", "content": "fix the LOGIN bug"}])
    rows = s.list_sessions(str(tmp_path))
    assert rows and rows[0]["title"].startswith("fix the LOGIN")
    assert s.latest_id(str(tmp_path)) == sid
    hits = s.search("login", str(tmp_path))
    assert hits and hits[0]["id"] == sid


# --------------------------------------------------------------------------- #
# new tools: glob / multi_edit / background / git / web
# --------------------------------------------------------------------------- #
def test_glob(tmp_path):
    from euron_agent.tools import glob_files
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y", encoding="utf-8")
    out = glob_files(ctx_for(tmp_path), "**/*.py")
    assert "src/a.py" in out.output


def test_multi_edit_atomic(tmp_path):
    from euron_agent.tools import multi_edit
    ctx = ctx_for(tmp_path)
    f = tmp_path / "m.py"
    f.write_text("a\nb\nc\n", encoding="utf-8")
    ok = multi_edit(ctx, "m.py", [
        {"old_string": "a", "new_string": "x"},
        {"old_string": "c", "new_string": "z"},
    ])
    assert ok.ok and f.read_text() == "x\nb\nz\n"
    # atomic: a failing edit leaves the file untouched
    f.write_text("a\nb\n", encoding="utf-8")
    bad = multi_edit(ctx, "m.py", [
        {"old_string": "a", "new_string": "x"},
        {"old_string": "NOPE", "new_string": "z"},
    ])
    assert not bad.ok and f.read_text() == "a\nb\n"


def test_background_manager(tmp_path):
    import time
    from euron_agent.background import BackgroundManager
    m = BackgroundManager()
    pid = m.start(str(tmp_path), "echo hello")
    time.sleep(0.5)
    out = m.output(pid)
    assert "hello" in out or "exited" in out
    assert pid in m.list_()
    m.kill(pid)


def test_git_tools(tmp_path):
    import subprocess
    for c in ("git init -q", "git config user.email t@t.com", "git config user.name t"):
        subprocess.run(c, cwd=tmp_path, shell=True)
    (tmp_path / "f.txt").write_text("hi", encoding="utf-8")
    from euron_agent.tools import git_status, git_commit
    ctx = ctx_for(tmp_path)
    assert "f.txt" in git_status(ctx).output
    assert git_commit(ctx, "init").ok


def test_web_helpers(monkeypatch):
    import euron_agent.webtools as wt
    assert "hello" in wt._strip_html("<p>hello <b>world</b></p>")
    ok, text = wt._fmt([{"title": "T", "url": "http://x", "snippet": "S"}])
    assert ok and "T" in text and "http://x" in text

    class R:
        status_code = 200
        headers = {"content-type": "text/html"}
        text = "<html><body>PAGE_CONTENT</body></html>"

        def raise_for_status(self):
            pass

    monkeypatch.setattr(wt.httpx, "get", lambda *a, **k: R())
    ok, text = wt.web_fetch("http://x")
    assert ok and "PAGE_CONTENT" in text


# --------------------------------------------------------------------------- #
# meta tools: todo / plan mode / sub-agents / compact / mcp
# --------------------------------------------------------------------------- #
def test_todo_write(tmp_path):
    responses = [
        LLMResponse(content="", tool_calls=[ToolCall("1", "todo_write", {"todos": [
            {"content": "a", "status": "in_progress"},
            {"content": "b", "status": "pending"}]})]),
        LLMResponse(content="ok", tool_calls=[]),
    ]
    sess, io = make_session(tmp_path, responses)
    run(sess.run("multi step"))
    assert any(e["type"] == "todos" for e in io.events)
    assert sess.todos and sess.todos[0]["content"] == "a"


def test_plan_mode_approval(tmp_path):
    responses = [
        LLMResponse(content="here's my plan", tool_calls=[
            ToolCall("1", "update_plan", {"plan": "1. do x\n2. do y"})]),
        LLMResponse(content="implementing", tool_calls=[]),
    ]
    sess, io = make_session(tmp_path, responses, approve=True)
    sess.plan_mode = True
    run(sess.run("build feature"))
    assert any(e["type"] == "plan" for e in io.events)
    assert sess.plan_mode is False  # approved → plan mode turns off


def test_subagent(tmp_path, monkeypatch):
    import euron_agent.loop as loopmod
    monkeypatch.setattr(
        loopmod, "build_client",
        lambda p, a=None: ScriptedClient([LLMResponse(content="sub-agent analysis result", tool_calls=[])]),
    )
    parent = [
        LLMResponse(content="", tool_calls=[ToolCall("1", "spawn_agent", {
            "description": "analyze", "prompt": "analyze the repo"})]),
        LLMResponse(content="done", tool_calls=[]),
    ]
    cfg = load_config(provider="openai", api_key="x")
    io = CollectIO()
    sess = AgentSession(str(tmp_path), cfg, io)
    sess.client = ScriptedClient(parent)
    run(sess.run("delegate"))
    assert any(e["type"] == "subagent_start" for e in io.events)
    assert any(e["type"] == "subagent_end" for e in io.events)
    assert any("analysis result" in (m.get("content") or "")
               for m in sess.messages if m.get("role") == "tool")


def test_compact_summarize():
    from euron_agent.context import summarize_history
    client = ScriptedClient([LLMResponse(content="SUMMARY_BRIEF", tool_calls=[])])
    msgs = [{"role": "system", "content": "s"}]
    for _ in range(5):
        msgs.append({"role": "user", "content": "do x"})
        msgs.append({"role": "assistant", "content": "did x"})
    new, changed = summarize_history(client, msgs, keep_recent=2)
    assert changed
    assert any("SUMMARY_BRIEF" in (m.get("content") or "") for m in new)
    assert new[0]["content"] == "s"


def test_mcp_routing(tmp_path):
    class FakeMCP:
        started = True
        errors = []

        def schemas(self):
            return [{"type": "function", "function": {
                "name": "mcp__srv__do", "description": "", "parameters": {"type": "object"}}}]

        async def start(self):
            pass

        async def call(self, name, args):
            return f"mcp ok {name}"

    responses = [
        LLMResponse(content="", tool_calls=[ToolCall("1", "mcp__srv__do", {"x": 1})]),
        LLMResponse(content="done", tool_calls=[]),
    ]
    sess, io = make_session(tmp_path, responses, approve=True)
    sess.mcp = FakeMCP()
    run(sess.run("use mcp"))
    assert any("mcp ok" in (m.get("content") or "")
               for m in sess.messages if m.get("role") == "tool")


# --------------------------------------------------------------------------- #
# 0.4.0: permissions / hooks / memory / commands / pricing / multimodal
# --------------------------------------------------------------------------- #
def test_permissions_rules(tmp_path, monkeypatch):
    import euron_agent.permissions as pm
    monkeypatch.setattr(pm, "PERMISSIONS_FILE", tmp_path / "x.json")
    perms = pm.Permissions.from_config(
        {"deny": ["run_command(rm -rf*)"], "allow": ["read_file(**)"]},
        auto_writes=False, auto_commands=False,
    )
    assert perms.decide("run_command", {"command": "rm -rf /"}) == "deny"
    assert perms.decide("read_file", {"path": "x"}) == "allow"
    assert perms.decide("run_command", {"command": "ls"}) == "ask"
    assert perms.decide("write_file", {"path": "x"}) == "ask"


def test_always_allow(tmp_path, monkeypatch):
    import euron_agent.permissions as pm
    monkeypatch.setattr(pm, "PERMISSIONS_FILE", tmp_path / "p.json")
    p = pm.Permissions(default_writes="ask")
    assert p.decide("write_file", {"path": "a.py"}) == "ask"
    p.add_always_allow("write_file", {"path": "a.py"})
    assert p.decide("write_file", {"path": "a.py"}) == "allow"


def test_loop_permission_deny(tmp_path, monkeypatch):
    import euron_agent.permissions as pm
    monkeypatch.setattr(pm, "PERMISSIONS_FILE", tmp_path / "pd.json")
    (tmp_path / "d.txt").write_text("x", encoding="utf-8")
    cfg = load_config(provider="openai", api_key="x")
    cfg.permissions = {"deny": ["delete_file(**)"]}
    io = CollectIO()
    sess = AgentSession(str(tmp_path), cfg, io)
    sess.client = ScriptedClient([
        LLMResponse(content="", tool_calls=[ToolCall("1", "delete_file", {"path": "d.txt"})]),
        LLMResponse(content="ok", tool_calls=[]),
    ])
    run(sess.run("delete it"))
    assert (tmp_path / "d.txt").exists()
    assert any("Denied" in (m.get("content") or "")
               for m in sess.messages if m.get("role") == "tool")


def test_loop_hook_blocks(tmp_path, monkeypatch):
    import euron_agent.permissions as pm
    monkeypatch.setattr(pm, "PERMISSIONS_FILE", tmp_path / "ph.json")
    cfg = load_config(provider="openai", api_key="x")
    cfg.agent.auto_approve_writes = True
    cfg.hooks = {"PreToolUse": [{"matcher": "write_file", "command": "exit 3"}]}
    io = CollectIO()
    sess = AgentSession(str(tmp_path), cfg, io)
    sess.client = ScriptedClient([
        LLMResponse(content="", tool_calls=[ToolCall("1", "write_file", {"path": "h.txt", "content": "x"})]),
        LLMResponse(content="ok", tool_calls=[]),
    ])
    run(sess.run("write h"))
    assert not (tmp_path / "h.txt").exists()
    assert any("Blocked by PreToolUse" in (m.get("content") or "")
               for m in sess.messages if m.get("role") == "tool")


def test_memory(tmp_path, monkeypatch):
    import euron_agent.memory as mem
    monkeypatch.setattr(mem, "USER_FILE", tmp_path / "nouser.md")
    (tmp_path / "AGENTS.md").write_text("PROJECT_RULE_X", encoding="utf-8")
    assert "PROJECT_RULE_X" in mem.load_memory(str(tmp_path))
    sub = tmp_path / "proj"
    sub.mkdir()
    p = mem.write_template(str(sub))
    assert p.exists() and "Project memory" in p.read_text()


def test_custom_commands(tmp_path):
    from euron_agent.commands import expand_command, load_commands
    d = tmp_path / ".euron" / "commands"
    d.mkdir(parents=True)
    (d / "review.md").write_text("Review $ARGUMENTS for bugs. First: $1", encoding="utf-8")
    cmds = load_commands(str(tmp_path))
    assert "review" in cmds
    out = expand_command(cmds["review"], "file.py extra")
    assert "file.py extra" in out and "First: file.py" in out


def test_pricing():
    from euron_agent.pricing import cost_for
    assert abs(cost_for("gpt-4o-mini", 1_000_000, 0) - 0.15) < 1e-9
    assert cost_for("unknown-model", 1000, 1000) == 0.0
    assert cost_for("qwen2.5-coder:7b", 1000, 1000) == 0.0


def test_multimodal(tmp_path):
    sess, io = make_session(tmp_path, [LLMResponse(content="saw it", tool_calls=[])])
    run(sess.run("describe", images=["data:image/png;base64,AAAA"]))
    user = [m for m in sess.messages if m["role"] == "user"][-1]
    assert isinstance(user["content"], list)
    assert any(b.get("type") == "image_url" for b in user["content"])
    # usage event now carries a cost field
    assert any("session_cost" in e for e in io.events if e["type"] == "usage")


def test_anthropic_image_conversion():
    from euron_agent.llm import AnthropicClient
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}}]}]
    _system, conv = AnthropicClient._to_anthropic_messages(msgs)
    blocks = conv[0]["content"]
    assert any(b["type"] == "image" for b in blocks)


# --------------------------------------------------------------------------- #
# 0.5.0: skills / fallback / worktrees / usage
# --------------------------------------------------------------------------- #
def test_skills(tmp_path):
    from euron_agent.skills import load_skills, skills_summary
    d = tmp_path / ".euron" / "skills" / "deploy"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\ndescription: Deploy the app\n---\nRun make deploy.",
                                encoding="utf-8")
    skills = load_skills(str(tmp_path))
    assert skills["deploy"]["description"] == "Deploy the app"
    assert "deploy" in skills_summary(skills)


def test_loop_use_skill(tmp_path):
    d = tmp_path / ".euron" / "skills" / "hello"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text("---\ndescription: say hi\n---\nSECRET_PLAYBOOK", encoding="utf-8")
    sess, io = make_session(tmp_path, [
        LLMResponse(content="", tool_calls=[ToolCall("1", "use_skill", {"name": "hello"})]),
        LLMResponse(content="done", tool_calls=[]),
    ])
    run(sess.run("use the hello skill"))
    assert any("SECRET_PLAYBOOK" in (m.get("content") or "")
               for m in sess.messages if m.get("role") == "tool")


def test_fallback_client():
    from euron_agent.llm import FallbackClient, LLMError, LLMResponse

    class Boom:
        provider = None

        def chat(self, *a, **k):
            raise LLMError("primary down")

    class Good:
        provider = None

        def chat(self, *a, **k):
            return LLMResponse(content="fallback worked")

    fc = FallbackClient([Boom(), Good()])
    assert fc.chat([], None, None, False).content == "fallback worked"


def test_worktree(tmp_path):
    import subprocess
    for c in ("git init -q", "git config user.email t@t.com", "git config user.name t"):
        subprocess.run(c, cwd=tmp_path, shell=True)
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    subprocess.run("git add -A && git commit -q -m init", cwd=tmp_path, shell=True)
    from euron_agent.tools import worktree_add, worktree_list, worktree_remove
    ctx = ctx_for(tmp_path)
    assert worktree_add(ctx, "feature", "feat-branch").ok
    assert "feature" in worktree_list(ctx).output
    assert worktree_remove(ctx, "feature").ok


def test_usage_tracking(tmp_path):
    sess, io = make_session(tmp_path, [
        LLMResponse(content="", tool_calls=[ToolCall("1", "list_files", {})]),
        LLMResponse(content="done", tool_calls=[]),
    ])
    run(sess.run("list the files"))
    assert sess.tool_calls["list_files"] == 1


# --------------------------------------------------------------------------- #
# 0.6.0: plugins
# --------------------------------------------------------------------------- #
def test_plugins(tmp_path, monkeypatch):
    import euron_agent.plugins as pl
    monkeypatch.setattr(pl, "PLUGINS_DIR", tmp_path / "plugins")
    src = tmp_path / "myplug"
    (src / "skills" / "foo").mkdir(parents=True)
    (src / "commands").mkdir(parents=True)
    (src / "euron-plugin.yaml").write_text(
        "name: myplug\ndescription: test plugin\nmcp:\n  servers:\n    s1:\n      command: echo\n",
        encoding="utf-8")
    (src / "skills" / "foo" / "SKILL.md").write_text(
        "---\ndescription: foo skill\n---\nbody", encoding="utf-8")
    (src / "commands" / "hi.md").write_text("say hi to $ARGUMENTS", encoding="utf-8")

    assert pl.install(str(src)) == "myplug"
    assert any(p["name"] == "myplug" for p in pl.list_plugins())
    assert "s1" in pl.plugin_mcp_servers()

    from euron_agent.commands import load_commands
    from euron_agent.skills import load_skills
    assert "foo" in load_skills(str(tmp_path / "ws"))
    assert "hi" in load_commands(str(tmp_path / "ws"))
    assert pl.remove("myplug")


# --------------------------------------------------------------------------- #
# 1.0.0: providers / teams / schedules / notify / headless
# --------------------------------------------------------------------------- #
def test_many_providers():
    cfg = load_config()
    expected = {"gemini", "groq", "cerebras", "deepseek", "together", "mistral", "xai", "vercel", "lmstudio"}
    assert expected <= set(cfg.all_providers)
    g = load_config(provider="gemini")
    assert "generativelanguage" in (g.provider.base_url or "")


def test_team_id():
    from euron_agent.teams import team_id
    assert team_id("Auth Sprint!") == "team-auth-sprint"


def test_cron_match():
    from datetime import datetime
    from euron_agent.schedules import cron_match
    dt = datetime(2026, 6, 15, 9, 0)
    assert cron_match("* * * * *", dt)
    assert cron_match("0 9 * * *", dt)
    assert not cron_match("30 9 * * *", dt)
    assert cron_match("0 9 15 6 *", dt)
    assert not cron_match("0 9 16 6 *", dt)
    assert cron_match("0 9 * * MON-FRI", dt) == (dt.weekday() < 5)
    assert cron_match("*/15 * * * *", datetime(2026, 6, 15, 9, 30))


def test_schedules_crud(tmp_path, monkeypatch):
    from datetime import datetime
    import euron_agent.schedules as sc
    monkeypatch.setattr(sc, "SCHEDULES_FILE", tmp_path / "sch.json")
    s = sc.create("daily", "* * * * *", "do it", str(tmp_path))
    assert sc.list_schedules()[0]["name"] == "daily"
    assert sc.get(s["id"])["cron"] == "* * * * *"
    now = datetime(2026, 6, 15, 9, 0)
    assert sc.due(now)
    sc.mark_run(s["id"], now.strftime("%Y-%m-%d %H:%M"))
    assert not sc.due(now)
    assert sc.remove(s["id"])


def test_notify_dispatch(monkeypatch):
    import httpx
    import euron_agent.notify as nt
    monkeypatch.setattr(httpx, "post", lambda *a, **k: type("R", (), {"status_code": 200})())
    assert nt.send_slack("http://x", "hi")
    assert nt.dispatch({"slack_webhook": "http://x", "discord_webhook": "http://y"}, "hi") == ["slack", "discord"]
    assert nt.dispatch({}, "hi") == []


def test_loop_notify(tmp_path, monkeypatch):
    sent = []
    import euron_agent.notify as nt
    monkeypatch.setattr(nt, "dispatch", lambda cfg, text: sent.append(text) or ["slack"])
    cfg = load_config(provider="openai", api_key="x")
    cfg.notifications = {"on": ["done"], "slack_webhook": "http://x"}
    io = CollectIO()
    sess = AgentSession(str(tmp_path), cfg, io)
    sess.client = ScriptedClient([LLMResponse(content="all done", tool_calls=[])])
    run(sess.run("hi"))
    assert sent and "all done" in sent[0]


def test_headless_run(tmp_path, monkeypatch):
    import euron_agent.loop as loopmod
    monkeypatch.setattr(loopmod, "build_client",
                        lambda p, a=None: ScriptedClient([LLMResponse(content="headless ok", tool_calls=[])]))
    from euron_agent.headless import run_headless
    res = run(run_headless("do x", str(tmp_path)))
    assert res["final"] == "headless ok"


def test_team_mode(tmp_path, monkeypatch):
    import euron_agent.sessions as s
    monkeypatch.setattr(s, "SESSIONS_DIR", tmp_path / "sessions")
    cfg = load_config(provider="openai", api_key="x")
    io = CollectIO()
    sess = AgentSession(str(tmp_path), cfg, io, team="auth-sprint")
    sess.client = ScriptedClient([LLMResponse(content="coordinated", tool_calls=[])])
    run(sess.run("plan auth"))
    assert sess.session_id == "team-auth-sprint"
    # team coordinator instructions injected into the system prompt
    assert any("COORDINATOR" in (m.get("content") or "") for m in sess.messages if m.get("role") == "system")
