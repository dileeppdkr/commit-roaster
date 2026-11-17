from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from commit_roaster.git import GitClient
from commit_roaster.tools import TOOL_DEFINITIONS, RepoTools


@pytest.fixture
def repo(git_repo: Path) -> Path:
    files = {
        "app/models/user.rb": "class User\n  has_many :posts\nend\n",
        "app/services/formatter.rb": "def format_name(user)\n  user.name.strip\nend\n",
        "config/.env.production": "SECRET_TOKEN=supersecret\n",
        "logo.png": "\x00PNG",
    }
    for name, content in files.items():
        path = git_repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=git_repo, check=True)
    return git_repo


def _tools(repo: Path) -> RepoTools:
    return RepoTools(GitClient(repo), repo)


def test_definitions_are_strict_and_sorted() -> None:
    names = [tool["name"] for tool in TOOL_DEFINITIONS]
    assert names == sorted(names)
    for tool in TOOL_DEFINITIONS:
        schema = tool["input_schema"]
        assert tool["strict"] is True
        assert schema["additionalProperties"] is False
        assert schema["required"] == list(schema["properties"])


def test_read_file_numbers_lines(repo: Path) -> None:
    text, is_error = _tools(repo).run("read_file", {"path": "app/models/user.rb", "start_line": 2})
    assert not is_error
    assert text == "app/models/user.rb (lines 2-3 of 3)\n2:   has_many :posts\n3: end"


@pytest.mark.parametrize(
    ("path", "message"),
    [
        ("../outside.txt", "outside the repository"),
        (".git/config", "off limits"),
        ("app/missing.rb", "not a file"),
        ("logo.png", "binary"),
    ],
)
def test_read_file_refuses(repo: Path, path: str, message: str) -> None:
    (repo.parent / "outside.txt").write_text("secret")
    text, is_error = _tools(repo).run("read_file", {"path": path, "start_line": 1})
    assert is_error
    assert message in text


def test_symlink_cannot_escape(repo: Path) -> None:
    (repo.parent / "outside.txt").write_text("secret")
    (repo / "link.txt").symlink_to(repo.parent / "outside.txt")
    text, is_error = _tools(repo).run("read_file", {"path": "link.txt", "start_line": 1})
    assert is_error
    assert "outside the repository" in text


def test_read_file_redacts_secrets(repo: Path) -> None:
    tools = _tools(repo)
    text, _ = tools.run("read_file", {"path": "config/.env.production", "start_line": 1})
    assert "supersecret" not in text
    assert "SECRET_TOKEN=[REDACTED]" in text
    assert [(r.path, r.kind) for r in tools.redactions] == [
        ("config/.env.production", "environment file values")
    ]


def test_search_code_finds_lines(repo: Path) -> None:
    text, is_error = _tools(repo).run("search_code", {"pattern": r"def \w+_name", "path_glob": ""})
    assert not is_error
    assert text == "app/services/formatter.rb:1: def format_name(user)"


def test_search_code_redacts_and_handles_no_matches(repo: Path) -> None:
    tools = _tools(repo)
    found, _ = tools.run("search_code", {"pattern": "SECRET", "path_glob": "config/*"})
    assert "supersecret" not in found
    none, is_error = tools.run("search_code", {"pattern": "nothing_like_this", "path_glob": ""})
    assert (none, is_error) == ("No matches.", False)


def test_search_code_reports_a_bad_regex(repo: Path) -> None:
    _text, is_error = _tools(repo).run("search_code", {"pattern": "(", "path_glob": ""})
    assert is_error


def test_list_files_with_glob(repo: Path) -> None:
    text, _ = _tools(repo).run("list_files", {"path_glob": "app/*"})
    assert text.splitlines() == ["app/models/user.rb", "app/services/formatter.rb"]


def test_pathspec_magic_is_neutralised(repo: Path) -> None:
    text, _ = _tools(repo).run("list_files", {"path_glob": ":(exclude)app"})
    assert text == "No tracked files match."


@pytest.mark.parametrize(
    ("name", "tool_input", "message"),
    [
        ("delete_file", {}, "Unknown tool"),
        ("read_file", {"path": "app/models/user.rb"}, "Invalid input"),
        ("read_file", ["not", "an", "object"], "must be an object"),
    ],
)
def test_bad_calls_are_errors(repo: Path, name: str, tool_input: object, message: str) -> None:
    text, is_error = _tools(repo).run(name, tool_input)
    assert is_error
    assert message in text
