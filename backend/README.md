# euron-agent (backend)

The Python agent backend behind the **Euron Coding Agent** VS Code extension —
also a standalone CLI. Provider-agnostic (Euron/Euri, OpenAI, OpenRouter,
Anthropic, Ollama, or any OpenAI-compatible / self-hosted endpoint).

## Install

```bash
pip install euron-coding-agent   # add: pip install "euron-coding-agent[anthropic]" for Claude
```

## CLI

```bash
euron-agent init                 # scaffold config.yaml + .env (optional)
euron-agent providers            # list providers
euron-agent run "add a /health route to app.py"
euron-agent chat                 # interactive REPL (keeps context)
euron-agent serve --port 0       # run the API/WebSocket server (0 = auto-port)
```

Set a key via environment (`OPENAI_API_KEY`, `EURI_API_KEY`, …) or `config.yaml`.
Built-in provider profiles mean no config file is required — just pick a
provider and supply a key.

## Server

- `GET  /health` — liveness.
- `GET  /providers` — configured providers.
- `POST /agent/run` — one-shot, non-interactive (set `"auto_approve": true`).
- `WS   /ws` — streaming agent with per-action approval (used by the extension).

The WebSocket `init` message accepts `provider`, `model`, `api_key`, and
`base_url` so secrets never need to live on disk.

See the [project README](https://github.com/euron-tech/Euron-coding-agent) for the
full architecture and the VS Code extension.

## License

MIT
