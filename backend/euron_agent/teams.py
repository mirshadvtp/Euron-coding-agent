"""Multi-agent teams — a coordinator that delegates to specialist sub-agents,
with team state that persists across sessions.

A team is a named, persistent coordinator session (`team-<name>`). The
coordinator breaks work into subtasks and delegates each to a specialist via
`spawn_agent`; progress is tracked with `todo_write`. Because the team is a
persisted session, you can stop and resume later:

    euron-agent --team-name auth-sprint "Plan and implement auth with tests"
    euron-agent --team-name auth-sprint            # resume the team
"""
from __future__ import annotations

import re

from . import sessions

TEAM_PREFIX = "team-"


def team_id(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "team"
    return TEAM_PREFIX + slug


def list_teams() -> list[dict]:
    return [s for s in sessions.list_sessions() if str(s.get("id", "")).startswith(TEAM_PREFIX)]


def coordinator_prompt(name: str) -> str:
    return (
        f"\n\n# Team mode — you are the COORDINATOR of team '{name}'.\n"
        "Plan the work, then DELEGATE each independent subtask to a specialist via "
        "`spawn_agent` (give each a focused role and a complete prompt). Track the "
        "plan with `todo_write` (one item in_progress at a time). Run specialists in "
        "parallel where possible, integrate their results, and verify the whole. "
        "Team state persists, so summarize progress clearly for future sessions."
    )
