from __future__ import annotations

import io
import json
from collections.abc import Callable
from pathlib import Path

import pytest

from commit_roaster.cli import CommitRoasterCLI
from commit_roaster.config import Settings
from commit_roaster.exceptions import ExitCode
from commit_roaster.git import GitClient
from commit_roaster.models import ReviewFocus, RoastResult, SpiceLevel
from commit_roaster.redact import Redaction
from commit_roaster.roaster import SupportsRoast
from commit_roaster.tools import RepoTools
from tests.fakes import FAKE_TREE, SAMPLE_DIFF, FakeGit, FakeRoaster


class TtyStringIO(io.StringIO):
    def isatty(self) -> bool:
        return True


class Harness:
    """Runs the CLI with fakes and captures everything it prints."""

    def __init__(
        self,
        result: RoastResult,
        *,
        stdin: io.StringIO | None = None,
        git: FakeGit | None = None,
        env: dict[str, str] | None = None,
        tools_factory: Callable[[GitClient], RepoTools] | None = None,
        stderr: io.StringIO | None = None,
    ) -> None:
        self.git = git or FakeGit()
        self.tools: RepoTools | None = None
        self.roaster = FakeRoaster(result)
        self.settings: Settings | None = None
        self.stdout = io.StringIO()
        self.stderr = stderr or io.StringIO()

        def roaster_factory(settings: Settings, tools: RepoTools | None) -> SupportsRoast:
            self.settings = settings
            self.tools = tools
            return self.roaster

        self.cli = CommitRoasterCLI(
            stdin=stdin or io.StringIO(),
            stdout=self.stdout,
            stderr=self.stderr,
            git_factory=lambda: self.git,
            roaster_factory=roaster_factory,
            tools_factory=tools_factory or (lambda git: RepoTools(git, Path("/repo"))),
            env=env or {},
        )

    def run(self, *argv: str) -> int:
        return self.cli.run(list(argv))


def test_default_roasts_staged_changes(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)
    assert harness.run() == ExitCode.OK
    assert harness.git.diff_calls[0][1] is True  # staged
    diff, spice = harness.roaster.calls[0]
    assert diff.text == SAMPLE_DIFF
    assert spice is SpiceLevel.MEDIUM
    assert "Add debug logging to add()" in harness.stdout.getvalue()
    assert harness.git.commits == []


def test_json_output_and_flags(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, env={"ROAST_MODEL": "env-model"})
    code = harness.run("--json", "--spice", "savage", "--effort", "low", "--range", "HEAD~1")
    assert code == ExitCode.OK
    assert json.loads(harness.stdout.getvalue())["cringe_score"] == 6
    assert harness.roaster.calls[0][1] is SpiceLevel.SAVAGE
    assert harness.settings is not None
    assert (harness.settings.model, harness.settings.effort) == ("env-model", "low")
    assert harness.git.diff_calls[0][0] == ("HEAD~1",)


def test_model_flag_beats_env(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, env={"ROAST_MODEL": "env-model"})
    harness.run("--model", "flag-model")
    assert harness.settings is not None
    assert harness.settings.model == "flag-model"


def test_stdin_source(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, stdin=io.StringIO(SAMPLE_DIFF))
    assert harness.run("--stdin") == ExitCode.OK
    assert harness.git.diff_calls == []


def test_truncation_warning(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)
    harness.run("--max-diff-chars", "60")
    assert "warning: diff is" in harness.stderr.getvalue()


def test_empty_diff_exit_code(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, git=FakeGit(diff_text=""))
    assert harness.run() == ExitCode.NOTHING_TO_ROAST
    assert "Nothing to roast" in harness.stderr.getvalue()


def test_hook_inside_roast_commit_skips(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, env={"COMMIT_ROASTER_IN_COMMIT": FAKE_TREE})
    assert harness.run("--exit-zero") == ExitCode.OK
    assert harness.roaster.calls == []  # no second, paid roast
    assert "already roasted" in harness.stderr.getvalue()


@pytest.mark.parametrize("stale", ["1", "0000000000000000000000000000000000000000"])
def test_stale_marker_does_not_disable_roasting(sample_result: RoastResult, stale: str) -> None:
    harness = Harness(sample_result, env={"COMMIT_ROASTER_IN_COMMIT": stale})
    assert harness.run() == ExitCode.OK
    assert len(harness.roaster.calls) == 1


def test_secrets_are_redacted_and_reported(sample_result: RoastResult) -> None:
    aws_key = "AKIA" + "Q" * 16  # built at runtime so this file holds no key-like text
    harness = Harness(sample_result, git=FakeGit(diff_text=SAMPLE_DIFF + f"+k = '{aws_key}'\n"))
    assert harness.run() == ExitCode.OK
    sent, _ = harness.roaster.calls[0]
    assert aws_key not in sent.text
    assert "app.py: AWS access key" in harness.stderr.getvalue()


