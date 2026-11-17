"""Reading git's unified diff format: split a diff into per-file sections."""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

FILE_HEADER = "diff --git "
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


@dataclass(frozen=True, slots=True)
class DiffSection:
    """One ``diff --git`` file section, or the text between them (e.g. a commit header)."""

    lines: tuple[str, ...]
    # The file's current name (the new name after a rename); None outside file sections.
    name: str | None = None
    # Every name the header mentions: old and new, for renames and copies.
    paths: frozenset[str] = frozenset()

    @property
    def is_file(self) -> bool:
        return bool(self.lines) and self.lines[0].startswith(FILE_HEADER)

    @property
    def text(self) -> str:
        return "".join(self.lines)


def unquote(name: str) -> str:
    """Undo git's C-style quoting (``\\t``, ``\\"``, octal UTF-8 bytes) if present."""
    if len(name) < 2 or not (name.startswith('"') and name.endswith('"')):
        return name
    try:
        raw = name[1:-1].encode("utf-8").decode("unicode_escape").encode("latin-1")
    except UnicodeError:
        return name
    return raw.decode("utf-8", errors="replace")


def _strip_prefix(name: str, prefix: str) -> str | None:
    name = unquote(name)
    return name[len(prefix) :] if name.startswith(prefix) else None


def path_matches(path: str, patterns: Sequence[str]) -> bool:
    """Approximate git's default pathspec rules: ``*`` also matches ``/``, and a
    plain name matches that path or anything under it."""
    for pattern in patterns:
        directory = pattern.rstrip("/")
        if fnmatchcase(path, pattern) or path.startswith(f"{directory}/"):
            return True
    return False


def _file_section(lines: tuple[str, ...]) -> DiffSection:
    """Read the names from one ``diff --git`` section's header lines."""
    paths: set[str] = set()
    header_name: str | None = None
    new_name: str | None = None
    header = lines[0].rstrip("\r\n")
    for pattern in _HEADER_PATHS:
        if match := pattern.match(header):
            a_name, b_name = match.groups()
            old = _strip_prefix(a_name, "a/")
            header_name = _strip_prefix(b_name, "b/")
            paths.update(name for name in (old, header_name) if name)
    for line in lines[1:]:
        if line.startswith("@@"):
            break  # hunks start here; anything below is file content, not headers
        for line_prefix, path_prefix in _PATH_LINES:
            if line.startswith(line_prefix):
                # git appends a tab to ---/+++ names that contain spaces
                raw_name = line[len(line_prefix) :].rstrip("\r\n").removesuffix("\t")
                if name := _strip_prefix(raw_name, path_prefix):
                    paths.add(name)
                    if line_prefix == "+++ ":
                        new_name = name
    name = header_name or new_name or (min(paths) if paths else None)
    return DiffSection(lines=lines, name=name, paths=frozenset(paths))


def split_sections(diff_text: str) -> list[DiffSection]:
    """Split a diff into file sections and the text around them.

    Joining every section's ``text`` gives back ``diff_text`` exactly. A new section
    starts at each ``diff --git`` line and at each ``commit`` line (``git show`` and
    ``git log -p`` output). Non-git diffs come back as one non-file section.
    """
    groups: list[list[str]] = [[]]
    for line in diff_text.splitlines(keepends=True):
        if line.startswith((FILE_HEADER, "commit ")):
            groups.append([])
        groups[-1].append(line)
    sections = []
    for group in groups:
        if not group:
            continue
        lines = tuple(group)
        is_file = lines[0].startswith(FILE_HEADER)
        sections.append(_file_section(lines) if is_file else DiffSection(lines=lines))
    return sections
