from __future__ import annotations

import pytest

from commit_roaster.models import TokenUsage
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
