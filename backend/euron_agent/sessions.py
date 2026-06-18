"""Named, resumable sessions with a dashboard + transcript search.

Each session is a JSON file under ``~/.euron-agent/sessions/<id>.json`` with
metadata (workspace, created/updated timestamps, title) and the full message
list. Supports listing (dashboard), resuming by id or "latest for this
workspace", and full-text search across past conversations.
"""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .settings import SETTINGS_DIR

SESSIONS_DIR = SETTINGS_DIR / "sessions"


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def _file(session_id: str) -> Path:
    return SESSIONS_DIR / f"{session_id}.json"


def _title(messages: list[dict]) -> str:
    for m in messages:
        if m.get("role") == "user":
            c = m.get("content")
            text = c if isinstance(c, str) else (
                next((b.get("text", "") for b in c if isinstance(b, dict) and b.get("type") == "text"), "")
                if isinstance(c, list) else ""
            )
            text = text.strip().replace("\n", " ")
            if text:
                return text[:60]
    return "(empty session)"


def save(session_id: str, workspace: str, messages: list[dict]) -> None:
    try:
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
        f = _file(session_id)
        created = time.time()
        if f.exists():
            try:
                created = json.loads(f.read_text(encoding="utf-8")).get("created", created)
            except Exception:
                pass
        f.write_text(json.dumps({
            "id": session_id,
            "workspace": str(Path(workspace).resolve()),
            "created": created,
            "updated": time.time(),
            "title": _title(messages),
            "messages": messages,
        }), encoding="utf-8")
    except Exception:
        pass


def load(session_id: str) -> list[dict]:
    try:
        return json.loads(_file(session_id).read_text(encoding="utf-8")).get("messages", [])
    except Exception:
        return []


def _meta(path: Path) -> dict | None:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return {k: d.get(k) for k in ("id", "workspace", "created", "updated", "title")}
    except Exception:
        return None


def list_sessions(workspace: str | None = None) -> list[dict]:
    if not SESSIONS_DIR.is_dir():
        return []
    ws = str(Path(workspace).resolve()) if workspace else None
    out = []
    for p in SESSIONS_DIR.glob("*.json"):
        m = _meta(p)
        if m and (ws is None or m.get("workspace") == ws):
            out.append(m)
    return sorted(out, key=lambda m: m.get("updated") or 0, reverse=True)


def latest_id(workspace: str) -> str | None:
    rows = list_sessions(workspace)
    return rows[0]["id"] if rows else None


def search(query: str, workspace: str | None = None) -> list[dict]:
    q = query.lower()
    hits = []
    for meta in list_sessions(workspace):
        for m in load(meta["id"]):
            c = m.get("content")
            if isinstance(c, str) and q in c.lower():
                idx = c.lower().find(q)
                hits.append({"id": meta["id"], "title": meta["title"],
                             "snippet": c[max(0, idx - 30): idx + 50].replace("\n", " ")})
                break
    return hits


def delete(session_id: str) -> None:
    try:
        _file(session_id).unlink()
    except Exception:
        pass
