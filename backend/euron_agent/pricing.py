"""Best-effort token → cost estimation.

Prices are USD per 1M tokens (input, output). Matched by substring against the
model id, so 'gpt-4o-mini-2024-..' still resolves. Unknown models cost 0 (we
only ever *estimate*; never bill). Self-hosted/local models are free.
"""
from __future__ import annotations

# (input_per_1M, output_per_1M)
PRICES: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.0),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    "o3-mini": (1.10, 4.40),
    "o3": (2.00, 8.00),
    "claude-haiku": (0.80, 4.00),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude-sonnet": (3.00, 15.0),
    "claude-3-5-sonnet": (3.00, 15.0),
    "claude-opus": (15.0, 75.0),
    "deepseek": (0.27, 1.10),
    "qwen": (0.0, 0.0),
    "llama": (0.0, 0.0),
}


def cost_for(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    m = (model or "").lower()
    rate = None
    # longest matching key wins (more specific)
    for key in sorted(PRICES, key=len, reverse=True):
        if key in m:
            rate = PRICES[key]
            break
    if rate is None:
        return 0.0
    return prompt_tokens / 1_000_000 * rate[0] + completion_tokens / 1_000_000 * rate[1]
