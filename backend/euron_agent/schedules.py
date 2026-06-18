"""Scheduled agents — run tasks on cron schedules, independent of any terminal.

    euron-agent schedule create "PR summary" --cron "0 9 * * MON-FRI" \
        --prompt "List all open PRs and their review status" --workspace /repo
    euron-agent schedule list
    euron-agent schedule run <id>
    euron-agent schedule daemon          # foreground loop that fires due schedules

Schedules persist in ~/.euron-agent/schedules.json across restarts. Includes a
small dependency-free cron matcher (5 fields, supports *, lists, ranges, steps,
and day/month names).
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime

from .settings import SETTINGS_DIR

SCHEDULES_FILE = SETTINGS_DIR / "schedules.json"

_DOW = {"SUN": 0, "MON": 1, "TUE": 2, "WED": 3, "THU": 4, "FRI": 5, "SAT": 6}
_MON = {m: i + 1 for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"])}


# --------------------------------------------------------------------------- #
# Cron matching
# --------------------------------------------------------------------------- #
def _match_field(expr: str, value: int, names: dict | None = None) -> bool:
    expr = expr.upper()
    if names:
        for n, v in names.items():
            expr = expr.replace(n, str(v))
    for part in expr.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part in ("*", ""):
            lo, hi = None, None
        elif "-" in part:
            a, b = part.split("-", 1)
            lo, hi = int(a), int(b)
        else:
            lo = hi = int(part)
        if lo is None:  # wildcard
            if value % step == 0:
                return True
        elif lo <= value <= hi and (value - lo) % step == 0:
            return True
    return False


def cron_match(expr: str, dt: datetime) -> bool:
    fields = expr.split()
    if len(fields) != 5:
        return False
    minute, hour, dom, month, dow = fields
    cron_dow = (dt.weekday() + 1) % 7  # Python Mon=0..Sun=6 -> cron Sun=0..Sat=6
    dom_r = dom not in ("*", "?")
    dow_r = dow not in ("*", "?")
    base = (
        _match_field(minute, dt.minute)
        and _match_field(hour, dt.hour)
        and _match_field(month, dt.month, _MON)
    )
    if not base:
        return False
    dom_ok = _match_field(dom, dt.day)
    dow_ok = _match_field(dow, cron_dow, _DOW)
    # standard cron: when both day-fields are restricted, it's OR
    if dom_r and dow_r:
        return dom_ok or dow_ok
    return dom_ok and dow_ok


# --------------------------------------------------------------------------- #
# Storage
# --------------------------------------------------------------------------- #
def _load() -> list[dict]:
    try:
        return json.loads(SCHEDULES_FILE.read_text(encoding="utf-8")).get("schedules", [])
    except Exception:
        return []


def _save(rows: list[dict]) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SCHEDULES_FILE.write_text(json.dumps({"schedules": rows}, indent=2), encoding="utf-8")


def create(name: str, cron: str, prompt: str, workspace: str,
           provider: str | None = None, model: str | None = None) -> dict:
    rows = _load()
    sched = {
        "id": uuid.uuid4().hex[:8],
        "name": name,
        "cron": cron,
        "prompt": prompt,
        "workspace": workspace,
        "provider": provider,
        "model": model,
        "last_run": None,
    }
    rows.append(sched)
    _save(rows)
    return sched


def list_schedules() -> list[dict]:
    return _load()


def remove(schedule_id: str) -> bool:
    rows = _load()
    new = [r for r in rows if r["id"] != schedule_id]
    _save(new)
    return len(new) != len(rows)


def get(schedule_id: str) -> dict | None:
    return next((r for r in _load() if r["id"] == schedule_id), None)


def mark_run(schedule_id: str, stamp: str) -> None:
    rows = _load()
    for r in rows:
        if r["id"] == schedule_id:
            r["last_run"] = stamp
    _save(rows)


def due(now: datetime) -> list[dict]:
    stamp = now.strftime("%Y-%m-%d %H:%M")
    return [r for r in _load() if r.get("last_run") != stamp and cron_match(r["cron"], now)]
