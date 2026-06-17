# Changelog

## 0.1.1

- CLI: bare `euron-agent` now opens an interactive chat (Claude-CLI style).
- CLI: configure everything in-session — `/provider`, `/key`, `/model`,
  `/baseurl`, `/config` — persisted to `~/.euron-agent/config.json`.
- Fix: `euron-agent init` works from a pip install (templates are now embedded).

## 0.1.0

Initial release.

- Agentic chat panel in the VS Code sidebar (plan → read → edit → run).
- Streaming responses with per-action **approval** and inline **diffs**.
- Provider-agnostic: Euron/Euri, OpenAI, OpenRouter, Anthropic, Ollama, or any
  OpenAI-compatible / self-hosted endpoint.
- **Zero manual setup**: the extension auto-installs and manages its Python
  backend in a private environment.
- API keys stored securely in VS Code SecretStorage.
