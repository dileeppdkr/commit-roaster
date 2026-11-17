from __future__ import annotations

import pytest

from commit_roaster.config import (
    DEFAULT_EFFORT,
    DEFAULT_EXCLUDE_PATTERNS,
    DEFAULT_MODEL,
    Settings,
    parse_effort,
)
from commit_roaster.exceptions import ConfigError


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
