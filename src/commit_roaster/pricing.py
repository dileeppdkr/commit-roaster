"""Rough cost of one roast, from Anthropic's list prices.

Only used for the estimate shown after each roast. Prices change, so treat it as
a guide and check https://platform.claude.com/docs/en/about-claude/pricing for the
real numbers.
"""

from __future__ import annotations

from commit_roaster.models import RequestTokens, TokenUsage

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


# Prompt caching, as a share of the input price. Writes (5-minute cache) cost more than
# normal input; reads cost a small fraction, and less on the newest models.
_CACHE_WRITE = 1.25
_CACHE_READ_DEFAULT = 0.10
_CACHE_READ: dict[str, float] = {
    "claude-opus-5-5": 0.05,
    "claude-sonnet-5-5": 0.05,
    "claude-fable-5-2": 0.025,
    "claude-mythos-5-2": 0.025,
    "claude-fable-5-1": 0.025,
    "claude-mythos-5-1": 0.025,
}


def estimate_cost(usage: TokenUsage) -> float | None:
    """Estimated USD cost of the roast, or None for a model with no price on file.

    Each request is priced on its own: the long-prompt tier depends on that
    request's whole prompt, cached tokens included, not on the roast's totals.
    """
    if usage.model not in _PRICES:
        return None
    return sum(_request_cost(usage.model, request) for request in usage.per_request())


def _request_cost(model: str, request: RequestTokens) -> float:
    prices = _PRICES[model]
    if request.prompt_tokens > _LONG_PROMPT_TOKENS:
        prices = _LONG_PROMPT_PRICES.get(model, prices)
    input_price, output_price = prices
    cache_read_price = input_price * _CACHE_READ.get(model, _CACHE_READ_DEFAULT)
    total = (
        request.input_tokens * input_price
        + request.output_tokens * output_price
        + request.cache_write_tokens * input_price * _CACHE_WRITE
        + request.cache_read_tokens * cache_read_price
    )
    return total / 1_000_000


def describe_usage(usage: TokenUsage) -> str:
    """One line for the user, e.g. ``Cost: about $0.0021 (14,210 input + 912 output tokens on
    claude-haiku-5-5)``. Cached tokens and tool calls are shown when there are any."""
    parts = [f"{usage.input_tokens:,} input", f"{usage.output_tokens:,} output"]
    if usage.cache_read_tokens:
        parts.append(f"{usage.cache_read_tokens:,} cache-read")
    if usage.cache_write_tokens:
        parts.append(f"{usage.cache_write_tokens:,} cache-write")
    details = " + ".join(parts) + " tokens"
    if usage.tool_calls:
        details += f", {usage.tool_calls} tool call{'s' if usage.tool_calls != 1 else ''},"
    details += f" on {usage.model}"
    cost = estimate_cost(usage)
    if cost is None:
        return f"Tokens: {details} (no price on file for this model)"
    amount = f"${cost:.4f}" if cost < 0.01 else f"${cost:.3f}"
    return f"Cost: about {amount} ({details})"
