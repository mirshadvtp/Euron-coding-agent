"""Multi-model routing — one model per job, across providers, cost-aware.

You can assign a different model (from any configured provider) to each *phase* of
the agent's work:

    models:                       # named roles; each may be a different provider
      planner:  { provider: anthropic, model: claude-opus-4-8 }
      executor: { provider: openai,    model: gpt-5.5 }
      cheap:    { provider: groq,      model: llama-3.3-70b-versatile }
      verifier: { provider: gemini,    model: gemini-2.5-flash }
    routing:
      strategy: auto              # auto = cheap-where-safe + escalate on trouble
      plan: planner               # model used in plan mode
      execute: executor           # model for normal execution
      subagent: cheap             # model for delegated sub-agents
      verify: cheap               # model for the post-edit verifier
      escalate: planner           # stronger model to jump to when quality slips
      escalate_after: 2           # consecutive all-failed steps before escalating

The goal is to spend the least money that still gets the job done: cheap models for
mechanical / parallel work, your main model for execution, a strong reasoning model
for planning — and an automatic **escalation** to a stronger model when the cheaper
one keeps failing. If nothing is configured, behaviour is identical to before
(single active model, with `router.cheap` / `subagent_model` still honored).
"""
from __future__ import annotations

from dataclasses import replace

from . import pricing
from .config import AgentConfig, Config, ProviderConfig
from .llm import build_client

# Phase -> ordered fallback role names if the phase isn't explicitly routed.
_PHASE_DEFAULTS = {
    "plan": ["planner", "plan", "heavy", "executor", "execute"],
    "execute": ["executor", "execute"],
    "subagent": ["subagent", "cheap", "executor", "execute"],
    "verify": ["verifier", "verify", "cheap", "executor", "execute"],
    "escalate": ["escalate", "heavy", "planner", "executor", "execute"],
}


class ModelRouter:
    def __init__(self, config: Config):
        self.config = config
        self.routing: dict = dict(config.routing or {})
        self.strategy = str(self.routing.get("strategy", "auto")).lower()
        self.escalate_after = int(self.routing.get("escalate_after", 2))
        self.roles: dict[str, dict] = self._build_roles()
        self._clients: dict[str, object] = {}

    # --- role resolution -------------------------------------------------- #
    def _build_roles(self) -> dict[str, dict]:
        roles: dict[str, dict] = {}
        for name, entry in (self.config.models or {}).items():
            if isinstance(entry, str):          # shorthand: "role: model-id"
                entry = {"model": entry}
            roles[name] = dict(entry or {})
        # Implicit roles for back-compat / zero-config.
        roles.setdefault("execute", {"provider": self.config.provider.name,
                                     "model": self.config.provider.model})
        roles.setdefault("executor", roles["execute"])
        cheap = (self.config.router or {}).get("cheap") or self.config.subagent_model
        if cheap and "cheap" not in roles:
            roles["cheap"] = {"model": cheap}
        heavy = (self.config.router or {}).get("heavy")
        if heavy and "heavy" not in roles:
            roles["heavy"] = {"model": heavy}
        return roles

    def role_for_phase(self, phase: str) -> str:
        explicit = self.routing.get(phase)
        if explicit and explicit in self.roles:
            return explicit
        for cand in _PHASE_DEFAULTS.get(phase, ["execute"]):
            if cand in self.roles:
                return cand
        return "execute"

    def _provider_for_role(self, role: str) -> ProviderConfig:
        entry = self.roles.get(role, {})
        base_name = entry.get("provider") or self.config.provider.name
        # Prefer the live active provider (it may carry a runtime-injected key).
        if base_name == self.config.provider.name:
            base = self.config.provider
        else:
            base = self.config.all_providers.get(base_name, self.config.provider)
        over: dict = {}
        if entry.get("model"):
            over["model"] = entry["model"]
        if "temperature" in entry:
            over["temperature"] = float(entry["temperature"])
        if "max_tokens" in entry:
            over["max_tokens"] = int(entry["max_tokens"])
        return replace(base, **over) if over else base

    def _agent_for_role(self, role: str) -> AgentConfig:
        entry = self.roles.get(role, {})
        if "fallback_models" in entry:
            return replace(self.config.agent, fallback_models=list(entry["fallback_models"]))
        return self.config.agent

    # --- public API ------------------------------------------------------- #
    def provider_for_phase(self, phase: str) -> ProviderConfig:
        return self._provider_for_role(self.role_for_phase(phase))

    def model_for_phase(self, phase: str) -> str:
        return self.provider_for_phase(phase).model

    def client_for_phase(self, phase: str):
        role = self.role_for_phase(phase)
        if role not in self._clients:
            self._clients[role] = build_client(self._provider_for_role(role),
                                               self._agent_for_role(role))
        return self._clients[role]

    def is_default_phase(self, phase: str) -> bool:
        """True if `phase` resolves to the active provider's model (no divergence).
        Lets callers reuse an injected/active client instead of building a new one."""
        p = self.provider_for_phase(phase)
        return p.name == self.config.provider.name and p.model == self.config.provider.model

    def has_distinct_escalation(self) -> bool:
        """True if the escalate phase resolves to a different model than execute."""
        return self.model_for_phase("escalate") != self.model_for_phase("execute")

    @staticmethod
    def _blended_rate(model: str, overrides: dict | None = None) -> float | None:
        r = pricing.rate_for(model, overrides)
        if r is None:
            return None
        # Weight output heavier (it dominates agent cost): input + 3*output.
        return r[0] + 3 * r[1]

    def summary(self) -> list[dict]:
        """One row per phase: which role/provider/model and its price (visibility)."""
        rows = []
        for phase in ("plan", "execute", "subagent", "verify", "escalate"):
            role = self.role_for_phase(phase)
            p = self._provider_for_role(role)
            rate = pricing.rate_for(p.model, self.config.pricing)
            rows.append({
                "phase": phase,
                "role": role,
                "provider": p.name,
                "model": p.model,
                "in_per_1m": rate[0] if rate else None,
                "out_per_1m": rate[1] if rate else None,
            })
        return rows
