from __future__ import annotations

import io
import json

import pytest

from commit_roaster.cli import CommitRoasterCLI
from commit_roaster.config import Settings
from commit_roaster.exceptions import ExitCode
from commit_roaster.models import RoastResult, SpiceLevel
from commit_roaster.roaster import SupportsRoast
from tests.fakes import SAMPLE_DIFF, FakeGit, FakeRoaster


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
    ) -> None:
        self.git = git or FakeGit()
        self.roaster = FakeRoaster(result)
        self.settings: Settings | None = None
        self.stdout = io.StringIO()
        self.stderr = io.StringIO()

        def roaster_factory(settings: Settings) -> SupportsRoast:
            self.settings = settings
            return self.roaster

        self.cli = CommitRoasterCLI(
            stdin=stdin or io.StringIO(),
            stdout=self.stdout,
            stderr=self.stderr,
            git_factory=lambda: self.git,
            roaster_factory=roaster_factory,
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
