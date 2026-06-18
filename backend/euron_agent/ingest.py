"""Ingest files, folders, and images the user references or drags into the chat.

If a message contains a path (quoted, bare, or the whole line - e.g. a drag-and-
dropped file/folder, absolute or relative) that exists on disk, its contents are
inlined into the prompt: text files are read (any reasonable length), folders are
read recursively (bounded), and images become multimodal blocks for vision models.
"""
from __future__ import annotations

import base64
import mimetypes
import re
from pathlib import Path

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
_MAX_FILE = 400_000        # chars per ingested file
_MAX_TOTAL = 1_200_000     # total chars across an ingest
_MAX_FOLDER_FILES = 60


def _is_binary(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return b"\x00" in f.read(4096)
    except Exception:
        return True


def _read_text(p: Path) -> str:
    try:
        data = p.read_text(encoding="utf-8", errors="replace")
    except Exception as e:  # noqa: BLE001
        return f"(could not read: {e})"
    if len(data) > _MAX_FILE:
        head = _MAX_FILE * 3 // 4
        data = data[:head] + f"\n…[truncated, {len(data)} chars total]…\n" + data[-(_MAX_FILE - head):]
    return data


def _image_url(p: Path) -> str | None:
    try:
        mime = mimetypes.guess_type(str(p))[0] or "image/png"
        b64 = base64.b64encode(p.read_bytes()).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except Exception:
        return None


def _candidates(text: str) -> list[str]:
    cands: list[str] = []
    for m in re.finditer(r'"([^"]+)"|\'([^\']+)\'', text):  # quoted (drag-drop w/ spaces)
        cands.append(m.group(1) or m.group(2))
    whole = text.strip().strip('"').strip("'")
    if whole:
        cands.append(whole)                                  # whole line = one dropped path
    for tok in re.split(r"\s+", text):                       # path-like tokens
        tok = tok.strip().strip("\"'").rstrip(".,;:)").lstrip("@")
        looks_path = "/" in tok or "\\" in tok or re.match(r"^[A-Za-z]:", tok)
        has_ext = re.search(r"\.[A-Za-z0-9]{1,6}$", tok)
        if len(tok) > 2 and (looks_path or has_ext):
            cands.append(tok)
    out, seen = [], set()
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _resolve(cand: str, ctx) -> Path | None:
    try:
        p = Path(cand)
        if p.exists():
            return p
        rp = ctx.root / cand
        if rp.exists():
            return rp
    except Exception:
        return None
    return None


def gather(task: str, ctx) -> tuple[str, list[str]]:
    """Return (augmented_task, image_data_urls). Inlines referenced files/folders;
    collects images as data URLs for vision models."""
    images: list[str] = []
    blocks: list[str] = []
    total = 0
    handled: set[str] = set()
    for cand in _candidates(task):
        p = _resolve(cand, ctx)
        if p is None:
            continue
        key = str(p.resolve())
        if key in handled:
            continue
        handled.add(key)
        try:
            if p.is_dir():
                files = [fp for fp in sorted(p.rglob("*"))
                         if fp.is_file() and not _is_binary(fp)][:_MAX_FOLDER_FILES]
                blocks.append(f"--- folder: {p} ({len(files)} files) ---")
                for fp in files:
                    if total > _MAX_TOTAL:
                        break
                    c = _read_text(fp)
                    try:
                        rel = fp.relative_to(p)
                    except Exception:
                        rel = fp.name
                    blocks.append(f"### {rel}\n{c}")
                    total += len(c)
            elif p.suffix.lower() in IMAGE_EXTS:
                url = _image_url(p)
                if url:
                    images.append(url)
                    blocks.append(f"[image attached: {p.name}]")
            elif p.is_file() and not _is_binary(p):
                c = _read_text(p)
                blocks.append(f"--- file: {p} ---\n{c}")
                total += len(c)
        except Exception:
            continue
        if total > _MAX_TOTAL:
            break

    if blocks:
        task = task + "\n\n# Provided context (files / folders / images you referenced)\n" + "\n\n".join(blocks)
    return task, images
