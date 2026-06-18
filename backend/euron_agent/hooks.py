"""User-defined hooks — run shell commands on agent lifecycle events.

Config (config.yaml):
    hooks:
      PreToolUse:
        - matcher: "write_file|edit_file|multi_edit"   # regex on tool name (or "*")
          command: "ruff check ."                        # non-zero exit BLOCKS the tool
      PostToolUse:
        - matcher: "*"
          command: "echo done"
      Stop:
        - command: "notify-send 'agent finished'"
      UserPromptSubmit:
        - command: "echo prompt received"

The event payload is passed to the command as JSON on stdin. For PreToolUse a
non-zero exit code blocks the tool and the command's output is fed back to the
model as the reason.
"""
from __future__ import annotations

import json
import re
import subprocess
from typing import Optional

EVENTS = ("PreToolUse", "PostToolUse", "Stop", "UserPromptSubmit")


class HookRunner:
    def __init__(self, config: Optional[dict], workspace: str, timeout: int = 30):
        self.config = config or {}
        self.workspace = workspace
        self.timeout = timeout

    @property
    def active(self) -> bool:
        return bool(self.config)

    def _matching(self, event: str, tool: Optional[str]) -> list[str]:
        cmds = []
        for hook in self.config.get(event, []) or []:
            matcher = hook.get("matcher", "*")
            if tool is None or matcher == "*" or re.search(matcher, tool):
                if hook.get("command"):
                    cmds.append(hook["command"])
        return cmds

    def run(self, event: str, payload: dict) -> tuple[bool, str]:
        """Returns (blocked, message). `blocked` is only ever True for PreToolUse."""
        tool = payload.get("tool")
        outputs: list[str] = []
        for command in self._matching(event, tool):
            try:
                res = subprocess.run(
                    command,
                    cwd=self.workspace,
                    shell=True,
                    input=json.dumps(payload),
                    capture_output=True,
                    text=True,
                    timeout=self.timeout,
                )
            except Exception as e:  # noqa: BLE001
                outputs.append(f"hook error: {e}")
                continue
            out = (res.stdout + res.stderr).strip()
            if out:
                outputs.append(out)
            if event == "PreToolUse" and res.returncode != 0:
                return True, out or f"blocked by hook (exit {res.returncode})"
        return False, "\n".join(outputs)
