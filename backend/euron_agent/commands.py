"""Custom slash commands loaded from markdown files.

Files in `.euron/commands/*.md` (project) or `~/.euron-agent/commands/*.md`
(global) become `/name` commands. The file body is the prompt template;
`$ARGUMENTS` is replaced with everything after the command, and `$1`, `$2`, … with
individual whitespace-separated args.

Example `.euron/commands/review.md`:
    Review the changes in $ARGUMENTS for bugs and suggest fixes.
"""
from __future__ import annotations

from pathlib import Path

from .settings import SETTINGS_DIR


def _dirs(workspace: str) -> list[Path]:
    from . import plugins

    return [SETTINGS_DIR / "commands", *plugins.plugin_command_dirs(),
            Path(workspace) / ".euron" / "commands"]


def load_commands(workspace: str) -> dict[str, str]:
    cmds: dict[str, str] = {}
    for d in _dirs(workspace):
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                try:
                    cmds[f.stem] = f.read_text(encoding="utf-8")
                except Exception:
                    continue
    return cmds


def expand_command(body: str, args: str) -> str:
    parts = args.split()
    out = body.replace("$ARGUMENTS", args)
    for i, p in enumerate(parts, 1):
        out = out.replace(f"${i}", p)
    return out
