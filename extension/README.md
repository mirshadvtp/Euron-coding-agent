# Euron Coding Agent

A lightweight, **Cline/OpenCode-style agentic coding assistant** for VS Code. It
plans, reads your code, makes edits, and runs commands — **with your approval and
a diff at every step.**

Works with **Euron/Euri, OpenAI, OpenRouter, Anthropic, Ollama**, or **any
OpenAI-compatible / self-hosted** model. Bring your own key; nothing is sent to
us.

## Features

- 🧠 **Agentic loop** — plan → read → search → edit → run → verify, iterating on
  real results (native tool-calling, not a one-shot prompt).
- ✅ **Approval + diffs** — no file is written and no command runs without your OK.
- 🔌 **Any provider** — pick one from the toolbar; swap models anytime.
- 🔒 **Keys stay local** — stored in VS Code SecretStorage, never in plaintext files.
- ⚡ **Zero manual setup** — the extension installs and manages its Python backend
  for you in a private environment.

## Getting started

1. Install the extension and open the **Euron Agent** view in the activity bar.
2. Click the **server** icon → pick a provider; click the **key** icon → paste
   your API key (skip for local Ollama).
3. Type a task, e.g. *"add a `/health` route to `app.py` and a test for it."*
4. Review the proposed diffs and click **Approve**.

> **Requirement:** Python 3.9+ must be available on your machine (used to run the
> agent backend). The extension auto-detects it and sets everything else up. If
> Python isn't found it will point you to the download.

## Settings

| Setting | Description |
|---|---|
| `euronAgent.model` | Override the model for the active provider. |
| `euronAgent.pythonPath` | Python 3.9+ to use. Empty = auto-detect. |
| `euronAgent.backendVersion` | Pin the backend version from PyPI. Empty = latest. |
| `euronAgent.serverUrl` | Advanced: connect to a backend you run yourself. |

## Privacy

Your prompts and the relevant file contents are sent **directly to the LLM
provider you configure** (e.g. OpenAI, Anthropic, or your own server). This
extension runs no server of its own and collects no telemetry. Review your
provider's data policy before sending proprietary code.

## License

MIT
