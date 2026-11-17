"""Rough cost of one roast, from Anthropic's list prices.

Only used for the estimate shown after each roast. Prices change, so treat it as
a guide and check https://platform.claude.com/docs/en/about-claude/pricing for the
real numbers.
"""

from __future__ import annotations

from commit_roaster.models import TokenUsage

# USD per million tokens: (input, output). List prices as of November 2026.
_PRICES: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-fable-5-2": (8.00, 40.00),
    "claude-mythos-5-2": (8.00, 40.00),
    "claude-fable-5-1": (10.00, 50.00),
    "claude-mythos-5-1": (10.00, 50.00),
    "claude-fable-5": (10.00, 50.00),
}
# Claude Haiku 5.5 charges more for prompts over 100K input tokens.
_LONG_PROMPT_TOKENS = 100_000
_LONG_PROMPT_PRICES: dict[str, tuple[float, float]] = {"claude-haiku-5-5": (0.50, 2.50)}


def estimate_cost(usage: TokenUsage) -> float | None:
    """Estimated USD cost of the call, or None for a model with no price on file."""
    prices = _PRICES.get(usage.model)
    if prices is None:
        return None
    if usage.input_tokens > _LONG_PROMPT_TOKENS:
        prices = _LONG_PROMPT_PRICES.get(usage.model, prices)
    input_price, output_price = prices
    return (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000


def describe_usage(usage: TokenUsage) -> str:
    """One line for the user, e.g. ``Cost: about $0.0021 (14,210 input + 912 output tokens on
    claude-haiku-5-5)``."""
    tokens = (
        f"{usage.input_tokens:,} input + {usage.output_tokens:,} output tokens on {usage.model}"
    )
    cost = estimate_cost(usage)
    if cost is None:
        return f"Tokens: {tokens} (no price on file for this model)"
    amount = f"${cost:.4f}" if cost < 0.01 else f"${cost:.3f}"
    return f"Cost: about {amount} ({tokens})"
