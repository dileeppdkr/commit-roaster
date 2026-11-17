"""Thin, testable wrapper around the git command line."""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from commit_roaster.exceptions import GitCommandError, GitError, NotAGitRepositoryError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 30.0
# Set to the staged tree id while `roast --commit` runs `git commit`, so the roast
# pre-commit hook can skip that exact change (and only that one).
IN_COMMIT_ENV = "COMMIT_ROASTER_IN_COMMIT"


class GitClient:
    """Runs git commands in one working directory.

    Commands are always passed as an argument list, never through a shell, so
    user-supplied values such as revision ranges cannot inject shell syntax.
    """

    def __init__(
        self,
        cwd: Path | None = None,
        *,
        executable: str = "git",
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._cwd = cwd
        self._executable = executable
        self._timeout = timeout

    def ensure_repository(self) -> None:
        """Raise :class:`NotAGitRepositoryError` unless ``cwd`` is inside a work tree."""
        try:
            output = self._run("rev-parse", "--is-inside-work-tree")
        except GitCommandError as exc:
            # Only a git that ran and said no means "wrong folder". A missing git or a
            # timeout stays a plain GitError so the user sees the real cause.
            raise NotAGitRepositoryError(
                "Not inside a git repository. Run this from your project folder, "
                "or pipe a diff in with --stdin."
            ) from exc
        if output.strip() != "true":
            raise NotAGitRepositoryError("Not inside a git work tree.")

    def diff(
        self,
        *revisions: str,
        staged: bool = False,
        exclude_patterns: Sequence[str] = (),
    ) -> str:
        """Return a unified diff for the whole repository.

        ``exclude_patterns`` are glob patterns matched from the repository root.
        """
        args = ["diff", "--no-color", "--no-ext-diff"]
        if staged:
            args.append("--cached")
        args.extend(revisions)
        args.append("--")
        args.append(":/")
        args.extend(f":(top,exclude){pattern}" for pattern in exclude_patterns)
        return self._run(*args)

    def staged_tree(self) -> str:
        """The id of the tree the staged changes would commit (``git write-tree``)."""
        return self._run("write-tree").strip()

    def commit(self, message: str) -> str:
        """Commit the staged changes with ``message``; returns git's summary output.

        The repository's hooks run as usual and can be slow (pre-commit may install
        tools first), so there is no time limit: killing git mid-commit can leave
        ``.git/index.lock`` behind. Hooks see ``COMMIT_ROASTER_IN_COMMIT`` set to the
        staged tree id, so a roast hook can tell that this exact change was roasted.
        """
        env = {**os.environ, IN_COMMIT_ENV: self.staged_tree()}
        return self._run("commit", "--file", "-", input_text=message, env=env, time_limit=False)

    def _run(
        self,
        *args: str,
        input_text: str | None = None,
        env: Mapping[str, str] | None = None,
        time_limit: bool = True,
    ) -> str:
        executable = shutil.which(self._executable)
        if executable is None:
            raise GitError(f"Could not find the {self._executable!r} executable on PATH.")
        command = [executable, *args]
        logger.debug("Running %s", " ".join(command))
        try:
            completed = subprocess.run(
                command,
                cwd=self._cwd,
                input=input_text,
                capture_output=True,
                text=True,
                env=env,
                timeout=self._timeout if time_limit else None,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(f"git {args[0]} timed out after {self._timeout:g}s.") from exc
        except OSError as exc:
            raise GitError(f"Could not run git: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip() or "no output"
            raise GitCommandError(f"git {args[0]} failed (exit {completed.returncode}): {detail}")
        return completed.stdout
