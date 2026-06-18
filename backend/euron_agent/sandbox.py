"""Per-tool command sandbox + network egress policy.

A deny-by-default safety layer for `run_command`, evaluated *before* a command
runs (and before the approval prompt). Configured under `sandbox:` in config.yaml:

    sandbox:
      block_network: true              # block curl/wget/ssh/etc. and URLs
      deny_commands: ["rm -rf /", "git push"]   # regex, always denied
      allow_commands: ["pytest", "npm (run )?test"]  # if set, ONLY these allowed

This keeps autonomous / dangerous-mode runs contained: even with every approval
auto-granted, a denied command never executes.
"""
from __future__ import annotations

import re

# Commands/binaries that perform network egress.
_NET = re.compile(
    r"(?i)(?:^|[\s;|&])(curl|wget|nc|ncat|netcat|telnet|ssh|scp|sftp|ftp|rsync|"
    r"http|https)\b|https?://|ftp://")

# Always-dangerous shapes blocked when block_network or any policy is active is
# NOT assumed; these are only used by the built-in 'strict' helper below.
_DESTRUCTIVE = [
    r"rm\s+-rf\s+/(?:\s|$)",
    r":\(\)\s*\{.*\};:",          # fork bomb
    r"mkfs\b", r"dd\s+if=.*of=/dev/",
    r">\s*/dev/sd[a-z]",
]


def check_command(sandbox: dict, command: str) -> tuple[bool, str]:
    """Return (allowed, reason). Empty/whitespace commands are allowed (no-op)."""
    if not sandbox:
        return True, ""
    cmd = (command or "").strip()
    if not cmd:
        return True, ""

    for pat in sandbox.get("deny_commands", []) or []:
        try:
            if re.search(pat, cmd):
                return False, f"blocked by sandbox.deny_commands rule: /{pat}/"
        except re.error:
            if pat in cmd:
                return False, f"blocked by sandbox.deny_commands rule: {pat!r}"

    if sandbox.get("block_network") and _NET.search(cmd):
        return False, "network egress blocked by sandbox.block_network"

    allow = sandbox.get("allow_commands") or []
    if allow:
        for pat in allow:
            try:
                if re.search(pat, cmd):
                    return True, ""
            except re.error:
                if pat in cmd:
                    return True, ""
        return False, "not in sandbox.allow_commands allowlist (deny-by-default)"

    return True, ""


def is_destructive(command: str) -> bool:
    """True for catastrophic shapes (used by `doctor`/strict presets)."""
    cmd = command or ""
    return any(re.search(p, cmd) for p in _DESTRUCTIVE)
