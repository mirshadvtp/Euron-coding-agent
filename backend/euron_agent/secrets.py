"""Secret scanner — find hard-coded credentials before they ship.

A regex sweep over the workspace for the highest-signal secret shapes (API keys,
private keys, tokens, connection strings). It is intentionally conservative to
keep false positives low, reports `path:line` with the match masked, and never
prints the full secret back into the model's context.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

# (label, compiled pattern). Patterns target distinctive secret shapes.
_RULES: list[tuple[str, re.Pattern]] = [
    ("AWS access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("AWS secret access key", re.compile(r"(?i)aws.{0,20}secret.{0,20}['\"][0-9a-zA-Z/+]{40}['\"]")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b")),
    ("GitHub fine-grained token", re.compile(r"\bgithub_pat_[0-9A-Za-z_]{60,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("OpenAI / sk- key", re.compile(r"\bsk-[A-Za-z0-9]{20,}\b")),
    ("Anthropic key", re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b")),
    ("PyPI token", re.compile(r"\bpypi-[A-Za-z0-9\-_]{40,}\b")),
    ("Stripe key", re.compile(r"\b[sr]k_live_[0-9A-Za-z]{20,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b")),
    ("Private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----")),
    ("Generic secret assignment", re.compile(
        r"(?i)(?:password|passwd|secret|api[_-]?key|access[_-]?token|auth[_-]?token)"
        r"\s*[:=]\s*['\"][^'\"\s]{8,}['\"]")),
    ("Connection string w/ password", re.compile(
        r"(?i)(?:postgres|mysql|mongodb|redis|amqp)(?:\+\w+)?://[^:\s]+:[^@\s]+@")),
]

# Don't flag obvious placeholders.
_PLACEHOLDER = re.compile(
    r"(?i)(your[_-]?|example|placeholder|changeme|xxx+|\.\.\.|<[^>]+>|dummy|sample|test[_-]?key)")

_MAX_HITS = 200


def _mask(s: str) -> str:
    s = s.strip().strip("'\"")
    if len(s) <= 12:
        return s[:2] + "…"
    return f"{s[:4]}…{s[-4:]} (len {len(s)})"


def scan(ctx, path: str = ".") -> tuple[int, str]:
    """Scan the workspace for likely secrets. Returns (hit_count, report)."""
    base = ctx.resolve(path)
    hits: list[str] = []
    scanned = 0
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
                if not ctx.is_ignored(ctx.rel(fp)):
                    files.append(fp)

    for fp in files:
        try:
            if fp.stat().st_size > 400_000:
                continue
            with open(fp, "rb") as fb:
                if b"\x00" in fb.read(2048):
                    continue
        except Exception:
            continue
        try:
            with open(fp, encoding="utf-8", errors="ignore") as fh:
                for i, line in enumerate(fh, 1):
                    if len(line) > 1000:
                        continue
                    for label, rx in _RULES:
                        m = rx.search(line)
                        if m and not _PLACEHOLDER.search(m.group(0)):
                            hits.append(f"{ctx.rel(fp)}:{i}: [{label}] {_mask(m.group(0))}")
                            break
                    if len(hits) >= _MAX_HITS:
                        break
        except Exception:
            continue
        scanned += 1
        if len(hits) >= _MAX_HITS:
            hits.append("… (more; truncated)")
            break

    if not hits:
        return 0, f"No likely secrets found ({scanned} files scanned)."
    report = (f"Found {len(hits)} potential secret(s) across {scanned} files. "
              "Review each; rotate anything real and move it to env/secret storage:\n\n"
              + "\n".join(hits))
    return len(hits), report
