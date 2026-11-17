from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from commit_roaster.config import DEFAULT_EXCLUDE_PATTERNS
from commit_roaster.diff import exclude_files
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


# Real paths from projects in each stack, and whether the default excludes should
# keep them in the review. Hand-written files with "risky" names must stay True.
_STACK_PATHS = {
    # Go
    "go.mod": True,
    "go.sum": False,
    "services/billing/go.sum": False,
    "cmd/server/main.go": True,
    "api/v1/user.pb.go": False,
    "api/v1/user.pb.gw.go": False,
    "pkg/apis/v1/zz_generated.deepcopy.go": False,
    "internal/format_string.go": True,
    "internal/notzz_generated.go": True,
    # Java / Kotlin
    "pom.xml": True,
    "build.gradle.kts": True,
    "gradlew": False,
    "gradlew.bat": False,
    "android/gradlew": False,
    "mvnw": False,
    "mvnw.cmd": False,
    "gradle.lockfile": False,
    "src/main/java/com/acme/UserService.java": True,
    "src/main/kotlin/com/acme/Gradle.kt": True,
    # .NET
    "src/Web/packages.lock.json": False,
    "src/Web/Forms/MainForm.Designer.cs": False,
    "src/Data/Migrations/AppDbContextModelSnapshot.cs": False,
    "src/Data/Migrations/20260101000000_Init.cs": True,
    "src/Data/DataMigrations/UserModelSnapshot.cs": True,
    "Migrations/AppDbContextModelSnapshot.cs": False,
    "src/Services/SnapshotService.cs": True,
    # Swift / iOS
    "Package.resolved": False,
    "App.xcodeproj/project.pbxproj": False,
    "Podfile.lock": False,
    "Sources/App/ContentView.swift": True,
    # Dart / Flutter
    "pubspec.lock": False,
    "lib/models/user.dart": True,
    "lib/models/user.g.dart": False,
    "lib/models/user.freezed.dart": False,
    # Rust
    "Cargo.lock": False,
    "src/main.rs": True,
    # Terraform
    ".terraform.lock.hcl": False,
    "infra/prod/.terraform.lock.hcl": False,
    "infra/prod/main.tf": True,
    # Rails
    "Gemfile.lock": False,
    "db/schema.rb": False,
    "db/structure.sql": False,
    "api/db/schema.rb": False,
    "db/migrate/20260101000000_create_users.rb": True,
    "app/models/user.rb": True,
    "app/services/json_schema.rb": True,
    "app/services/vendor/sync.rb": True,
    "public/assets/application-3f2a.css": False,
    # React / Angular
    "package-lock.json": False,
    "web/package-lock.json": False,
    "yarn.lock": False,
    "pnpm-lock.yaml": False,
    "src/App.tsx": True,
    "src/__snapshots__/App.test.tsx.snap": False,
    "src/app/app.component.ts": True,
    "dist/main.min.js": False,
    # PHP / Laravel
    "composer.lock": False,
    "vendor/autoload.php": False,
    "vendors/report.php": True,
    "app/Http/Controllers/UserController.php": True,
    "public/build/manifest.json": False,
    "_ide_helper.php": False,
    "backend/_ide_helper.php": False,
    "app/vendor/acme/Client.php": True,
    # Python
    "poetry.lock": False,
    "uv.lock": False,
    "src/app/main.py": True,
    "proto/user_pb2.py": False,
    "proto/user_pb2.pyi": False,
    "proto/user_pb2_grpc.py": False,
}


def test_default_excludes_match_git_and_stdin_filter(git_repo: Path) -> None:
    for name in _STACK_PATHS:
        path = git_repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x\n")
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    client = GitClient(git_repo)

    def files_in(diff: str) -> set[str]:
        # Every sample file is new, so each has exactly one "+++ b/<path>" line, which
        # holds the path alone (unlike the "diff --git a/<path> b/<path>" header).
        prefix = "+++ b/"
        return {line.removeprefix(prefix) for line in diff.splitlines() if line.startswith(prefix)}

    expected = {name for name, keep in _STACK_PATHS.items() if keep}
    from_git = files_in(client.diff(staged=True, exclude_patterns=DEFAULT_EXCLUDE_PATTERNS))
    from_stdin = files_in(exclude_files(client.diff(staged=True), DEFAULT_EXCLUDE_PATTERNS))
    assert from_git == expected
    assert from_stdin == expected


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
