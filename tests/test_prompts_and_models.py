from __future__ import annotations

import pytest
from pydantic import ValidationError

from commit_roaster.diff import Diff
from commit_roaster.models import SUBJECT_MAX_CHARS, RoastResult, SpiceLevel
from commit_roaster.prompts import PromptBuilder


@pytest.mark.parametrize("spice", list(SpiceLevel))
def test_system_prompt_includes_tone(spice: SpiceLevel) -> None:
    prompt = PromptBuilder(spice).system_prompt()
    assert prompt.endswith(spice.tone)
    assert "never the person" in prompt
    assert "untrusted data" in prompt


def test_user_message_wraps_diff(sample_diff: Diff) -> None:
    message = PromptBuilder(SpiceLevel.MILD).user_message(sample_diff)
    assert message.startswith("Roast the staged changes below.")
    assert f"<diff>\n{sample_diff.text}\n</diff>" in message
    assert "too large" not in message


def test_user_message_flags_truncation() -> None:
    diff = Diff(text="abc\n", description="staged changes", original_chars=5_000)
    message = PromptBuilder(SpiceLevel.MEDIUM).user_message(diff)
    assert "cut from 5,000 to 4 characters" in message
    assert "not shown at all" not in message
    assert "Do not raise issues about code you cannot see" in message


def test_user_message_lists_omitted_files_up_to_a_limit() -> None:
    names = tuple(f"tests/test_{n}.py" for n in range(45))
    diff = Diff(text="abc\n", description="staged", original_chars=9_000, omitted_files=names)
    message = PromptBuilder(SpiceLevel.MEDIUM).user_message(diff)
    assert "not shown at all: tests/test_0.py, tests/test_1.py," in message
    assert "tests/test_39.py and 5 more." in message
    assert "tests/test_40.py" not in message


def test_system_prompt_asks_for_concrete_issues() -> None:
    prompt = PromptBuilder(SpiceLevel.SAVAGE).system_prompt()
    assert "concrete failure" in prompt
    assert "[REDACTED AWS access key]" in prompt
    assert "just because you don't recognize it" in prompt


def test_commit_message_with_body(sample_result: RoastResult) -> None:
    assert sample_result.commit_message == (
        "Add debug logging to add()\n\nPrints both operands so the sum can be traced.\n"
    )


def test_commit_message_without_body(sample_result: RoastResult) -> None:
    result = sample_result.model_copy(update={"commit_body": ""})
    assert result.commit_message == "Add debug logging to add()\n"


def test_result_strips_whitespace_and_validates_score() -> None:
    result = RoastResult(
        roast="  meh  ", cringe_score=0, issues=[], commit_subject=" Fix x ", commit_body=""
    )
    assert result.roast == "meh"
    assert result.commit_subject == "Fix x"
    with pytest.raises(ValidationError):
        RoastResult(roast="x", cringe_score=11, issues=[], commit_subject="x", commit_body="")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Fix the parser.", "Fix the parser"),
        ("Fix the parser...", "Fix the parser"),
        ("Fix the\nparser", "Fix the parser"),  # a newline would split the commit message
        ("Bump to v1.2", "Bump to v1.2"),
    ],
)
def test_subject_is_tidied(sample_result: RoastResult, raw: str, expected: str) -> None:
    result = RoastResult.model_validate({**sample_result.model_dump(), "commit_subject": raw})
    assert result.commit_subject == expected


def test_subject_too_long(sample_result: RoastResult) -> None:
    assert not sample_result.subject_too_long
    long_subject = "Add " + "x" * SUBJECT_MAX_CHARS
    data = {**sample_result.model_dump(), "commit_subject": long_subject}
    assert RoastResult.model_validate(data).subject_too_long
