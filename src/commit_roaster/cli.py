"""Command-line entry point: ``roast``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Sequence
from typing import TextIO

from commit_roaster import __version__
from commit_roaster.config import EFFORT_LEVELS, Settings, parse_positive_int
from commit_roaster.diff import (
    Diff,
    DiffSource,
    RevisionRangeDiffSource,
    StagedDiffSource,
    StdinDiffSource,
)
from commit_roaster.exceptions import CommitRoasterError, ConfigError, ExitCode
from commit_roaster.git import GitClient
from commit_roaster.models import SUBJECT_MAX_CHARS, RoastResult, SpiceLevel
from commit_roaster.render import JsonRenderer, Renderer, TextRenderer
from commit_roaster.roaster import Roaster, SupportsRoast

RoasterFactory = Callable[[Settings], SupportsRoast]
GitFactory = Callable[[], GitClient]

_EPILOG = """\
examples:
  roast                         roast staged changes
  roast --spice savage          no mercy
  roast --range HEAD~3..HEAD    roast the last three commits
  git show | roast --stdin      roast any diff you can pipe in
  roast --commit                roast, then commit with the suggested message
"""


def _positive_int(value: str) -> int:
    """argparse adapter for :func:`parse_positive_int`, so the rule lives in one place."""
    try:
        return parse_positive_int(value)
    except ConfigError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


class CommitRoasterCLI:
    """Parses arguments, wires the pieces together and maps errors to exit codes.

    Collaborators are injected so tests can run the full flow without git or the API.
    """

    def __init__(
        self,
        *,
        stdin: TextIO | None = None,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
        git_factory: GitFactory = GitClient,
        roaster_factory: RoasterFactory = Roaster.from_settings,
        env: dict[str, str] | None = None,
    ) -> None:
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr
        self._git_factory = git_factory
        self._roaster_factory = roaster_factory
        self._env = dict(os.environ) if env is None else env

    # ------------------------------------------------------------------ public

    def run(self, argv: Sequence[str] | None = None) -> int:
        try:
            args = self._parse_args(argv)
        except SystemExit as exc:  # argparse exits on --help, --version and bad usage
            return exc.code if isinstance(exc.code, int) else ExitCode.USAGE

        self._configure_logging(args.verbose)
        try:
            return self._execute(args)
        except CommitRoasterError as exc:
            self._print_err(f"error: {exc}")
            return exc.exit_code
        except KeyboardInterrupt:
            self._print_err("Interrupted.")
            return ExitCode.INTERRUPTED

    def build_parser(self) -> argparse.ArgumentParser:
        parser = argparse.ArgumentParser(
            prog="roast",
            description="A grumpy senior dev reviews your diff, then writes the commit message.",
            epilog=_EPILOG,
            formatter_class=argparse.RawDescriptionHelpFormatter,
        )
        source = parser.add_mutually_exclusive_group()
        source.add_argument(
            "-r",
            "--range",
            metavar="REVS",
            help="roast a revision or range (e.g. HEAD~1, main..feature) instead of staged changes",
        )
        source.add_argument(
            "--stdin", action="store_true", help="read the diff from standard input"
        )
        parser.add_argument(
            "-s",
            "--spice",
            choices=[level.value for level in SpiceLevel],
            default=SpiceLevel.MEDIUM.value,
            help="how hard to roast (default: %(default)s)",
        )
        parser.add_argument("--json", action="store_true", help="print the result as JSON")
        parser.add_argument(
            "-c",
            "--commit",
            action="store_true",
            help="commit the staged changes with the suggested message",
        )
        parser.add_argument(
            "-y", "--yes", action="store_true", help="with --commit, skip the confirmation"
        )
        parser.add_argument("--model", help="Claude model ID (env: ROAST_MODEL)")
        parser.add_argument(
            "--effort", choices=EFFORT_LEVELS, help="thinking effort (env: ROAST_EFFORT)"
        )
        parser.add_argument(
            "--max-diff-chars",
            type=_positive_int,
            metavar="N",
            help="truncate larger diffs, with a warning (env: ROAST_MAX_DIFF_CHARS)",
        )
        parser.add_argument(
            "-v", "--verbose", action="count", default=0, help="-v for info, -vv for debug logs"
        )
        parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
        return parser

    # ----------------------------------------------------------------- private

    def _parse_args(self, argv: Sequence[str] | None) -> argparse.Namespace:
        parser = self.build_parser()
        args = parser.parse_args(argv)
        if args.commit and (args.stdin or args.range):
            parser.error("--commit only works with staged changes (drop --range/--stdin)")
        if args.yes and not args.commit:
            parser.error("--yes only makes sense with --commit")
        if args.commit and not args.yes and not self._is_tty(self._stdin):
            # Checked before the paid API call, not after it.
            parser.error("--commit asks for confirmation, which needs a terminal; add --yes")
        return args

    def _execute(self, args: argparse.Namespace) -> int:
        settings = Settings.from_env(self._env).with_overrides(
            model=args.model, effort=args.effort, max_diff_chars=args.max_diff_chars
        )
        git = self._git_factory()
        diff = self._build_source(args, git, settings).load(settings.max_diff_chars)
        if diff.truncated:
            self._print_err(
                f"warning: diff is {diff.original_chars:,} characters; only the first "
                f"{len(diff.text):,} will be reviewed (raise with --max-diff-chars)."
            )
        if not args.json and self._is_tty(self._stderr):
            self._print_err(f"Roasting {diff.description} with {settings.model}...")

        result = self._roaster_factory(settings).roast(diff, SpiceLevel(args.spice))
        self._print_out(self._renderer(args).render(result, diff))

        if args.commit:
            self._commit(git, result, diff, assume_yes=args.yes)
        return ExitCode.OK

    def _build_source(
        self, args: argparse.Namespace, git: GitClient, settings: Settings
    ) -> DiffSource:
        if args.stdin:
            return StdinDiffSource(self._stdin, settings.exclude_patterns)
        if args.range:
            return RevisionRangeDiffSource(git, args.range, settings.exclude_patterns)
        return StagedDiffSource(git, settings.exclude_patterns)

    def _renderer(self, args: argparse.Namespace) -> Renderer:
        if args.json:
            return JsonRenderer()
        color = self._is_tty(self._stdout) and "NO_COLOR" not in self._env
        return TextRenderer(color=color)

    def _commit(self, git: GitClient, result: RoastResult, diff: Diff, *, assume_yes: bool) -> None:
        if result.subject_too_long:
            self._print_err(
                f"warning: the subject is {len(result.commit_subject)} characters; "
                f"git convention is {SUBJECT_MAX_CHARS} or fewer."
            )
        if not assume_yes:
            question = "Commit staged changes with this message?"
            if diff.truncated:
                question = (
                    f"Claude reviewed only {len(diff.text):,} of {diff.original_chars:,} "
                    f"characters. Commit all staged changes with this message?"
                )
            self._stderr.write(f"\n{question} [y/N] ")
            self._stderr.flush()
            if self._stdin.readline().strip().lower() not in {"y", "yes"}:
                self._print_err("Commit skipped.")
                return
        summary = git.commit(result.commit_message)
        self._print_err(summary.strip())

    def _configure_logging(self, verbosity: int) -> None:
        level = {0: logging.WARNING, 1: logging.INFO}.get(verbosity, logging.DEBUG)
        logging.basicConfig(
            level=level,
            format="%(levelname)s %(name)s: %(message)s",
            stream=self._stderr,
            force=True,
        )

    @staticmethod
    def _is_tty(stream: TextIO) -> bool:
        isatty = getattr(stream, "isatty", None)
        return bool(isatty and isatty())

    def _print_out(self, text: str) -> None:
        print(text, file=self._stdout)

    def _print_err(self, text: str) -> None:
        print(text, file=self._stderr)


def main(argv: Sequence[str] | None = None) -> int:
    return CommitRoasterCLI().run(argv)
