# Multi-model routing — one model per job, across providers

Model-provider lock-in does not age well: the best/cheapest model rotates every few
months, and IDE vendors keep merging with one cloud. Euron Coding Agent is built so
the *same workflow* runs on **any model from any provider** — and you can assign a
**different model to each phase of the work**, optimizing cost without giving up
quality on the parts that need a strong model.

## The idea

The agent runs in phases. You map each phase to a named model **role**, and each
role can point at a different provider:

| Phase | When it runs | Typical choice |
|---|---|---|
| `plan` | plan mode — research & propose a plan | a strong reasoning model |
| `execute` | normal execution (read/edit/run) | your balanced main model |
| `subagent` | delegated sub-agents (parallel/mechanical work) | a cheap, fast model |
| `verify` | the post-edit critic that reviews diffs | a cheap model |
| `escalate` | auto-jumped to when the cheaper model keeps failing | a strong model |

## Configure it (`config.yaml`)

```yaml
# Define named roles. Each may target a different provider + model.
models:
  planner:  { provider: anthropic, model: claude-opus-4-8 }
  executor: { provider: openai,    model: gpt-5.5 }
  cheap:    { provider: groq,      model: llama-3.3-70b-versatile }
  verifier: { provider: gemini,    model: gemini-2.5-flash }

# Map phases to roles + the cost strategy.
routing:
  strategy: auto        # auto = cheap where safe + escalate on trouble; fixed = never escalate
  plan: planner
  execute: executor
  subagent: cheap
  verify: verifier
  escalate: planner     # the strong model to jump to when quality slips
  escalate_after: 2     # consecutive all-failed steps before escalating
```

Each provider still uses its own key (`ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`GROQ_API_KEY`, `GEMINI_API_KEY`, …), exactly as in the providers list. Mix and
match freely — OpenAI for execution, Anthropic for planning, Groq for the cheap
parallel work, Gemini for verification.

### Shorthand

If a role uses the **active** provider and you only want to change the model, a
string is enough:

```yaml
models:
  cheap: gpt-4o-mini          # same provider as active, cheaper model
routing:
  subagent: cheap
  verify: cheap
```

### Back-compatible shortcut

The older `router:` / `subagent_model:` keys still work and seed a `cheap` / `heavy`
role automatically:

```yaml
router: { cheap: gpt-4o-mini, heavy: gpt-4o }
```

## How cost optimization works

- **Cheap where safe.** Sub-agents and the verifier run on the cheap model by
  default — that is where most of the token volume is.
- **Your model for execution.** The `execute` phase uses the model you consider the
  right cost/quality balance.
- **Escalate only when needed.** With `strategy: auto`, if the model produces
  nothing but failing tool calls for `escalate_after` steps in a row, the agent
  automatically switches to the stronger `escalate` model for the rest of the turn —
  so you never pay for the expensive model on the easy steps, but quality is rescued
  on the hard ones. Use `strategy: fixed` to disable escalation.

Cost is tracked **per model actually used**, so `/usage` reflects the real blended
spend.

## See what's active

```bash
euron-agent models      # CLI: routing table with per-phase model + price
```
…or `/models` inside a chat session. With nothing configured you get a single-model
table (no behavior change); once you add `models:`/`routing:`, each phase shows its
assigned provider, model, and $/1M token price.
