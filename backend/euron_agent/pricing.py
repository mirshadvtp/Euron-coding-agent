"""Token to cost estimation.

Prices are USD per 1M tokens (input, output), matched by substring against the
model id (longest match wins), so 'gpt-4o-mini-2024-..' resolves correctly.

Two ways a model gets priced:
  1. A per-model override from config (`pricing:` in config.yaml) - authoritative.
  2. The built-in table below.
Unknown models return 0.0 (we only ever estimate, never bill); for those, the CLI
tells you to add a price under `pricing:`.
"""
from __future__ import annotations

from typing import Optional

# (input_per_1M, output_per_1M)
PRICES: dict[str, tuple[float, float]] = {
    # OpenAI
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5.5": (1.25, 10.0),
    "gpt-5": (1.25, 10.0),
    "o4-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),
    "o1-mini": (1.10, 4.40),
    "o1": (15.0, 60.0),
    # Anthropic
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-haiku": (1.00, 5.00),
    "claude-3-5-sonnet": (3.00, 15.0),
    "claude-sonnet": (3.00, 15.0),
    "claude-opus": (15.0, 75.0),
    # Google
    "gemini-2.5-pro": (1.25, 10.0),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.0-flash": (0.10, 0.40),
    "gemini-1.5-flash": (0.075, 0.30),
    "gemini-1.5-pro": (1.25, 5.00),
    "gemini": (0.10, 0.40),
    # others
    "deepseek": (0.27, 1.10),
    "grok": (2.00, 10.0),
    "mistral-large": (2.00, 6.00),
    "mistral": (0.20, 0.60),
    "llama": (0.0, 0.0),
    "qwen": (0.0, 0.0),
}


def _rate_from(table: dict, model: str) -> Optional[tuple[float, float]]:
    m = (model or "").lower()
    for key in sorted(table, key=len, reverse=True):  # longest (most specific) wins
        if key.lower() in m:
            v = table[key]
            if isinstance(v, dict):
                return float(v.get("input", 0)), float(v.get("output", 0))
            return float(v[0]), float(v[1])
    return None


def rate_for(model: str, overrides: Optional[dict] = None) -> Optional[tuple[float, float]]:
    """Return (input_per_1M, output_per_1M) or None if the model is unpriced."""
    if overrides:
        r = _rate_from(overrides, model)
        if r is not None:
            return r
    return _rate_from(PRICES, model)


def is_priced(model: str, overrides: Optional[dict] = None) -> bool:
    return rate_for(model, overrides) is not None


def cost_for(model: str, prompt_tokens: int, completion_tokens: int,
             overrides: Optional[dict] = None) -> float:
    rate = rate_for(model, overrides)
    if rate is None:
        return 0.0
    return prompt_tokens / 1_000_000 * rate[0] + completion_tokens / 1_000_000 * rate[1]
