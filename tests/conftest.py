from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from commit_roaster.diff import Diff
from commit_roaster.models import RoastResult
from tests.fakes import SAMPLE_DIFF


@pytest.fixture
def sample_diff() -> Diff:
    return Diff(text=SAMPLE_DIFF, description="staged changes", original_chars=len(SAMPLE_DIFF))


@pytest.fixture
def sample_result() -> RoastResult:
    return RoastResult(
        roast="A print statement named DEBUG. Bold of you to ship your diary.",
        cringe_score=6,
        issues=["Leftover debug print in add()."],
        commit_subject="Add debug logging to add()",
        commit_body="Prints both operands so the sum can be traced.",
    )


@pytest.fixture
def git_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A real, empty git repository isolated from the user's global git config."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-global-config"))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for prefix in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{prefix}_NAME", "Test User")
        monkeypatch.setenv(f"{prefix}_EMAIL", "test@example.com")
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "--quiet", "--initial-branch=main"], cwd=repo, check=True)
    return repo
