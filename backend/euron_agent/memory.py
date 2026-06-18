"""Project & user memory — persistent instructions auto-loaded into context.

Looks for memory files in the workspace (and a global one) and injects them into
the system prompt, so project conventions and standing instructions are always in
context (like Claude Code's CLAUDE.md / AGENTS.md).

Precedence (all concatenated): user-global → project.
"""
from __future__ import annotations

from pathlib import Path

from .settings import SETTINGS_DIR

PROJECT_FILES = ["AGENTS.md", "EURON.md", "CLAUDE.md", ".euron/AGENTS.md"]
USER_FILE = SETTINGS_DIR / "AGENTS.md"
_MAX = 12000


def load_memory(workspace: str) -> str:
    parts: list[str] = []
    if USER_FILE.is_file():
        try:
            parts.append("# User memory (global)\n" + USER_FILE.read_text(encoding="utf-8")[:_MAX])
        except Exception:
            pass
    root = Path(workspace)
    for name in PROJECT_FILES:
        p = root / name
        if p.is_file():
            try:
                parts.append(f"# Project memory ({name})\n" + p.read_text(encoding="utf-8")[:_MAX])
            except Exception:
                pass
            break  # first project memory file wins
    return "\n\n".join(parts)


TEMPLATE = """# Project memory for the Euron Agent

This file is automatically loaded into the agent's context. Put standing
instructions and project conventions here.

## Commands
- Build:
- Test:
- Lint:

## Conventions
- (e.g. "use 4-space indent", "prefer async", "never touch generated/")

## Notes
- (anything the agent should always know about this project)
"""


def write_template(workspace: str) -> Path:
    path = Path(workspace) / "AGENTS.md"
    if not path.exists():
        path.write_text(TEMPLATE, encoding="utf-8")
    return path
