"""Dependency vulnerability audit.

Detects the project's package ecosystem(s) and runs the matching audit tool if it
is installed - `pip-audit` for Python, `npm audit` for Node, `cargo audit` for
Rust, `govulncheck` for Go. Each is best-effort: if the tool is missing we say so
and tell the agent how to install it, rather than failing. The agent can then act
on the findings (pin/upgrade versions) or surface them to the user.
"""
from __future__ import annotations

import shutil
import subprocess

# (manifest filename, tool exe, audit command, install hint)
_ECOSYSTEMS = [
    ("requirements.txt", "pip-audit", "pip-audit -r requirements.txt --progress-spinner off",
     "pip install pip-audit"),
    ("pyproject.toml", "pip-audit", "pip-audit --progress-spinner off", "pip install pip-audit"),
    ("package.json", "npm", "npm audit --omit=dev", "install Node.js / npm"),
    ("Cargo.toml", "cargo-audit", "cargo audit", "cargo install cargo-audit"),
    ("go.mod", "govulncheck", "govulncheck ./...", "go install golang.org/x/vuln/cmd/govulncheck@latest"),
]


def audit(ctx, timeout: int = 180) -> tuple[bool, str]:
    """Run dependency audits for every detected ecosystem. Returns (clean, report)."""
    root = ctx.root
    detected = [(mf, exe, cmd, hint) for (mf, exe, cmd, hint) in _ECOSYSTEMS
                if (root / mf).exists()]
    if not detected:
        return True, ("No recognized dependency manifest found "
                      "(requirements.txt, pyproject.toml, package.json, Cargo.toml, go.mod).")

    blocks: list[str] = []
    any_findings = False
    seen_tools: set[str] = set()
    for manifest, exe, cmd, hint in detected:
        if exe in seen_tools:
            continue
        seen_tools.add(exe)
        if not shutil.which(exe):
            blocks.append(f"## {manifest}\n  ⚠ '{exe}' not installed — `{hint}` to enable this audit.")
            continue
        try:
            res = subprocess.run(cmd, cwd=root, shell=True, capture_output=True,
                                 text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            blocks.append(f"## {manifest}\n  ⚠ {exe} timed out after {timeout}s.")
            continue
        except Exception as e:  # noqa: BLE001
            blocks.append(f"## {manifest}\n  ⚠ {exe} failed to run: {e}")
            continue
        out = (res.stdout + "\n" + res.stderr).strip()
        if len(out) > 6000:
            out = out[:6000] + "\n… (truncated)"
        # Non-zero exit from these tools means vulnerabilities were found.
        status = "VULNERABILITIES FOUND" if res.returncode != 0 else "clean"
        if res.returncode != 0:
            any_findings = True
        blocks.append(f"## {manifest} ({exe}) — {status}\n{out or '(no output)'}")

    report = "Dependency audit\n\n" + "\n\n".join(blocks)
    return (not any_findings), report
