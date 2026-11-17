from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from commit_roaster.exceptions import GitCommandError, GitError, NotAGitRepositoryError
from commit_roaster.git import GitClient


def _stage(repo: Path, name: str, content: str) -> None:
    (repo / name).write_text(content)
    subprocess.run(["git", "add", name], cwd=repo, check=True)


def test_staged_diff_from_real_repo(git_repo: Path) -> None:
    _stage(git_repo, "hello.py", "print('hi')\n")
    diff = GitClient(git_repo).diff(staged=True)
    assert "hello.py" in diff
    assert "+print('hi')" in diff


def test_exclude_patterns_hide_matching_files(git_repo: Path) -> None:
    _stage(git_repo, "hello.py", "x = 1\n")
    (git_repo / "deps").mkdir()
    _stage(git_repo, "deps/poetry.lock", "lots of noise\n")
    diff = GitClient(git_repo).diff(staged=True, exclude_patterns=["*.lock"])
    assert "hello.py" in diff
    assert "poetry.lock" not in diff


def test_diff_covers_whole_repo_from_subdirectory(git_repo: Path) -> None:
    _stage(git_repo, "top.py", "x = 1\n")
    sub = git_repo / "sub"
    sub.mkdir()
    assert "top.py" in GitClient(sub).diff(staged=True)


def test_commit_uses_message(git_repo: Path) -> None:
    _stage(git_repo, "a.txt", "a\n")
    GitClient(git_repo).commit("Add a.txt\n\nBecause reasons.\n")
    log = subprocess.run(
        ["git", "log", "-1", "--format=%B"],
        cwd=git_repo,
        capture_output=True,
        text=True,
        check=True,
    )
    assert log.stdout.strip() == "Add a.txt\n\nBecause reasons."


def test_ensure_repository_passes_inside_repo(git_repo: Path) -> None:
    GitClient(git_repo).ensure_repository()


def test_ensure_repository_fails_outside_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))
    outside = tmp_path / "plain"
    outside.mkdir()
    with pytest.raises(NotAGitRepositoryError):
        GitClient(outside).ensure_repository()


def test_missing_executable_raises() -> None:
    with pytest.raises(GitError, match="Could not find"):
        GitClient(executable="definitely-not-git-xyz").diff()


def test_ensure_repository_reports_missing_git_as_such() -> None:
    with pytest.raises(GitError, match="Could not find") as caught:
        GitClient(executable="definitely-not-git-xyz").ensure_repository()
    assert not isinstance(caught.value, NotAGitRepositoryError)


def test_failed_command_includes_stderr(git_repo: Path) -> None:
    with pytest.raises(GitCommandError, match="exit"):
        GitClient(git_repo).diff("no-such-revision")


def test_timeout_raises_git_error(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    def fake_run(*args: object, **kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd="git", timeout=1)

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GitError, match="timed out"):
        GitClient(git_repo, timeout=1).diff()


def test_os_error_raises_git_error(monkeypatch: pytest.MonkeyPatch, git_repo: Path) -> None:
    def fake_run(*args: object, **kwargs: object) -> None:
        raise PermissionError("denied")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(GitError, match="Could not run git"):
        GitClient(git_repo).diff()
