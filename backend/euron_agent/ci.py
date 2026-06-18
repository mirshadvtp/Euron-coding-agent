"""CI recipe scaffolding.

Generates a ready-to-use GitHub Actions workflow that runs the agent headlessly
(`euron-agent run --json`) to review pull requests and/or run the project's tests
and a security pass. Written to `.github/workflows/euron-agent.yml`.
"""
from __future__ import annotations

from pathlib import Path

WORKFLOW = """\
name: Euron Agent

on:
  pull_request:
  workflow_dispatch:

jobs:
  review:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install Euron Agent
        run: pip install euron-coding-agent

      - name: Security + dependency scan
        env:
          # Set the key for your provider in repo Settings → Secrets.
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          euron-agent run --json --yes \\
            "Run secret_scan and dependency_audit on this repo. Summarize any \\
             findings with severity and a concrete fix. Fail loudly if criticals."

      - name: Review the diff
        if: github.event_name == 'pull_request'
        env:
          OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
        run: |
          euron-agent run --json --yes \\
            "Review the changes on this branch vs the base for bugs, security \\
             issues, and missing tests. Output a concise PR review."
"""

README_SNIPPET = """\
## CI: Euron Agent

A GitHub Actions workflow at `.github/workflows/euron-agent.yml` runs the agent on
every pull request to scan for secrets/vulnerable deps and to review the diff.

1. Add your provider key as a repo secret (e.g. `OPENAI_API_KEY`).
2. Adjust the provider/model with `--provider`/`--model` if needed.
"""


def write_workflow(workspace: str, force: bool = False) -> tuple[bool, str]:
    """Create the workflow file. Returns (written, path-or-message)."""
    dest = Path(workspace) / ".github" / "workflows" / "euron-agent.yml"
    if dest.exists() and not force:
        return False, f"{dest} already exists (use force to overwrite)."
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(WORKFLOW, encoding="utf-8")
    except Exception as e:  # noqa: BLE001
        return False, f"Could not write workflow: {e}"
    return True, str(dest)
