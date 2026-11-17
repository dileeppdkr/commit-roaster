"""Runtime settings, resolved from defaults, then environment, then CLI flags."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Literal, get_args

from commit_roaster.exceptions import ConfigError

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORT_LEVELS: tuple[Effort, ...] = get_args(Effort)

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT: Effort = "medium"
DEFAULT_MAX_TOKENS = 16_000
# ~15k tokens of diff. Larger diffs are truncated, with a warning, not silently.
DEFAULT_MAX_DIFF_CHARS = 60_000
# Generated or vendored files that add noise and cost but nothing worth roasting.
DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    "*.lock",
    "package-lock.json",
    "pnpm-lock.yaml",
    "*.min.js",
    "*.min.css",
    "*.map",
)

ENV_MODEL = "ROAST_MODEL"
ENV_EFFORT = "ROAST_EFFORT"
ENV_MAX_DIFF_CHARS = "ROAST_MAX_DIFF_CHARS"


def parse_effort(value: str) -> Effort:
    """Validate an effort level coming from untyped input (env var or flag)."""
    normalized = value.strip().lower()
    for level in EFFORT_LEVELS:
        if level == normalized:
            return level
    raise ConfigError(f"Invalid effort {value!r}. Choose one of: {', '.join(EFFORT_LEVELS)}.")


def parse_positive_int(value: str, name: str | None = None) -> int:
    """Validate a positive whole number from untyped input (env var or flag).

    ``name`` starts the error message; leave it out when the caller already says
    which input was wrong (argparse prefixes ``argument --flag:``).
    """
    subject = f"{name} " if name else ""
    try:
        number = int(value)
    except ValueError:
        raise ConfigError(f"{subject}must be a whole number, got {value!r}.") from None
    if number <= 0:
        raise ConfigError(f"{subject}must be greater than zero, got {number}.")
    return number


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable configuration for one run."""

    model: str = DEFAULT_MODEL
    effort: Effort = DEFAULT_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS
    exclude_patterns: tuple[str, ...] = DEFAULT_EXCLUDE_PATTERNS

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ConfigError("Model name must not be empty.")
        # Frozen dataclass: store the normalized value ("MEDIUM" -> "medium") directly.
        object.__setattr__(self, "effort", parse_effort(self.effort))
        if self.max_tokens <= 0:
            raise ConfigError("max_tokens must be greater than zero.")
        if self.max_diff_chars <= 0:
            raise ConfigError("max_diff_chars must be greater than zero.")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from defaults overridden by ``ROAST_*`` environment variables."""
        source = os.environ if env is None else env
        settings = cls()
        if model := source.get(ENV_MODEL, "").strip():
            settings = replace(settings, model=model)
        if effort := source.get(ENV_EFFORT, "").strip():
            settings = replace(settings, effort=parse_effort(effort))
        if max_chars := source.get(ENV_MAX_DIFF_CHARS, "").strip():
            settings = replace(
                settings, max_diff_chars=parse_positive_int(max_chars, ENV_MAX_DIFF_CHARS)
            )
        return settings

    def with_overrides(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_diff_chars: int | None = None,
    ) -> Settings:
        """Return a copy with any non-``None`` overrides applied (used for CLI flags)."""
        settings = self
        if model is not None:
            settings = replace(settings, model=model)
        if effort is not None:
            settings = replace(settings, effort=parse_effort(effort))
        if max_diff_chars is not None:
            settings = replace(settings, max_diff_chars=max_diff_chars)
        return settings
