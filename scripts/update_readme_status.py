#!/usr/bin/env python3
"""Refresh the auto-generated status line in README.md from the live code.

Run by CI on release (and usable locally). Replaces the content between
<!-- AUTOGEN:STATUS --> markers with the current version, tool count, provider
count, and test count.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))


def _version() -> str:
    text = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else "?"


def _tool_count() -> int:
    from euron_agent.tool_schemas import TOOL_SCHEMAS

    return len(TOOL_SCHEMAS)


def _provider_count() -> int:
    from euron_agent.config import BUILTIN_PROVIDERS

    return len(BUILTIN_PROVIDERS)


def _test_count() -> int:
    n = 0
    for f in (BACKEND / "tests").glob("test_*.py"):
        n += len(re.findall(r"^def test_", f.read_text(encoding="utf-8"), re.MULTILINE))
    return n


def main() -> int:
    readme = ROOT / "README.md"
    text = readme.read_text(encoding="utf-8")
    line = (
        f"**Latest: v{_version()}** · {_tool_count()} tools · "
        f"{_provider_count()} providers · {_test_count()} tests passing"
    )
    new = re.sub(
        r"<!-- AUTOGEN:STATUS -->.*?<!-- /AUTOGEN:STATUS -->",
        f"<!-- AUTOGEN:STATUS -->\n{line}\n<!-- /AUTOGEN:STATUS -->",
        text,
        flags=re.DOTALL,
    )
    if new != text:
        readme.write_text(new, encoding="utf-8")
        print("README status updated:", line)
    else:
        print("README status already current")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
