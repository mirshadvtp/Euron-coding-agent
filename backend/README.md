# euron-coding-agent

The Python agent backend and standalone CLI behind the Euron Coding Agent VS Code
extension. An open-source, model-agnostic agentic coding assistant that plans,
edits code, runs commands, searches the web, reviews diffs, coordinates teams of
sub-agents, and runs on cron schedules - with your approval at every step.

Provider-agnostic: works with Anthropic, OpenAI, Google Gemini, OpenRouter, Groq,
Cerebras, DeepSeek, Together, Mistral, xAI, Vercel AI Gateway, Euron/Euri,
Ollama, LM Studio, or any OpenAI-compatible / self-hosted endpoint.

## Install

    pip install euron-coding-agent
    euron-agent update                            # update to the latest anytime
    # optional extras:
    pip install "euron-coding-agent[anthropic]"   # native Anthropic client
    pip install "euron-coding-agent[mcp]"         # Model Context Protocol servers

Tip: install into a virtual environment for isolation. The client auto-adapts to
each provider's API (e.g. models needing max_completion_tokens instead of
max_tokens, or rejecting a custom temperature) - no model-specific config needed.

## Quick start (CLI)

    euron-agent                                    # interactive chat in the current folder
    euron-agent run "add a /health route to app.py"
    euron-agent run "summarize the repo" --json     # headless JSON output for CI/scripts
    euron-agent serve --host 0.0.0.0 --port 8000    # self-host with bearer-token auth

Set a provider and key right inside the chat (no files needed):

    you: /provider openai      # or gemini, anthropic, groq, ollama, ...
    you: /key                  # paste your API key (stored in ~/.euron-agent)
    you: create a FastAPI hello-world app with a test

Or use environment variables (OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY,
EURI_API_KEY, ...) or a config.yaml. Built-in provider profiles mean no config
file is required - just pick a provider and supply a key.

## CLI commands

    euron-agent                     interactive chat (remembers provider/key)
    euron-agent run "<task>"        one-shot task ( --yes to auto-approve, --json headless )
    euron-agent providers           list configured providers
    euron-agent init                scaffold config.yaml + .env
    euron-agent serve               run the API / WebSocket server ( --port 0 = auto )
    euron-agent --team-name <name> "<task>"   multi-agent team (coordinator + specialists)
    euron-agent team                list multi-agent teams
    euron-agent schedule create "<name>" --cron "0 9 * * MON-FRI" --prompt "<task>"
    euron-agent schedule list | run <id> | remove <id> | daemon
    euron-agent plugin add <dir|zip-url> | list | remove <name>
    euron-agent sessions            list saved sessions (dashboard)
    euron-agent chat --session <id> | --resume   resume a past session

In-chat commands: /provider /key /model /effort /plan /review /compact /init
/skills /search /usage /undo /reset /yes /help /exit, plus any custom command in
.euron/commands/.

## What's new (1.0.8)

- Auto-onboarding: the first time the agent works in a project it scaffolds a
  `.euron/` wrapper - memory (AGENTS.md, pre-filled with the detected stack and
  build/test commands), a project doc (PROJECT.md), and a starter skill - so it is
  set up with zero effort. Re-run with `euron-agent onboard` or `/onboard`.
- Live status line in the CLI (spinner, elapsed, live tokens + cost, tool and
  sub-agent counters) and a completion summary of the steps taken.
- Dangerous (YOLO) mode (`--dangerous` / `/dangerous`), `/` command autocomplete,
  accurate per-model cost (with a `pricing:` override), and memory/context
  optimization (bounded tool outputs + automatic compaction).

## Features

- Auto-onboarding (.euron/ memory + skill + project doc, created automatically).
- Agentic tool-calling loop: plan, read, search, edit, run, verify.
- 28 sandboxed tools: read/write/edit/multi_edit/create/delete files, glob,
  search, run_command (streaming), background processes, git status/diff/commit/
  branch/push, open_pr, git worktrees, web_search, web_fetch.
- Plan mode, sub-agents, and multi-agent teams with persistent state.
- Scheduled agents on cron schedules (independent of any terminal).
- MCP servers, plugins, skills, project memory (AGENTS.md), and custom commands.
- Permissions (allow/ask/deny), hooks, approvals with diffs, checkpoints, undo.
- Web search/fetch, multimodal image input, token and cost tracking, extended
  thinking, model fallback chains.
- Messaging notifications: Slack, Discord, Telegram, Google Chat, WhatsApp, Linear.
- Headless JSON mode and a self-hostable server with bearer-token auth.

## Server

- GET  /health      liveness.
- GET  /providers   configured providers.
- POST /agent/run   one-shot, non-interactive (set "auto_approve": true).
- WS   /ws          streaming agent with per-action approval (used by the extension).

The WebSocket init message accepts provider, model, api_key, and base_url so
secrets never need to live on disk. Set EURON_AGENT_TOKEN (or serve --token) to
require bearer auth.

See the project README at https://github.com/euron-tech/Euron-coding-agent for the
full architecture and the VS Code extension.

## License

Apache License 2.0. Copyright 2026 Euron Engage Sphere Technology Private Limited.
