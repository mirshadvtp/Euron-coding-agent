"""Plugins — installable bundles of skills, commands, and MCP servers.

A plugin is a folder (or a `.zip` URL) containing any of:
    <plugin>/skills/<name>/SKILL.md     # skills
    <plugin>/commands/*.md              # custom slash commands
    <plugin>/euron-plugin.yaml          # manifest: name, description, mcp servers

Installed plugins live in `~/.euron-agent/plugins/<name>/`. Their skills,
commands, and MCP servers are merged into every session automatically.
"""
from __future__ import annotations

import io
import shutil
import zipfile
from pathlib import Path

import yaml

from .settings import SETTINGS_DIR

PLUGINS_DIR = SETTINGS_DIR / "plugins"


def _manifest(plugin_dir: Path) -> dict:
    for name in ("euron-plugin.yaml", "euron-plugin.yml", "euron-plugin.json"):
        p = plugin_dir / name
        if p.is_file():
            try:
                return yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            except Exception:
                return {}
    return {}


def list_plugins() -> list[dict]:
    if not PLUGINS_DIR.is_dir():
        return []
    out = []
    for d in sorted(PLUGINS_DIR.iterdir()):
        if d.is_dir():
            m = _manifest(d)
            out.append({"name": d.name, "description": m.get("description", "")})
    return out


def install(source: str) -> str:
    """Install from a local directory or a .zip URL. Returns the plugin name."""
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    src = Path(source)
    if src.is_dir():
        name = _manifest(src).get("name") or src.name
        dest = PLUGINS_DIR / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
        return name
    if source.startswith(("http://", "https://")) and source.endswith(".zip"):
        import httpx

        data = httpx.get(source, follow_redirects=True, timeout=60).content
        name = Path(source).stem
        dest = PLUGINS_DIR / name
        if dest.exists():
            shutil.rmtree(dest)
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            zf.extractall(dest)
        # if the zip wrapped everything in a single top folder, flatten it
        entries = [p for p in dest.iterdir()]
        if len(entries) == 1 and entries[0].is_dir():
            inner = entries[0]
            for child in inner.iterdir():
                shutil.move(str(child), str(dest / child.name))
            inner.rmdir()
        return _manifest(dest).get("name") or name
    raise ValueError("source must be a local directory or an http(s) .zip URL")


def remove(name: str) -> bool:
    dest = PLUGINS_DIR / name
    if dest.is_dir():
        shutil.rmtree(dest)
        return True
    return False


def _plugin_dirs() -> list[Path]:
    if not PLUGINS_DIR.is_dir():
        return []
    return [d for d in PLUGINS_DIR.iterdir() if d.is_dir()]


def plugin_skill_dirs() -> list[Path]:
    return [d / "skills" for d in _plugin_dirs() if (d / "skills").is_dir()]


def plugin_command_dirs() -> list[Path]:
    return [d / "commands" for d in _plugin_dirs() if (d / "commands").is_dir()]


def plugin_mcp_servers() -> dict:
    servers: dict = {}
    for d in _plugin_dirs():
        servers.update((_manifest(d).get("mcp", {}) or {}).get("servers", {}) or {})
    return servers
