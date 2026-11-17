"""Runtime settings, resolved from defaults, then environment, then CLI flags."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Literal, get_args

from commit_roaster.exceptions import ConfigError

Effort = Literal["low", "medium", "high", "xhigh", "max"]
EFFORT_LEVELS: tuple[Effort, ...] = get_args(Effort)

DEFAULT_MODEL = "claude-haiku-5-5"
DEFAULT_EFFORT: Effort = "low"
DEFAULT_MAX_TOKENS = 16_000
# ~15k tokens of diff. Larger diffs are truncated, with a warning, not silently.
DEFAULT_MAX_DIFF_CHARS = 60_000


def _anywhere(*paths: str) -> tuple[str, ...]:
    """Match ``paths`` in any folder, not just at the root (useful in monorepos)."""
    return tuple(pattern for path in paths for pattern in (path, f"*/{path}"))


# Generated or vendored files that are often committed but add noise and cost, not
# anything worth roasting. Git pathspec rules, from the repository root: `*` also
# matches `/`, and a plain path matches that file or everything under that folder.
#
# One rule: file patterns match in any folder (monorepos); folder patterns match only
# at the root, so `vendor` does not hide a hand-written `app/services/vendor/`.
# Never add a pattern that could match code someone wrote by hand. Extension patterns
# (`*.lock`, `*.lockfile`, `*.min.js`) are safe because the extension itself marks
# the file as generated. Anchor fixed names with `_anywhere` instead of a leading `*`,
# so `zz_generated.*` cannot match `notzz_generated.go`.
DEFAULT_EXCLUDE_PATTERNS: tuple[str, ...] = (
    # Lock files: Gemfile.lock, yarn.lock, composer.lock, poetry.lock, uv.lock,
    # Cargo.lock, Podfile.lock, pubspec.lock, mix.lock, gradle.lockfile, ...
    "*.lock",
    "*.lockfile",
    *_anywhere(
        "package-lock.json",  # npm
        "npm-shrinkwrap.json",  # npm
        "pnpm-lock.yaml",  # pnpm
        "go.sum",  # Go
        "go.work.sum",  # Go workspaces
        "packages.lock.json",  # NuGet (.NET)
        "Package.resolved",  # Swift Package Manager
        ".terraform.lock.hcl",  # Terraform
    ),
    # Minified bundles, source maps and Jest/Vitest snapshots (React, Angular, ...)
    "*.min.js",
    "*.min.css",
    "*.map",
    "*.snap",
    # Rails: schema dumps generated from migrations (the migration is the real change)
    # and precompiled assets
    *_anywhere("db/schema.rb", "db/structure.sql"),
    "public/assets",
    "public/packs",
    # PHP / Laravel: Vite build output and generated IDE helpers
    "public/build",
    *_anywhere("_ide_helper.php", "_ide_helper_models.php", ".phpstorm.meta.php"),
    # Java / Kotlin: Gradle and Maven wrapper scripts (also in Android folders)
    *_anywhere("gradlew", "gradlew.bat", "mvnw", "mvnw.cmd"),
    # Generated code: protobuf/gRPC (Go, Python), Kubernetes deepcopy (Go),
    # build_runner and freezed (Dart/Flutter)
    "*.pb.go",
    "*.pb.gw.go",
    *_anywhere("zz_generated.*"),
    "*_pb2.py",
    "*_pb2.pyi",
    "*_pb2_grpc.py",
    "*.g.dart",
    "*.freezed.dart",
    # .NET: designer files and the Entity Framework model snapshot (like schema.rb)
    "*.Designer.cs",
    *_anywhere("Migrations/*ModelSnapshot.cs"),
    # Xcode project file (Swift / iOS)
    "*.pbxproj",
    # Third-party code checked in by Composer, Bundler, importmap or `go mod vendor`
    "vendor",
)

ENV_MODEL = "ROAST_MODEL"
ENV_EFFORT = "ROAST_EFFORT"
ENV_MAX_DIFF_CHARS = "ROAST_MAX_DIFF_CHARS"
ENV_EXCLUDE = "ROAST_EXCLUDE"


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


def parse_patterns(values: Iterable[str]) -> tuple[str, ...]:
    """Split comma-separated exclude patterns and tidy them.

    A leading ``/`` or ``./`` is dropped, so gitignore-style ``/vendor`` works:
    patterns are always matched from the repository root.
    """
    patterns: list[str] = []
    for value in values:
        for raw in value.split(","):
            pattern = raw.strip().removeprefix("./").lstrip("/")
            if pattern:
                patterns.append(pattern)
    return tuple(patterns)


@dataclass(frozen=True, slots=True)
class Settings:
    """Immutable configuration for one run."""

    model: str = DEFAULT_MODEL
    effort: Effort = DEFAULT_EFFORT
    max_tokens: int = DEFAULT_MAX_TOKENS
    max_diff_chars: int = DEFAULT_MAX_DIFF_CHARS
    extra_excludes: tuple[str, ...] = ()
    default_excludes: bool = True

    @property
    def exclude_patterns(self) -> tuple[str, ...]:
        """Every pattern to leave out of the diff: the defaults (unless turned off) plus extras."""
        defaults = DEFAULT_EXCLUDE_PATTERNS if self.default_excludes else ()
        return defaults + self.extra_excludes

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
        if excludes := parse_patterns([source.get(ENV_EXCLUDE, "")]):
            settings = replace(settings, extra_excludes=excludes)
        return settings

    def with_overrides(
        self,
        *,
        model: str | None = None,
        effort: str | None = None,
        max_diff_chars: int | None = None,
        exclude: Iterable[str] = (),
        default_excludes: bool | None = None,
    ) -> Settings:
        """Return a copy with any overrides applied (used for CLI flags).

        ``exclude`` patterns are added to the ones already set (e.g. from the environment).
        """
        settings = self
        if model is not None:
            settings = replace(settings, model=model)
        if effort is not None:
            settings = replace(settings, effort=parse_effort(effort))
        if max_diff_chars is not None:
            settings = replace(settings, max_diff_chars=max_diff_chars)
        if extra := parse_patterns(exclude):
            settings = replace(settings, extra_excludes=settings.extra_excludes + extra)
        if default_excludes is not None:
            settings = replace(settings, default_excludes=default_excludes)
        return settings
