from __future__ import annotations

import pytest

from commit_roaster.models import RequestTokens, TokenUsage
from commit_roaster.pricing import describe_usage, estimate_cost


def test_estimate_uses_input_and_output_prices() -> None:
    usage = TokenUsage(model="claude-opus-5-5", input_tokens=1_000_000, output_tokens=100_000)
    assert estimate_cost(usage) == pytest.approx(4.00 + 2.00)


def test_long_haiku_prompts_cost_more() -> None:
    short = TokenUsage(model="claude-haiku-5-5", input_tokens=100_000, output_tokens=0)
    long = TokenUsage(model="claude-haiku-5-5", input_tokens=100_001, output_tokens=0)
    assert estimate_cost(short) == pytest.approx(0.01)
    assert estimate_cost(long) == pytest.approx(100_001 * 0.50 / 1_000_000)


def test_unknown_model_has_no_estimate() -> None:
    usage = TokenUsage(model="some-new-model", input_tokens=10, output_tokens=5)
    assert estimate_cost(usage) is None
    assert describe_usage(usage) == (
        "Tokens: 10 input + 5 output tokens on some-new-model (no price on file for this model)"
    )


@pytest.mark.parametrize(
    ("model", "expected"),
    [("claude-haiku-5-5", "Cost: about $0.0019 ("), ("claude-sonnet-5-5", "Cost: about $0.038 (")],
)
def test_describe_usage_formats_small_and_large_costs(model: str, expected: str) -> None:
    usage = TokenUsage(model=model, input_tokens=14_210, output_tokens=912)
    assert describe_usage(usage).startswith(expected)


def test_cache_reads_and_writes_are_priced() -> None:
    usage = TokenUsage(
        model="claude-opus-5-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    # Opus 5.5 input is $4/M: reads at 0.05x, 5-minute writes at 1.25x.
    assert estimate_cost(usage) == pytest.approx(0.20 + 5.00)


def test_describe_usage_mentions_cache_and_tools() -> None:
    usage = TokenUsage(
        model="claude-haiku-5-5",
        input_tokens=5_000,
        output_tokens=800,
        cache_read_tokens=40_000,
        cache_write_tokens=6_000,
        tool_calls=3,
    )
    assert describe_usage(usage).endswith(
        "(5,000 input + 800 output + 40,000 cache-read + 6,000 cache-write tokens, "
        "3 tool calls, on claude-haiku-5-5)"
    )


def test_long_prompt_tier_counts_cached_tokens_per_request() -> None:
    cached_long = RequestTokens(input_tokens=10_000, output_tokens=0, cache_read_tokens=95_000)
    two_short = (
        RequestTokens(input_tokens=60_000, output_tokens=0),
        RequestTokens(input_tokens=60_000, output_tokens=0),
    )
    long = TokenUsage("claude-haiku-5-5", 10_000, 0, 95_000, requests=(cached_long,))
    short = TokenUsage("claude-haiku-5-5", 120_000, 0, requests=two_short)
    # 105k-token prompt: long-prompt prices ($0.50/M input, reads at 0.1x).
    assert estimate_cost(long) == pytest.approx((10_000 * 0.50 + 95_000 * 0.05) / 1_000_000)
    # Two 60k prompts stay on the normal price even though they sum past 100k.
    assert estimate_cost(short) == pytest.approx(120_000 * 0.10 / 1_000_000)
