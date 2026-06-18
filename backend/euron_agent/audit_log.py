"""Tamper-evident, append-only audit log of agent actions.

Every tool call (and its approval decision + outcome) is appended as one JSON line
to `.euron/audit/audit.log`. Each record carries a SHA-256 hash chained over the
previous record's hash, so any insertion, deletion, or edit anywhere in the
history breaks the chain and is detectable with `verify()`.

This gives autonomous / dangerous-mode runs a reviewable, non-repudiable trail for
compliance and debugging - "what did the agent actually do, in what order".
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

_GENESIS = "0" * 64


def _rel_dir(workspace: str) -> Path:
    return Path(workspace) / ".euron" / "audit"


def _log_path(workspace: str) -> Path:
    return _rel_dir(workspace) / "audit.log"


def _hash_record(prev_hash: str, payload: dict) -> str:
    body = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256((prev_hash + body).encode("utf-8")).hexdigest()


def _last_hash(path: Path) -> str:
    if not path.exists():
        return _GENESIS
    last = _GENESIS
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    try:
                        last = json.loads(line).get("hash", last)
                    except Exception:
                        continue
    except Exception:
        return _GENESIS
    return last


class AuditLog:
    """Append-only, hash-chained log. Construct once per session; cheap to call."""

    def __init__(self, workspace: str, enabled: bool = True, *, seq_start: int | None = None):
        self.workspace = workspace
        self.enabled = enabled
        self.path = _log_path(workspace)
        self._prev = _last_hash(self.path) if enabled else _GENESIS
        self._seq = seq_start if seq_start is not None else self._count()

    def _count(self) -> int:
        if not self.path.exists():
            return 0
        try:
            with open(self.path, encoding="utf-8") as fh:
                return sum(1 for ln in fh if ln.strip())
        except Exception:
            return 0

    def record(self, *, ts: str, tool: str, args: dict, decision: str,
               ok: bool | None = None, summary: str = "", depth: int = 0) -> None:
        """Append one action. `ts` is supplied by the caller (no hidden clock)."""
        if not self.enabled:
            return
        payload = {
            "seq": self._seq,
            "ts": ts,
            "tool": tool,
            "args": _shrink(args),
            "decision": decision,
            "ok": ok,
            "summary": summary[:300],
            "depth": depth,
            "prev": self._prev,
        }
        h = _hash_record(self._prev, payload)
        payload["hash"] = h
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(payload, ensure_ascii=False) + "\n")
            self._prev = h
            self._seq += 1
        except Exception:
            pass  # auditing must never break the run


def _shrink(args: dict) -> dict:
    """Keep the log small and secret-light: truncate long string values."""
    out = {}
    for k, v in (args or {}).items():
        if isinstance(v, str) and len(v) > 200:
            out[k] = v[:200] + f"…(+{len(v) - 200})"
        elif isinstance(v, (list, dict)) and len(json.dumps(v, default=str)) > 200:
            out[k] = f"<{type(v).__name__} len {len(v)}>"
        else:
            out[k] = v
    return out


def verify(workspace: str) -> tuple[bool, str]:
    """Re-walk the chain. Returns (intact, message)."""
    path = _log_path(workspace)
    if not path.exists():
        return True, "No audit log yet."
    prev = _GENESIS
    n = 0
    try:
        with open(path, encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                stored = rec.pop("hash", None)
                if rec.get("prev") != prev:
                    return False, f"Chain break at line {lineno}: prev mismatch."
                recomputed = _hash_record(prev, rec)
                if recomputed != stored:
                    return False, f"Tampering at line {lineno}: hash mismatch."
                prev = stored
                n += 1
    except Exception as e:  # noqa: BLE001
        return False, f"Audit log unreadable: {e}"
    return True, f"Audit log intact — {n} record(s), chain verified."


def tail(workspace: str, n: int = 20) -> str:
    path = _log_path(workspace)
    if not path.exists():
        return "No audit log yet."
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    except Exception as e:  # noqa: BLE001
        return f"Could not read audit log: {e}"
    rows = []
    for ln in lines[-n:]:
        try:
            r = json.loads(ln)
            ok = "" if r.get("ok") is None else ("ok" if r["ok"] else "FAIL")
            rows.append(f"{r.get('seq'):>4} {r.get('ts','')[:19]} {r.get('decision','?'):>5} "
                        f"{r.get('tool',''):<16} {ok:<4} {r.get('summary','')}")
        except Exception:
            continue
    return "\n".join(rows) if rows else "No records."
