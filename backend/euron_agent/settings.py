"""Persistent user settings for the CLI (Claude-CLI-style).

Stored at ``~/.euron-agent/config.json`` so the CLI remembers your provider,
model, and API keys between sessions. Keys live here in plaintext (user-only
file permissions on POSIX); for shared machines prefer environment variables.

Shape:
    {
      "provider": "openai",
      "providers": {
        "openai": {"api_key": "sk-...", "model": "gpt-4o-mini", "base_url": null}
      }
    }
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SETTINGS_DIR = Path.home() / ".euron-agent"
SETTINGS_FILE = SETTINGS_DIR / "config.json"


def load() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save(data: dict) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    try:
        os.chmod(SETTINGS_FILE, 0o600)  # best-effort; no-op on some platforms
    except Exception:
        pass


def set_active_provider(name: str) -> None:
    d = load()
    d["provider"] = name
    save(d)


def set_provider_field(provider: str, field: str, value) -> None:
    d = load()
    provs = d.setdefault("providers", {})
    p = provs.setdefault(provider, {})
    if value in (None, ""):
        p.pop(field, None)
    else:
        p[field] = value
    save(d)


def provider_overrides(provider: str) -> dict:
    return (load().get("providers") or {}).get(provider, {})
