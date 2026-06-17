"""Command-line interface.

    euron-agent run "add a /health route to app.py"   # one-shot in cwd
    euron-agent chat                                   # interactive REPL
    euron-agent serve                                  # start the API server
    euron-agent providers                              # list configured providers
    euron-agent init                                   # scaffold config.yaml/.env

The CLI uses the very same AgentSession/loop as the VS Code backend, so the
terminal experience and the editor experience are identical.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from .config import load_config
from .events import AgentIO, ApprovalDecision
from .loop import AgentSession


def _force_utf8() -> None:
    """Avoid UnicodeEncodeError for box-drawing/emoji on Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass


_force_utf8()
# legacy_windows=False -> use ANSI (Windows 10+ supports it) instead of the
# win32 console API, which encodes with the active code page and chokes on '●'.
console = Console(legacy_windows=False)


# --------------------------------------------------------------------------- #
# Terminal IO
# --------------------------------------------------------------------------- #
class TerminalIO(AgentIO):
    def __init__(self, auto_approve: bool):
        self.auto_approve = auto_approve
        self._dirty = False  # unfinished streamed line on screen

    def _newline_if_dirty(self) -> None:
        if self._dirty:
            sys.stdout.write("\n")
            sys.stdout.flush()
            self._dirty = False

    # streamed tokens (may arrive from a worker thread)
    def on_token(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()
        self._dirty = True

    async def emit(self, event: dict) -> None:
        t = event["type"]
        if t == "status":
            return  # keep the terminal quiet; spinners would fight streaming
        if t == "assistant_message":
            # text already streamed via tokens; just close the line
            self._newline_if_dirty()
            return
        self._newline_if_dirty()
        if t == "tool_start":
            args = event["args"]
            detail = args.get("path") or args.get("command") or args.get("query") or ""
            console.print(f"[cyan]⚙ {event['name']}[/cyan] [dim]{detail}[/dim]")
        elif t == "diff":
            self._print_diff(event["patch"])
        elif t == "tool_result":
            mark = "[green]✓[/green]" if event["ok"] else "[red]✗[/red]"
            out = (event["output"] or "").strip()
            if out:
                snippet = out if len(out) < 1200 else out[:1200] + " …"
                console.print(f"{mark} [dim]{snippet}[/dim]")
        elif t == "error":
            console.print(f"[red]error:[/red] {event['message']}")
        elif t == "done":
            console.print("[dim]— done —[/dim]")

    def _print_diff(self, patch: str) -> None:
        for line in patch.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                console.print(f"[green]{line}[/green]")
            elif line.startswith("-") and not line.startswith("---"):
                console.print(f"[red]{line}[/red]")
            elif line.startswith("@@"):
                console.print(f"[magenta]{line}[/magenta]")
            else:
                console.print(f"[dim]{line}[/dim]")

    async def request_approval(self, request: dict) -> ApprovalDecision:
        self._newline_if_dirty()
        preview = request.get("preview") or ""
        title = f"Approve {request['name']}?"
        if preview:
            self._print_diff(preview) if "\n" in preview and (
                "+++" in preview or "@@" in preview
            ) else console.print(Panel(preview, title=title, border_style="yellow"))
        if self.auto_approve:
            console.print("[green]auto-approved[/green]")
            return ApprovalDecision(approved=True)

        answer = await asyncio.to_thread(
            Prompt.ask,
            f"[yellow]{title}[/yellow] (y/n, or type feedback to reject)",
            default="y",
        )
        a = answer.strip().lower()
        if a in ("y", "yes", ""):
            return ApprovalDecision(approved=True)
        if a in ("n", "no"):
            return ApprovalDecision(approved=False, feedback="rejected by user")
        return ApprovalDecision(approved=False, feedback=answer.strip())


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
async def _run_task(task: str, args) -> None:
    cfg = load_config(args.config, provider=args.provider, model=args.model)
    if args.yes:
        cfg.agent.auto_approve_writes = True
        cfg.agent.auto_approve_commands = True
    workspace = str(Path(args.workspace).resolve())
    console.print(
        f"[dim]workspace={workspace} · provider={cfg.provider.name} · "
        f"model={cfg.provider.model}[/dim]"
    )
    io = TerminalIO(auto_approve=args.yes)
    session = AgentSession(workspace, cfg, io)
    await session.run(task)


def cmd_run(args) -> None:
    asyncio.run(_run_task(args.task, args))


async def _chat(args) -> None:
    cfg = load_config(args.config, provider=args.provider, model=args.model)
    if args.yes:
        cfg.agent.auto_approve_writes = True
        cfg.agent.auto_approve_commands = True
    workspace = str(Path(args.workspace).resolve())
    io = TerminalIO(auto_approve=args.yes)
    session = AgentSession(workspace, cfg, io)  # one session => memory across turns
    console.print(
        Panel(
            f"Euron Agent · [bold]{cfg.provider.name}[/bold] / {cfg.provider.model}\n"
            f"workspace: {workspace}\n"
            "Type a task. Commands: /exit, /reset, /yes (toggle auto-approve).",
            border_style="cyan",
        )
    )
    while True:
        try:
            msg = await asyncio.to_thread(Prompt.ask, "[bold cyan]you[/bold cyan]")
        except (EOFError, KeyboardInterrupt):
            break
        msg = msg.strip()
        if not msg:
            continue
        if msg in ("/exit", "/quit"):
            break
        if msg == "/reset":
            session.messages.clear()
            console.print("[dim]context cleared[/dim]")
            continue
        if msg == "/yes":
            io.auto_approve = not io.auto_approve
            console.print(f"[dim]auto-approve = {io.auto_approve}[/dim]")
            continue
        await session.run(msg)
    console.print("[dim]bye[/dim]")


def cmd_chat(args) -> None:
    asyncio.run(_chat(args))


def cmd_serve(args) -> None:
    from .server import serve

    console.print(f"[cyan]Euron Agent server[/cyan] on http://{args.host}:{args.port}")
    serve(host=args.host, port=args.port, reload=args.reload)


def cmd_providers(args) -> None:
    from rich.table import Table

    cfg = load_config(args.config)
    table = Table(title="Configured providers")
    table.add_column("name")
    table.add_column("active")
    table.add_column("type")
    table.add_column("model")
    table.add_column("base_url")
    for name, p in cfg.all_providers.items():
        table.add_row(
            name,
            "●" if name == cfg.provider.name else "",
            p.type,
            p.model,
            p.base_url or "(default)",
        )
    console.print(table)


def cmd_init(args) -> None:
    backend = Path(__file__).resolve().parent.parent
    pairs = [("config.example.yaml", "config.yaml"), (".env.example", ".env")]
    for src, dst in pairs:
        dst_path = Path.cwd() / dst
        src_path = backend / src
        if dst_path.exists():
            console.print(f"[yellow]skip[/yellow] {dst} already exists")
        elif src_path.exists():
            shutil.copy(src_path, dst_path)
            console.print(f"[green]created[/green] {dst}")
        else:
            console.print(f"[red]missing template[/red] {src}")
    console.print("Edit config.yaml (pick a provider) and .env (add your key).")


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="euron-agent", description="Euron coding agent.")
    p.add_argument("--config", help="Path to config.yaml")
    p.add_argument("--provider", help="Override active provider profile")
    p.add_argument("--model", help="Override model id")
    p.add_argument(
        "--workspace", default=os.getcwd(), help="Workspace root (default: cwd)"
    )
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("run", help="Run a single task and exit")
    r.add_argument("task")
    r.add_argument("--yes", "-y", action="store_true", help="Auto-approve all actions")
    r.set_defaults(func=cmd_run)

    c = sub.add_parser("chat", help="Interactive REPL")
    c.add_argument("--yes", "-y", action="store_true", help="Auto-approve all actions")
    c.set_defaults(func=cmd_chat)

    s = sub.add_parser("serve", help="Start the FastAPI server")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--reload", action="store_true")
    s.set_defaults(func=cmd_serve)

    sub.add_parser("providers", help="List configured providers").set_defaults(
        func=cmd_providers
    )
    sub.add_parser("init", help="Scaffold config.yaml and .env").set_defaults(
        func=cmd_init
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        console.print("\n[dim]interrupted[/dim]")
        return 130
    except Exception as e:  # noqa: BLE001
        console.print(f"[red]fatal:[/red] {type(e).__name__}: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
