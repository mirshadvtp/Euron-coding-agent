"""Permission engine — allow / ask / deny rules by tool + glob.

Replaces the coarse auto-approve flags with fine-grained control, e.g.
    allow:  read_file(**), git_status, git_diff
    ask:    run_command(*), write_file(**)
    deny:   delete_file(**/.git/**), run_command(rm -rf*)

Rules are evaluated in order (first match wins); otherwise per-category defaults
apply. "Always allow" decisions from an approval prompt are appended and
persisted to ~/.euron-agent/permissions.json.
"""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass

from .settings import SETTINGS_DIR

PERMISSIONS_FILE = SETTINGS_DIR / "permissions.json"

_READ_TOOLS = {
    "list_files", "read_file", "search_text", "glob",
    "process_output", "process_list", "git_status", "git_diff",
}
_COMMAND_TOOLS = {"run_command", "bash_background"}


@dataclass
class Rule:
    tool: str       # tool name or "*"
    pattern: str    # glob matched against the action's target string
    action: str     # allow | ask | deny


def _target(tool: str, args: dict) -> str:
    if tool in ("run_command", "bash_background"):
        return args.get("command", "")
    if tool.startswith("mcp__"):
        return f"{tool} {json.dumps(args)[:200]}"
    return args.get("path") or args.get("url") or args.get("query") or ""


def parse_rule(spec: str, action: str) -> Rule:
    """Parse 'tool(pattern)' or 'tool' into a Rule."""
    spec = spec.strip()
    if spec.endswith(")") and "(" in spec:
        tool, pattern = spec[:-1].split("(", 1)
        return Rule(tool.strip() or "*", pattern.strip() or "*", action)
    return Rule(spec or "*", "*", action)


class Permissions:
    def __init__(
        self,
        rules: list[Rule] | None = None,
        *,
        default_writes: str = "ask",
        default_commands: str = "ask",
        default_reads: str = "allow",
    ):
        self.rules = rules or []
        self.default_writes = default_writes
        self.default_commands = default_commands
        self.default_reads = default_reads

    @classmethod
    def from_config(cls, perms: dict, *, auto_writes: bool, auto_commands: bool) -> "Permissions":
        rules: list[Rule] = []
        for action in ("deny", "ask", "allow"):  # deny first so it's checked first
            for spec in (perms.get(action) or []):
                rules.append(parse_rule(spec, action))
        # persisted user "always allow" rules
        rules += _load_persisted()
        return cls(
            rules,
            default_writes="allow" if auto_writes else "ask",
            default_commands="allow" if auto_commands else "ask",
        )

    def decide(self, tool: str, args: dict) -> str:
        target = _target(tool, args)
        for r in self.rules:
            if r.tool in (tool, "*") and fnmatch.fnmatch(target, r.pattern):
                return r.action
        if tool in _READ_TOOLS:
            return self.default_reads
        if tool in _COMMAND_TOOLS or tool.startswith("mcp__"):
            return self.default_commands
        return self.default_writes

    def add_always_allow(self, tool: str, args: dict) -> None:
        target = _target(tool, args)
        pattern = _generalize(tool, target)
        rule = Rule(tool, pattern, "allow")
        self.rules.insert(0, rule)
        _persist(rule)


def _generalize(tool: str, target: str) -> str:
    """Turn a concrete target into a sensible reusable glob."""
    if tool in ("run_command", "bash_background"):
        first = target.split() [0] if target.split() else target
        return f"{first}*"
    if "/" in target:
        return target  # exact path
    return target or "*"


# --------------------------------------------------------------------------- #
def _load_persisted() -> list[Rule]:
    try:
        data = json.loads(PERMISSIONS_FILE.read_text(encoding="utf-8"))
        return [Rule(r["tool"], r["pattern"], r["action"]) for r in data.get("rules", [])]
    except Exception:
        return []


def _persist(rule: Rule) -> None:
    try:
        SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
        existing = []
        if PERMISSIONS_FILE.exists():
            existing = json.loads(PERMISSIONS_FILE.read_text(encoding="utf-8")).get("rules", [])
        existing.insert(0, {"tool": rule.tool, "pattern": rule.pattern, "action": rule.action})
        PERMISSIONS_FILE.write_text(json.dumps({"rules": existing}, indent=2), encoding="utf-8")
    except Exception:
        pass
