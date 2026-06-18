"""Skills — packaged, reusable capabilities loaded on demand (Claude-Code-style).

A skill is a folder with a `SKILL.md` file:
    .euron/skills/<name>/SKILL.md      (project)
    ~/.euron-agent/skills/<name>/SKILL.md   (global)

`SKILL.md` may start with YAML-ish frontmatter:
    ---
    description: One line shown to the model so it knows when to use this skill.
    ---
    <the full instructions / playbook>

Progressive disclosure: only the name + description go into the system prompt; the
model calls `use_skill(name)` to pull the full body when it actually needs it.
"""
from __future__ import annotations

from pathlib import Path

from .settings import SETTINGS_DIR


def _dirs(workspace: str) -> list[Path]:
    return [SETTINGS_DIR / "skills", Path(workspace) / ".euron" / "skills"]


def _parse(text: str) -> tuple[str, str]:
    """Return (description, body)."""
    description = ""
    body = text
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            front = text[3:end]
            body = text[end + 3:].lstrip("\n")
            for line in front.splitlines():
                if line.lower().strip().startswith("description:"):
                    description = line.split(":", 1)[1].strip()
    if not description:
        for line in body.splitlines():
            s = line.strip().lstrip("#").strip()
            if s:
                description = s
                break
    return description, body


def load_skills(workspace: str) -> dict[str, dict]:
    skills: dict[str, dict] = {}
    for d in _dirs(workspace):
        if not d.is_dir():
            continue
        for sub in sorted(d.iterdir()):
            skill_md = sub / "SKILL.md"
            if skill_md.is_file():
                try:
                    desc, body = _parse(skill_md.read_text(encoding="utf-8"))
                except Exception:
                    continue
                skills[sub.name] = {"description": desc, "body": body, "path": str(sub)}
    return skills


def skills_summary(skills: dict[str, dict]) -> str:
    if not skills:
        return ""
    lines = ["You have these SKILLS available — call `use_skill(name)` to load one when relevant:"]
    for name, s in skills.items():
        lines.append(f"- {name}: {s['description']}")
    return "\n".join(lines)
