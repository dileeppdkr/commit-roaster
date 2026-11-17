from __future__ import annotations

import json

import pytest

from commit_roaster.diff import Diff
from commit_roaster.models import RoastResult
from commit_roaster.render import JsonRenderer, TextRenderer
from tests.fakes import SAMPLE_USAGE


def test_text_renderer_plain(sample_result: RoastResult, sample_diff: Diff) -> None:
    output = TextRenderer().render(sample_result, sample_diff)
    assert "cringe score: 6/10" in output
    assert "  MEDIUM bugs  app.py:2" in output
    assert "         Leftover debug print in add() spams stdout" in output
    assert "  Add debug logging to add()" in output
    assert "\033[" not in output
    assert "Note:" not in output


def test_text_renderer_skips_empty_issues(sample_result: RoastResult, sample_diff: Diff) -> None:
    result = sample_result.model_copy(update={"issues": []})
    assert "Issues spotted" not in TextRenderer().render(result, sample_diff)


@pytest.mark.parametrize(("score", "color"), [(2, "\033[32m"), (5, "\033[33m"), (9, "\033[31m")])
def test_text_renderer_colors_score(
    sample_result: RoastResult, sample_diff: Diff, score: int, color: str
) -> None:
    result = sample_result.model_copy(update={"cringe_score": score})
    assert f"{color}{score}/10" in TextRenderer(color=True).render(result, sample_diff)


def test_text_renderer_notes_truncation(sample_result: RoastResult) -> None:
    diff = Diff(text="abc\n", description="staged changes", original_chars=9_000)
    assert "first 4 of 9,000 characters" in TextRenderer().render(sample_result, diff)


def test_json_renderer(sample_result: RoastResult, sample_diff: Diff) -> None:
    payload = json.loads(JsonRenderer().render(sample_result, sample_diff))
    assert payload["cringe_score"] == 6
    assert payload["commit_message"] == sample_result.commit_message
    assert payload["diff"] == {
        "source": "staged changes",
        "truncated": False,
        "chars_reviewed": len(sample_diff.text),
        "chars_total": len(sample_diff.text),
        "omitted_files": [],
        "redactions": [],
    }
    assert "usage" not in payload


def test_json_renderer_includes_usage_and_cost(
    sample_result: RoastResult, sample_diff: Diff
) -> None:
    payload = json.loads(JsonRenderer().render(sample_result, sample_diff, SAMPLE_USAGE))
    assert payload["usage"] == {
        "model": "claude-haiku-5-5",
        "input_tokens": 1_200,
        "output_tokens": 300,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "tool_calls": 0,
        "estimated_cost_usd": pytest.approx(0.00027),
    }


def test_text_renderer_shows_cost(sample_result: RoastResult, sample_diff: Diff) -> None:
    output = TextRenderer().render(sample_result, sample_diff, SAMPLE_USAGE)
    assert output.endswith(
        "Cost: about $0.0003 (1,200 input + 300 output tokens on claude-haiku-5-5)"
    )
