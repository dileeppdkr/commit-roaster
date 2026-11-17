"""Where the diff comes from.

:class:`DiffSource` is a template: subclasses only say how to read the raw
text, and the base class handles the shared rules (empty check, size limit).
"""

from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from typing import TextIO

from commit_roaster.exceptions import ConfigError, EmptyDiffError
from commit_roaster.git import GitClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Diff:
    """A diff ready to send to the model."""

    text: str
    description: str
    original_chars: int

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


_FILE_HEADER = "diff --git "
_QUOTED = r'"(?:[^"\\]|\\.)*"'
_HEADER_PATHS = (
    re.compile(r"^diff --git (a/.+) (b/.+)$"),
    # git quotes names with special or non-ASCII characters: "a/caf\303\251.lock"
    re.compile(rf"^diff --git ({_QUOTED}) ({_QUOTED})$"),
)
# (line prefix, path prefix git puts in front of the name)
_PATH_LINES = (
    ("--- ", "a/"),
    ("+++ ", "b/"),
    ("rename from ", ""),
    ("rename to ", ""),
    ("copy from ", ""),
    ("copy to ", ""),
)


def _unquote(name: str) -> str:
    """Undo git's C-style quoting (``\\t``, ``\\"``, octal UTF-8 bytes) if present."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    try:
        raw = name[1:-1].encode("utf-8").decode("unicode_escape").encode("latin-1")
    except UnicodeError:
        return name
    return raw.decode("utf-8", errors="replace")


def _strip_prefix(name: str, prefix: str) -> str | None:
    name = _unquote(name)
    return name[len(prefix) :] if name.startswith(prefix) else None


def _path_matches(path: str, patterns: Sequence[str]) -> bool:
    """Approximate git's default pathspec rules: ``*`` also matches ``/``, and a
    plain name matches that path or anything under it."""
    for pattern in patterns:
        directory = pattern.rstrip("/")
        if fnmatchcase(path, pattern) or path.startswith(f"{directory}/"):
            return True
    return False


def _section_paths(lines: Sequence[str]) -> set[str]:
    """Every path named in one ``diff --git`` section's header lines."""
    candidates: set[str | None] = set()
    header = lines[0].rstrip("\r\n")
    for pattern in _HEADER_PATHS:
        if match := pattern.match(header):
            a_name, b_name = match.groups()
            candidates.add(_strip_prefix(a_name, "a/"))
            candidates.add(_strip_prefix(b_name, "b/"))
    for line in lines[1:]:
        if line.startswith("@@"):
            break  # hunks start here; anything below is file content, not headers
        for line_prefix, path_prefix in _PATH_LINES:
            if line.startswith(line_prefix):
                # git appends a tab to ---/+++ names that contain spaces
                name = line[len(line_prefix) :].rstrip("\r\n").removesuffix("\t")
                candidates.add(_strip_prefix(name, path_prefix))
    return {path for path in candidates if path}


def exclude_files(diff_text: str, patterns: Sequence[str]) -> str:
    """Drop the per-file sections of a git diff whose path matches any pattern.

    Does for piped diffs what git's ``:(exclude)`` pathspecs do for the other
    sources. Text outside ``diff --git`` sections (e.g. ``git show`` commit
    headers) is kept, and non-git diffs pass through unchanged.
    """
    if not patterns:
        return diff_text
    sections: list[list[str]] = [[]]
    for line in diff_text.splitlines(keepends=True):
        if line.startswith((_FILE_HEADER, "commit ")):
            sections.append([])
        sections[-1].append(line)
    kept = [
        section
        for section in sections
        if not (
            section
            and section[0].startswith(_FILE_HEADER)
            and any(_path_matches(path, patterns) for path in _section_paths(section))
        )
    ]
    return "".join(line for section in kept for line in section)


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
        text = truncate_at_line(raw, max_chars)
        diff = Diff(text=text, description=self.description, original_chars=len(raw))
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