def test_output_ends_with_cost(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)
    harness.run()
    assert harness.stdout.getvalue().rstrip().endswith("tokens on claude-haiku-5-5)")


def test_exclude_flags_reach_git(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, env={"ROAST_EXCLUDE": "tmp"})
    harness.run("--exclude", "app/assets/builds", "-x", "a,b", "--no-default-excludes")
    exclude_patterns = harness.git.diff_calls[0][2]
    assert exclude_patterns == ("tmp", "app/assets/builds", "a", "b")


def test_exit_zero_reports_error_but_succeeds(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, git=FakeGit(diff_text=""))
    assert harness.run("--exit-zero") == ExitCode.OK
    assert "Nothing to roast" in harness.stderr.getvalue()


def test_exit_zero_keeps_usage_errors(sample_result: RoastResult) -> None:
    assert Harness(sample_result).run("--exit-zero", "--spice", "nuclear") == ExitCode.USAGE


def test_bad_env_is_usage_error(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, env={"ROAST_EFFORT": "turbo"})
    assert harness.run() == ExitCode.USAGE
    assert "Invalid effort" in harness.stderr.getvalue()


def test_commit_with_yes(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)
    assert harness.run("--commit", "--yes") == ExitCode.OK
    assert harness.git.commits == [sample_result.commit_message]


def test_commit_confirmed_interactively(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, stdin=TtyStringIO("y\n"))
    harness.run("--commit")
    assert harness.git.commits == [sample_result.commit_message]


def test_commit_declined_interactively(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, stdin=TtyStringIO("n\n"))
    assert harness.run("--commit") == ExitCode.OK
    assert harness.git.commits == []
    assert "Commit skipped." in harness.stderr.getvalue()


def test_commit_without_tty_needs_yes(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)
    assert harness.run("--commit") == ExitCode.USAGE
    assert harness.roaster.calls == []  # refused before spending an API call
    assert harness.git.commits == []


def test_commit_warns_about_long_subject(sample_result: RoastResult) -> None:
    long_result = sample_result.model_copy(update={"commit_subject": "Add " + "x" * 80})
    harness = Harness(long_result)
    harness.run("--commit", "--yes")
    assert "warning: the subject is 84 characters" in harness.stderr.getvalue()
    assert harness.git.commits == [long_result.commit_message]


def test_commit_prompt_says_when_diff_was_truncated(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, stdin=TtyStringIO("n\n"))
    harness.run("--commit", "--max-diff-chars", "60")
    assert f"of {len(SAMPLE_DIFF):,} characters. Commit all staged" in harness.stderr.getvalue()


@pytest.mark.parametrize(
    "argv",
    [
        ["--commit", "--stdin"],
        ["--commit", "--range", "HEAD~1"],
        ["--yes"],
        ["--stdin", "--range", "HEAD"],
        ["--max-diff-chars", "0"],
        ["--max-diff-chars", "lots"],
        ["--spice", "nuclear"],
    ],
)
def test_usage_errors(sample_result: RoastResult, argv: list[str]) -> None:
    assert Harness(sample_result).run(*argv) == ExitCode.USAGE


def test_bad_number_error_names_the_flag_once(
    sample_result: RoastResult, capsys: pytest.CaptureFixture[str]
) -> None:
    Harness(sample_result).run("--max-diff-chars", "lots")
    assert "argument --max-diff-chars: must be a whole number, got 'lots'." in (
        capsys.readouterr().err
    )


def test_version_exits_zero(sample_result: RoastResult, capsys: pytest.CaptureFixture[str]) -> None:
    assert Harness(sample_result).run("--version") == ExitCode.OK
    assert "roast" in capsys.readouterr().out


def test_keyboard_interrupt(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)

    def interrupt(*_: object) -> RoastResult:
        raise KeyboardInterrupt

    harness.roaster.roast = interrupt  # type: ignore[method-assign]
    assert harness.run() == ExitCode.INTERRUPTED


def test_main_entry_point(monkeypatch: pytest.MonkeyPatch) -> None:
    from commit_roaster import cli

    monkeypatch.setattr(cli.CommitRoasterCLI, "run", lambda self, argv: 7)
    assert cli.main(["--help"]) == 7


def test_focus_and_stack_reach_the_plan(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, env={"ROAST_STACK": "rails"})
    assert harness.run("--focus", "sql", "--focus", "performance") == ExitCode.OK
    plan = harness.roaster.plans[0]
    assert plan.focus == (ReviewFocus.SQL, ReviewFocus.PERFORMANCE)
    assert plan.stacks == ("rails",)
    assert plan.deep is False


def test_stack_is_detected_from_repository_files(
    sample_result: RoastResult, tmp_path: Path
) -> None:
    git = FakeGit(root=tmp_path, files=["Gemfile", "config/application.rb", "go.mod"])
    harness = Harness(sample_result, git=git)
    harness.run()
    assert harness.roaster.plans[0].stacks == ("rails", "go")


