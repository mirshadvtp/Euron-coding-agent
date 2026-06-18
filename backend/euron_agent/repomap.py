"""Token-friendly semantic repo map.

Instead of reading whole files, the agent can pull a compact *outline* of the
repository: per-file lists of the top-level symbols (classes, functions, methods,
exported consts) with their line numbers. This is language-agnostic - it uses
lightweight regex signatures, so there are no heavy parser dependencies - and the
output is bounded so it always fits in a small slice of the context window.

The agent reads the map first to locate code, then `read_file` only the specific
ranges it needs. That turns "read 40 files to find the auth handler" into "read
one outline, then one function".
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# Per-language symbol signatures: (compiled regex, kind). Group 1 is the name.
_LANG_PATTERNS: dict[str, list[tuple[re.Pattern, str]]] = {
    "py": [
        (re.compile(r"^\s*class\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)"), "def"),
    ],
    "js": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(?[^=]*=>"), "const-fn"),
    ],
    "ts": [
        (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?(?:abstract\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
        (re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"), "interface"),
        (re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)"), "type"),
        (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_$][\w$]*)"), "function"),
        (re.compile(r"^\s*(?:export\s+)?const\s+([A-Za-z_$][\w$]*)\s*[:=]"), "const"),
    ],
    "go": [
        (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)"), "func"),
        (re.compile(r"^\s*type\s+([A-Za-z_]\w*)\s+(?:struct|interface)"), "type"),
    ],
    "rs": [
        (re.compile(r"^\s*(?:pub\s+)?fn\s+([A-Za-z_]\w*)"), "fn"),
        (re.compile(r"^\s*(?:pub\s+)?(?:struct|enum|trait)\s+([A-Za-z_]\w*)"), "type"),
    ],
    "java": [
        (re.compile(r"^\s*(?:public|private|protected).*\bclass\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*(?:public|private|protected|static).*\b([A-Za-z_]\w*)\s*\([^;{]*\)\s*\{"), "method"),
    ],
    "rb": [
        (re.compile(r"^\s*class\s+([A-Za-z_]\w*)"), "class"),
        (re.compile(r"^\s*def\s+([A-Za-z_][\w?!]*)"), "def"),
    ],
}
# File extension -> language key.
_EXT_LANG = {
    ".py": "py", ".js": "js", ".jsx": "js", ".mjs": "js", ".cjs": "js",
    ".ts": "ts", ".tsx": "ts", ".go": "go", ".rs": "rs", ".java": "java",
    ".rb": "rb",
}

_MAX_FILES = 400
_MAX_SYMBOLS_PER_FILE = 40
_MAX_TOTAL_LINES = 1500


def _symbols_in(path: Path, lang: str) -> list[tuple[int, str, str]]:
    """Return (line_no, kind, name) for symbols in one file."""
    pats = _LANG_PATTERNS.get(lang, [])
    out: list[tuple[int, str, str]] = []
    try:
        with open(path, encoding="utf-8", errors="ignore") as fh:
            for i, line in enumerate(fh, 1):
                if len(line) > 400:
                    continue
                for rx, kind in pats:
                    m = rx.match(line)
                    if m:
                        out.append((i, kind, m.group(1)))
                        break
                if len(out) >= _MAX_SYMBOLS_PER_FILE:
                    out.append((i, "…", "(more symbols truncated)"))
                    break
    except Exception:
        return out
    return out


def build_map(ctx, path: str = ".", lang_filter: str | None = None) -> str:
    """Build a compact outline of the workspace (or a sub-path).

    `ctx` is a tools.ToolContext (used for sandboxing + ignore rules).
    """
    base = ctx.resolve(path)
    if not base.exists():
        return f"Path not found: {path}"
    if base.is_file():
        files = [base]
    else:
        files = []
        for root, dirs, names in os.walk(base):
            rroot = ctx.rel(Path(root))
            dirs[:] = [d for d in sorted(dirs)
                       if not ctx.is_ignored(f"{rroot}/{d}".lstrip("./"))]
            for n in sorted(names):
                fp = Path(root) / n
                if fp.suffix.lower() in _EXT_LANG and not ctx.is_ignored(ctx.rel(fp)):
                    files.append(fp)
            if len(files) >= _MAX_FILES:
                break

    lines: list[str] = []
    total = 0
    shown = 0
    for fp in sorted(files):
        lang = _EXT_LANG.get(fp.suffix.lower())
        if not lang or (lang_filter and lang != lang_filter):
            continue
        syms = _symbols_in(fp, lang)
        if not syms:
            continue
        lines.append(f"\n{ctx.rel(fp)}")
        for ln, kind, name in syms:
            lines.append(f"  {ln:>5}  {kind} {name}")
            total += 1
        shown += 1
        if total >= _MAX_TOTAL_LINES:
            lines.append("\n… (map truncated; narrow with path= or lang=)")
            break

    if not lines:
        return "(no recognizable source symbols found)"
    header = f"# Repo map — {shown} file(s), {total} symbols (line  kind  name)"
    return header + "\n" + "\n".join(lines)
