"""Where the diff comes from.

:class:`DiffSource` is a template: subclasses only say how to read the raw
text, and the base class handles the shared rules (empty check, secret
redaction, size limit).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TextIO

from commit_roaster.exceptions import ConfigError, EmptyDiffError
from commit_roaster.git import GitClient
from commit_roaster.patch import path_matches, split_sections
from commit_roaster.redact import Redaction, redact_secrets

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Diff:
    """A diff ready to send to the model."""

    text: str
    description: str
    original_chars: int
    # Files changed in the full diff whose section was cut off entirely by the size limit.
    omitted_files: tuple[str, ...] = ()
    # Credentials replaced with [REDACTED ...] markers before anything is sent.
    redactions: tuple[Redaction, ...] = ()

    @property
    def truncated(self) -> bool:
        return self.original_chars > len(self.text)


def truncate_at_line(text: str, limit: int) -> str:
    """Cut ``text`` to at most ``limit`` characters, ending on a whole line when possible."""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    last_newline = cut.rfind("\n")
    return cut[: last_newline + 1] if last_newline > 0 else cut


def exclude_files(diff_text: str, patterns: Sequence[str]) -> str:
    """Drop the per-file sections of a git diff whose path matches any pattern.

    Does for piped diffs what git's ``:(exclude)`` pathspecs do for the other
    sources. Text outside ``diff --git`` sections (e.g. ``git show`` commit
    headers) is kept, and non-git diffs pass through unchanged.
    """
    if not patterns:
        return diff_text
    return "".join(
        section.text
        for section in split_sections(diff_text)
        if not (section.is_file and any(path_matches(p, patterns) for p in section.paths))
    )


def files_after(diff_text: str, kept_chars: int) -> tuple[str, ...]:
    """Names of the files whose section starts at or after ``kept_chars``,
    i.e. the files a cut at that point leaves out completely."""
    omitted: list[str] = []
    offset = 0
    for section in split_sections(diff_text):
        if offset >= kept_chars and section.is_file and section.name:
            omitted.append(section.name)
        offset += len(section.text)
    return tuple(omitted)


class DiffSource(ABC):
    """Produces a :class:`Diff` from somewhere."""

    @property
    @abstractmethod
    def description(self) -> str:
        """Human-readable name, e.g. ``"staged changes"``."""

    @property
    def empty_hint(self) -> str:
        """Extra advice shown when there is nothing to roast."""
        return ""

    @abstractmethod
    def read_raw(self) -> str:
        """Return the full, untruncated diff text."""

    def load(self, max_chars: int) -> Diff:
        raw = self.read_raw()
        if not raw.strip():
            message = f"Nothing to roast in the {self.description}."
            raise EmptyDiffError(f"{message} {self.empty_hint}".strip())
        # Redact first, so no secret can survive into the text that is sent.
        raw, redactions = redact_secrets(raw)
        text = truncate_at_line(raw, max_chars)
        diff = Diff(
            text=text,
            description=self.description,
            original_chars=len(raw),
            omitted_files=files_after(raw, len(text)) if len(text) < len(raw) else (),
            redactions=redactions,
        )
        if diff.truncated:
            logger.info("Diff truncated from %d to %d characters", len(raw), len(text))
        return diff


class StagedDiffSource(DiffSource):
    """Changes staged with ``git add`` (what the next commit will contain)."""

    def __init__(self, git: GitClient, exclude_patterns: Sequence[str] = ()) -> None:
        self._git = git
        self._exclude_patterns = tuple(exclude_patterns)

    @property
    def description(self) -> str:
        return "staged changes"

    @property
    def empty_hint(self) -> str:
        return "Stage something with `git add` first, or use --range."

    def read_raw(self) -> str:
        self._git.ensure_repository()
        return self._git.diff(staged=True, exclude_patterns=self._exclude_patterns)


class RevisionRangeDiffSource(DiffSource):
    """Any revision or range git understands, e.g. ``HEAD~3..HEAD`` or ``main...``."""

    def __init__(
        self, git: GitClient, revision_range: str, exclude_patterns: Sequence[str] = ()
    ) -> None:
        revision_range = revision_range.strip()
        if not revision_range:
            raise ConfigError("Revision range must not be empty.")
        if revision_range.startswith("-"):
            # Would otherwise be parsed by git as an option.
            raise ConfigError(f"Invalid revision range {revision_range!r}.")
        self._git = git
        self._revision_range = revision_range
        self._exclude_patterns = tuple(exclude_patterns)

    @property
    def description(self) -> str:
        return f"revision range {self._revision_range}"

    def read_raw(self) -> str:
        self._git.ensure_repository()
        return self._git.diff(self._revision_range, exclude_patterns=self._exclude_patterns)


class StdinDiffSource(DiffSource):
    """A diff piped in, e.g. ``git show HEAD | roast --stdin``."""

    def __init__(self, stream: TextIO, exclude_patterns: Sequence[str] = ()) -> None:
        self._stream = stream
        self._exclude_patterns = tuple(exclude_patterns)

    @property
    def description(self) -> str:
        return "diff from stdin"

    def read_raw(self) -> str:
        return exclude_files(self._stream.read(), self._exclude_patterns)
