"""OpenAI-style tool (function) schemas exposed to the model.

These map 1:1 to the implementations in `tools.py`. Tools whose name appears in
`MUTATING_TOOLS` go through the approval gate before they execute.
"""
from __future__ import annotations

MUTATING_TOOLS = {"write_file", "edit_file", "create_file", "delete_file", "run_command"}


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


TOOL_SCHEMAS = [
    _fn(
        "list_files",
        "List files in the workspace (recursive, ignores configured paths). "
        "Use to understand project structure.",
        {
            "path": {
                "type": "string",
                "description": "Directory relative to workspace root. Default '.'.",
            }
        },
        [],
    ),
    _fn(
        "read_file",
        "Read a UTF-8 text file from the workspace. Optionally a line range.",
        {
            "path": {"type": "string", "description": "File path relative to root."},
            "start_line": {"type": "integer", "description": "1-based start (optional)."},
            "end_line": {"type": "integer", "description": "Inclusive end (optional)."},
        },
        ["path"],
    ),
    _fn(
        "search_text",
        "Search file contents across the workspace (ripgrep if available, else "
        "a pure-Python fallback). Returns matching path:line: text rows.",
        {
            "query": {"type": "string", "description": "Substring or regex to find."},
            "glob": {
                "type": "string",
                "description": "Optional file glob filter, e.g. '*.py'.",
            },
        },
        ["query"],
    ),
    _fn(
        "write_file",
        "Create a new file or fully overwrite an existing one with `content`. "
        "Requires user approval. Prefer edit_file for changing existing files.",
        {
            "path": {"type": "string"},
            "content": {"type": "string", "description": "Full file contents."},
        },
        ["path", "content"],
    ),
    _fn(
        "edit_file",
        "Make a surgical edit by replacing an exact substring. `old_string` must "
        "match the current file EXACTLY (incl. whitespace) and uniquely. Requires "
        "user approval.",
        {
            "path": {"type": "string"},
            "old_string": {"type": "string", "description": "Exact text to replace."},
            "new_string": {"type": "string", "description": "Replacement text."},
            "replace_all": {
                "type": "boolean",
                "description": "Replace every occurrence (default false).",
            },
        },
        ["path", "old_string", "new_string"],
    ),
    _fn(
        "create_file",
        "Create a new file with `content`. Fails if it already exists. Requires "
        "user approval.",
        {"path": {"type": "string"}, "content": {"type": "string"}},
        ["path", "content"],
    ),
    _fn(
        "delete_file",
        "Delete a file from the workspace. Requires user approval.",
        {"path": {"type": "string"}},
        ["path"],
    ),
    _fn(
        "run_command",
        "Run a shell command in the workspace root (e.g. run tests, a script, a "
        "build). Requires user approval. Has a timeout; not for long-running "
        "servers.",
        {"command": {"type": "string", "description": "The shell command to run."}},
        ["command"],
    ),
]