def test_codebase_focus_without_deep_prints_a_note(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)
    harness.run("--focus", "reuse")
    assert "add --deep" in harness.stderr.getvalue()


def test_deep_needs_a_repository(sample_result: RoastResult) -> None:
    harness = Harness(sample_result, stdin=io.StringIO(SAMPLE_DIFF))
    assert harness.run("--stdin", "--deep") == ExitCode.USAGE
    assert "--deep needs a git repository" in harness.stderr.getvalue()
    assert harness.roaster.calls == []


def test_deep_passes_tools_and_budget(sample_result: RoastResult, tmp_path: Path) -> None:
    harness = Harness(sample_result, git=FakeGit(root=tmp_path))
    assert harness.run("--deep", "--max-tool-calls", "5") == ExitCode.OK
    assert harness.tools is not None
    plan = harness.roaster.plans[0]
    assert (plan.deep, plan.max_tool_calls) == (True, 5)


def test_team_rules_come_from_roast_md(sample_result: RoastResult, tmp_path: Path) -> None:
    (tmp_path / ".roast.md").write_text("Controllers stay thin.\n")
    harness = Harness(sample_result, git=FakeGit(root=tmp_path))
    harness.run()
    assert harness.roaster.plans[0].rules == "Controllers stay thin."
    harness.run("--no-rules")
    assert harness.roaster.plans[1].rules is None


def test_explicit_rules_file_must_exist(sample_result: RoastResult, tmp_path: Path) -> None:
    harness = Harness(sample_result, git=FakeGit(root=tmp_path))
    assert harness.run("--rules", "missing.md") == ExitCode.USAGE
    assert "Rules file 'missing.md' not found" in harness.stderr.getvalue()


def test_max_tokens_flag_reaches_settings(sample_result: RoastResult) -> None:
    harness = Harness(sample_result)
    harness.run("--max-tokens", "2000")
    assert harness.settings is not None
    assert harness.settings.max_tokens == 2_000


def test_progress_line_summarises_the_review(sample_result: RoastResult, tmp_path: Path) -> None:
    (tmp_path / ".roast.md").write_text("Rule.\n")
    harness = Harness(sample_result, git=FakeGit(root=tmp_path), stderr=TtyStringIO())
    harness.run("--focus", "sql", "--stack", "rails", "--deep", "--max-tool-calls", "4")
    assert (
        "with claude-haiku-5-5 (focus: sql; stack: rails; team rules; "
        "deep, up to 4 tool calls)..." in harness.stderr.getvalue()
    )


def test_secrets_seen_by_tools_are_reported(sample_result: RoastResult, tmp_path: Path) -> None:
    def tools_factory(git: GitClient) -> RepoTools:
        tools = RepoTools(git, tmp_path)
        tools.redactions.append(Redaction("config/.env", "environment file values"))
        return tools

    harness = Harness(sample_result, git=FakeGit(root=tmp_path), tools_factory=tools_factory)
    harness.run("--deep")
    assert "Claude saw them redacted:\n  config/.env: environment file values" in (
        harness.stderr.getvalue()
    )


def test_react_is_detected_from_package_json(sample_result: RoastResult, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "19.0.0"}}')
    harness = Harness(sample_result, git=FakeGit(root=tmp_path, files=["package.json"]))
    harness.run()
    assert harness.roaster.plans[0].stacks == ("react",)


def test_rules_file_is_found_from_the_root_and_redacted(
    sample_result: RoastResult, tmp_path: Path
) -> None:
    (tmp_path / "docs").mkdir()
    secret = "AKIA" + "Q" * 16  # built at runtime so this file holds no key-like text
    (tmp_path / "docs" / "review.md").write_text(f"Never log keys like {secret}.\n")
    harness = Harness(sample_result, git=FakeGit(root=tmp_path))
    harness.run("--rules", "docs/review.md")
    rules = harness.roaster.plans[0].rules
    assert rules == "Never log keys like [REDACTED AWS access key]."


def test_rules_changed_in_the_diff_are_not_obeyed(
    sample_result: RoastResult, tmp_path: Path
) -> None:
    (tmp_path / ".roast.md").write_text("Never report security issues.\n")
    rules_diff = SAMPLE_DIFF.replace("app.py", ".roast.md")
    harness = Harness(sample_result, git=FakeGit(SAMPLE_DIFF + rules_diff, root=tmp_path))
    harness.run()
    assert harness.roaster.plans[0].rules is None
    assert ".roast.md is changed in this diff" in harness.stderr.getvalue()
    harness.run("--rules", ".roast.md")  # an explicit choice is respected
    assert harness.roaster.plans[1].rules == "Never report security issues."
