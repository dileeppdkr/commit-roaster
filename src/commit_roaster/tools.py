"""Read-only tools that let Claude explore the repository during a ``--deep`` review.

Every tool is read-only and confined to the repository: paths are resolved and
must stay inside the root, ``.git`` is off limits, only files tracked by git can
be read (so git-ignored secrets like ``.env`` stay private), git always runs with an
argument list (never a shell), and every result is size-capped and passed
through the same secret redaction as the diff.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from commit_roaster.exceptions import GitError
from commit_roaster.git import GitClient
from commit_roaster.redact import Redaction, redact_file

logger = logging.getLogger(__name__)

_READ_LINES = 300  # lines per read_file call; Claude can page with start_line
_MAX_RESULT_CHARS = 24_000  # per tool result, about 6k tokens
_MAX_SEARCH_HITS = 80
_MAX_LISTED_FILES = 300
_MAX_LINE_CHARS = 300


class _ReadFile(BaseModel):
    path: str
    start_line: int


class _SearchCode(BaseModel):
    pattern: str
    path_glob: str


class _ListFiles(BaseModel):
    path_glob: str


def _schema(properties: dict[str, dict[str, str]]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


# Sorted by name and never changed between turns, so the prompt cache stays valid.
TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": (
            "List tracked files in the repository. path_glob filters them, e.g. "
            "'app/models/*' or '*.tsx'; '*' also matches '/'. Use '' for all files. "
            f"Returns at most {_MAX_LISTED_FILES} paths."
        ),
        "strict": True,
        "input_schema": _schema(
            {"path_glob": {"type": "string", "description": "Glob, or '' for all files."}}
        ),
    },
    {
        "name": "read_file",
        "description": (
            "Read a file tracked by git from the working tree, with line numbers. Returns up "
            f"to {_READ_LINES} lines starting at start_line (1 for the beginning). Use it to "
            "read callers, similar code, models and helpers that the diff doesn't show."
        ),
        "strict": True,
        "input_schema": _schema(
            {
                "path": {"type": "string", "description": "Path from the repository root."},
                "start_line": {"type": "integer", "description": "First line, 1-based."},
            }
        ),
    },
    {
        "name": "search_code",
        "description": (
            "Search tracked files with a regular expression (git grep; Perl syntax such "
            "as \\w and \\d works on most setups). "
            "Use it to find callers of a changed function, existing helpers that new code "
            "may duplicate, or how the codebase does similar things. path_glob limits the "
            f"search ('' for everywhere). Returns at most {_MAX_SEARCH_HITS} matching lines."
        ),
        "strict": True,
        "input_schema": _schema(
            {
                "pattern": {"type": "string", "description": "Regex to search for."},
                "path_glob": {"type": "string", "description": "Glob, or '' for all files."},
            }
        ),
    },
]


class ToolError(Exception):
    """A tool call that can't be served; the message goes back to Claude."""


class RepoTools:
    """Runs Claude's tool calls against one repository."""

    def __init__(self, git: GitClient, root: Path) -> None:
        self._git = git
        self._root = root.resolve()
        self.redactions: list[Redaction] = []
        self._handlers: dict[str, Callable[[dict[str, Any]], str]] = {
            "list_files": self._list_files,
            "read_file": self._read_file,
            "search_code": self._search_code,
        }

    @classmethod
    def for_repository(cls, git: GitClient) -> RepoTools:
        root = git.toplevel()
        return cls(GitClient(root), root)

    def run(self, name: str, tool_input: object) -> tuple[str, bool]:
        """Run one tool call. Returns (result text, is_error)."""
        handler = self._handlers.get(name)
        if handler is None:
            return f"Unknown tool {name!r}.", True
        if not isinstance(tool_input, dict):
            return "Tool input must be an object.", True
        logger.info("tool %s %s", name, tool_input)
        try:
            return handler(tool_input), False
        except (ToolError, GitError) as exc:
            return str(exc), True
        except OSError as exc:
            return f"Could not read: {exc.strerror or exc}", True
        except ValidationError as exc:
            return f"Invalid input: {exc.errors()[0]['msg']}", True

    # ------------------------------------------------------------------ tools

    def _list_files(self, raw: dict[str, Any]) -> str:
        args = _ListFiles.model_validate(raw)
        paths = self._git.ls_files(_pathspec(args.path_glob))
        if not paths:
            return "No tracked files match."
        shown = paths[:_MAX_LISTED_FILES]
        more = len(paths) - len(shown)
        return "\n".join(shown) + (f"\n... and {more} more" if more else "")

    def _read_file(self, raw: dict[str, Any]) -> str:
        args = _ReadFile.model_validate(raw)
        path = self._resolve(args.path)
        data = path.read_bytes()
        if b"\0" in data[:8192]:
            raise ToolError(f"{args.path} is a binary file.")
        text, found = redact_file(args.path, data.decode("utf-8", errors="replace"))
        self.redactions.extend(found)
        lines = text.splitlines()
        start = max(args.start_line, 1)
        window = lines[start - 1 : start - 1 + _READ_LINES]
        if not window:
            raise ToolError(f"{args.path} has only {len(lines)} lines.")
        end = start + len(window) - 1
        body = "\n".join(f"{number}: {line}" for number, line in enumerate(window, start))
        header = f"{args.path} (lines {start}-{end} of {len(lines)})"
        return _cap(f"{header}\n{body}")

    def _search_code(self, raw: dict[str, Any]) -> str:
        args = _SearchCode.model_validate(raw)
        if not args.pattern.strip():
            raise ToolError("pattern must not be empty.")
        hits = self._git.grep(args.pattern, _pathspec(args.path_glob))
        if not hits:
            return "No matches."
        lines = []
        for path, number, text in hits[:_MAX_SEARCH_HITS]:
            redacted, found = redact_file(path, text[:_MAX_LINE_CHARS])
            self.redactions.extend(found)
            lines.append(f"{path}:{number}: {redacted}")
        more = len(hits) - _MAX_SEARCH_HITS
        if more > 0:
            lines.append(f"... and {more} more matches; narrow the pattern or path_glob")
        return _cap("\n".join(lines))

    # ---------------------------------------------------------------- helpers

    def _resolve(self, relative: str) -> Path:
        """A file tracked by git inside the repository, or ToolError. Symlinks can't
        escape it, and git-ignored files (``.env``, ``config/master.key``) stay private."""
        candidate = (self._root / relative.lstrip("/")).resolve()
        if not candidate.is_relative_to(self._root):
            raise ToolError(f"{relative} is outside the repository.")
        if ".git" in candidate.relative_to(self._root).parts:
            raise ToolError("The .git folder is off limits.")
        if not candidate.is_file():
            raise ToolError(f"{relative} is not a file in the repository.")
        tracked = candidate.relative_to(self._root).as_posix()
        if tracked not in self._git.ls_files(f":(top,literal){tracked}"):
            raise ToolError(f"{relative} is not tracked by git, so it can't be read.")
        return candidate


def _pathspec(glob: str) -> str:
    """Keep globs literal: no leading '/', no git pathspec magic like ':(exclude)'."""
    glob = glob.strip().lstrip("/")
    return f"./{glob}" if glob.startswith(":") else glob


def _cap(text: str) -> str:
    if len(text) <= _MAX_RESULT_CHARS:
        return text
    return text[:_MAX_RESULT_CHARS] + "\n... (cut off; ask for less)"
