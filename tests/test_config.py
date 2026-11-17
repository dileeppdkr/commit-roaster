from __future__ import annotations

import pytest

from commit_roaster.config import (
    DEFAULT_EFFORT,
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_MODEL,
    Settings,
    parse_effort,
    parse_focus,
)
from commit_roaster.exceptions import ConfigError
from commit_roaster.models import ReviewFocus


def test_defaults() -> None:
    settings = Settings()
    assert settings.model == DEFAULT_MODEL
    assert settings.effort == DEFAULT_EFFORT
    assert "*.lock" in settings.exclude_patterns


def test_from_env_reads_overrides() -> None:
    settings = Settings.from_env(
        {"ROAST_MODEL": "claude-sonnet-5-5", "ROAST_EFFORT": "HIGH", "ROAST_MAX_DIFF_CHARS": "500"}
    )
    assert settings.model == "claude-sonnet-5-5"
    assert settings.effort == "high"
    assert settings.max_diff_chars == 500


def test_from_env_ignores_blank_values() -> None:
    assert Settings.from_env({"ROAST_MODEL": "  ", "ROAST_EFFORT": ""}) == Settings()


@pytest.mark.parametrize("value", ["abc", "0", "-5"])
def test_from_env_rejects_bad_max_chars(value: str) -> None:
    with pytest.raises(ConfigError, match="ROAST_MAX_DIFF_CHARS"):
        Settings.from_env({"ROAST_MAX_DIFF_CHARS": value})


def test_from_env_adds_extra_excludes() -> None:
    settings = Settings.from_env({"ROAST_EXCLUDE": " app/assets/builds, /vendor/x ,,./tmp "})
    assert settings.extra_excludes == ("app/assets/builds", "vendor/x", "tmp")
    assert settings.exclude_patterns == (*DEFAULT_EXCLUDE_PATTERNS, *settings.extra_excludes)


def test_flag_excludes_add_to_env_excludes() -> None:
    settings = Settings.from_env({"ROAST_EXCLUDE": "a"}).with_overrides(exclude=["b,c", "d"])
    assert settings.extra_excludes == ("a", "b", "c", "d")


def test_default_excludes_can_be_turned_off() -> None:
    settings = Settings().with_overrides(exclude=["coverage"], default_excludes=False)
    assert settings.exclude_patterns == ("coverage",)


def test_direct_construction_normalizes_effort() -> None:
    assert Settings(effort=" MEDIUM ").effort == "medium"  # type: ignore[arg-type]


def test_parse_effort_rejects_unknown_level() -> None:
    with pytest.raises(ConfigError, match="Invalid effort"):
        parse_effort("turbo")


def test_with_overrides_skips_none() -> None:
    base = Settings(model="base-model")
    assert base.with_overrides() == base
    updated = base.with_overrides(model="other", effort="low", max_diff_chars=10)
    assert (updated.model, updated.effort, updated.max_diff_chars) == ("other", "low", 10)


@pytest.mark.parametrize(
    "kwargs",
    [{"model": " "}, {"max_tokens": 0}, {"max_diff_chars": 0}],
)
def test_invalid_settings_raise(kwargs: dict[str, object]) -> None:
    with pytest.raises(ConfigError):
        Settings(**kwargs)  # type: ignore[arg-type]


def test_parse_focus() -> None:
    assert parse_focus(["sql, Security", "sql"]) == (ReviewFocus.SQL, ReviewFocus.SECURITY)
    assert parse_focus(["all"]) == tuple(ReviewFocus)
    assert parse_focus([""]) == ()
    with pytest.raises(ConfigError, match="Unknown focus 'style'"):
        parse_focus(["style"])


def test_review_settings_from_env() -> None:
    settings = Settings.from_env(
        {
            "ROAST_FOCUS": "reuse,side-effects",
            "ROAST_DEEP": "yes",
            "ROAST_MAX_TOOL_CALLS": "8",
            "ROAST_STACK": "rails",
            "ROAST_RULES": "",
            "ROAST_MAX_TOKENS": "4000",
        }
    )
    assert settings.focus == (ReviewFocus.REUSE, ReviewFocus.SIDE_EFFECTS)
    assert settings.deep is True
    assert settings.max_tool_calls == 8
    assert settings.stacks == ("rails",)
    assert settings.rules_file == ""
    assert settings.max_tokens == 4_000


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"ROAST_DEEP": "maybe"}, "ROAST_DEEP must be true or false"),
        ({"ROAST_MAX_TOOL_CALLS": "0"}, "ROAST_MAX_TOOL_CALLS must be greater than zero"),
        ({"ROAST_MAX_TOKENS": "lots"}, "ROAST_MAX_TOKENS must be a whole number"),
    ],
)
def test_bad_review_env(env: dict[str, str], message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Settings.from_env(env)


def test_flags_override_review_settings() -> None:
    settings = Settings.from_env({"ROAST_FOCUS": "sql"}).with_overrides(
        focus=["tests"], deep=True, max_tool_calls=3, stacks="go", rules_file="R.md"
    )
    assert (settings.focus, settings.deep, settings.max_tool_calls) == (
        (ReviewFocus.TESTS,),
        True,
        3,
    )
    assert (settings.stacks, settings.rules_file) == (("go",), "R.md")
