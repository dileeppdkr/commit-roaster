"""Exception hierarchy.

Every error the tool expects to happen derives from :class:`CommitRoasterError`
and carries the process exit code the CLI should return for it. Anything else
is a bug and is allowed to surface as a traceback.
"""

from __future__ import annotations

from enum import IntEnum


class ExitCode(IntEnum):
    OK = 0
    ERROR = 1
    USAGE = 2
    NOTHING_TO_ROAST = 3
    INTERRUPTED = 130


class CommitRoasterError(Exception):
    """Base class for all expected errors."""

    exit_code: ExitCode = ExitCode.ERROR


class ConfigError(CommitRoasterError):
    """Invalid settings, flags or environment."""

    exit_code = ExitCode.USAGE


class GitError(CommitRoasterError):
    """A git command failed or git is unavailable."""


class GitCommandError(GitError):
    """git ran but exited with a non-zero status."""


class NotAGitRepositoryError(GitError):
    """The working directory is not inside a git repository."""

    exit_code = ExitCode.USAGE


class EmptyDiffError(CommitRoasterError):
    """There are no changes to roast."""

    exit_code = ExitCode.NOTHING_TO_ROAST


class RoastError(CommitRoasterError):
    """The Claude API call failed."""


class RoastRefusedError(RoastError):
    """Claude declined to roast this diff."""


class RoastIncompleteError(RoastError):
    """Claude's reply was cut off or did not match the expected shape."""
