"""`euron-agent doctor` — environment self-check.

Checks the things that commonly break a setup (Python version, package version, a
configured provider + reachable key, optional tools like git/ripgrep/gh, and the
writable user-data dir) and prints a clear PASS/WARN/FAIL report with a fix hint
for anything wrong. Read-only and safe to run anytime.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from . import __version__
from .config import Config


class Check:
    def __init__(self, name: str, status: str, detail: str = "", hint: str = ""):
        self.name = name
        self.status = status  # "pass" | "warn" | "fail"
        self.detail = detail
        self.hint = hint


def run_checks(config: Config | None = None, workspace: str = ".") -> list[Check]:
    checks: list[Check] = []

    # Python version
    v = sys.version_info
    if v >= (3, 9):
        checks.append(Check("Python", "pass", f"{v.major}.{v.minor}.{v.micro}"))
    else:
        checks.append(Check("Python", "fail", f"{v.major}.{v.minor}",
                            "Python 3.9+ is required."))

    checks.append(Check("euron-coding-agent", "pass", f"v{__version__}"))

    # Provider + key
    if config is not None:
        p = config.provider
        checks.append(Check("Active provider", "pass", f"{p.name} ({p.model})"))
        needs_key = bool(p.api_key_env)  # local providers (ollama/lmstudio) need none
        if not needs_key:
            checks.append(Check("API key", "pass", "not required (local provider)"))
        elif p.api_key:
            checks.append(Check("API key", "pass", f"set via {p.api_key_env}"))
        else:
            checks.append(Check("API key", "warn", f"{p.api_key_env} not set",
                                f"export {p.api_key_env}=… or run `/key` in chat."))
        if p.base_url:
            checks.append(Check("Base URL", "pass", p.base_url))

    # Optional external tools
    for exe, why in [
        ("git", "version control + git tools"),
        ("rg", "fast search (ripgrep)"),
        ("gh", "open pull requests"),
        ("pip-audit", "Python dependency audit"),
    ]:
        if shutil.which(exe):
            checks.append(Check(f"tool: {exe}", "pass", "found"))
        else:
            checks.append(Check(f"tool: {exe}", "warn", "not found",
                                f"optional — install for {why}."))

    # Writable user-data dir
    data_dir = Path(os.path.expanduser("~")) / ".euron-agent"
    try:
        data_dir.mkdir(parents=True, exist_ok=True)
        probe = data_dir / ".doctor_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        checks.append(Check("User data dir", "pass", str(data_dir)))
    except Exception as e:  # noqa: BLE001
        checks.append(Check("User data dir", "fail", str(e),
                            "Check permissions on your home directory."))

    # Workspace writability
    ws = Path(workspace)
    checks.append(Check("Workspace", "pass" if os.access(ws, os.W_OK) else "warn",
                        str(ws.resolve()),
                        "" if os.access(ws, os.W_OK) else "workspace is not writable."))
    return checks


def format_report(checks: list[Check]) -> str:
    icon = {"pass": "✔", "warn": "⚠", "fail": "✗"}
    lines = ["Euron Agent — environment check", ""]
    for c in checks:
        mark = icon.get(c.status, "?")
        line = f"  {mark} {c.name}: {c.detail}".rstrip()
        lines.append(line)
        if c.hint and c.status != "pass":
            lines.append(f"      ↳ {c.hint}")
    fails = sum(1 for c in checks if c.status == "fail")
    warns = sum(1 for c in checks if c.status == "warn")
    lines.append("")
    if fails:
        lines.append(f"Result: {fails} failure(s), {warns} warning(s) — fix failures above.")
    elif warns:
        lines.append(f"Result: all critical checks passed, {warns} optional warning(s).")
    else:
        lines.append("Result: all checks passed. You're good to go.")
    return "\n".join(lines)
