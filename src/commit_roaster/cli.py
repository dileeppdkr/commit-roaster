"""Command-line entry point: ``roast``."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TextIO

from commit_roaster import __version__
from commit_roaster.config import (
    DEFAULT_RULES_FILE,
    EFFORT_LEVELS,
    Settings,
    parse_positive_int,
)
from commit_roaster.diff import (
    Diff,
    DiffSource,
    RevisionRangeDiffSource,
    StagedDiffSource,
    StdinDiffSource,
)
from commit_roaster.exceptions import CommitRoasterError, ConfigError, ExitCode, GitError
from commit_roaster.git import IN_COMMIT_ENV, GitClient
from commit_roaster.models import SUBJECT_MAX_CHARS, ReviewPlan, RoastResult, SpiceLevel
from commit_roaster.patch import split_sections
from commit_roaster.redact import Redaction, redact_file
from commit_roaster.render import JsonRenderer, Renderer, TextRenderer
from commit_roaster.roaster import Roaster, SupportsRoast
from commit_roaster.stack import detect_stacks
from commit_roaster.tools import RepoTools

RoasterFactory = Callable[[Settings, RepoTools | None], SupportsRoast]
GitFactory = Callable[[], GitClient]
ToolsFactory = Callable[[GitClient], RepoTools]

# Team rules longer than this are cut, so a huge file can't blow up every request.
_MAX_RULES_CHARS = 20_000
_MAX_DETECTION_FILE_BYTES = 200_000

_EPILOG = """\
examples:
  roast                         roast staged changes
  roast --spice savage          no mercy
  roast --range HEAD~3..HEAD    roast the last three commits
  git show | roast --stdin      roast any diff you can pipe in
  roast --commit                roast, then commit with the suggested message
  roast --focus security,sql    review only for security and SQL problems
  roast --deep --focus reuse,side-effects
                                let Claude read the codebase to find duplicated
                                helpers and broken callers

focus areas (--focus, comma-separated or repeated; default: all):
  bugs, security, sql, performance (N+1 and friends), side-effects, patterns,
  reuse, best-practices, tests. side-effects, patterns and reuse need --deep.

team rules:
  Put your team's review rules in .roast.md at the repository root and every
  review applies them. Use --rules FILE for another file, --no-rules to skip.
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
        tools_factory: ToolsFactory = RepoTools.for_repository,
        env: dict[str, str] | None = None,
    ) -> None:
        self._stdin = stdin if stdin is not None else sys.stdin
        self._stdout = stdout if stdout is not None else sys.stdout
        self._stderr = stderr if stderr is not None else sys.stderr
        self._git_factory = git_factory
        self._roaster_factory = roaster_factory
        self._tools_factory = tools_factory
        self._env = dict(os.environ) if env is None else env

    # ------------------------------------------------------------------ public

    def run(self, argv: Sequence[str] | None = None) -> int:
        if self._already_roasted():
            # We are the pre-commit hook inside `roast --commit`, which has already
            # roasted this exact change. Don't pay for a second roast.
            self._print_err("roast: skipped, `roast --commit` already roasted this commit.")
            return ExitCode.OK
        try:
            args = self._parse_args(argv)
        except SystemExit as exc:  # argparse exits on --help, --version and bad usage
            return exc.code if isinstance(exc.code, int) else ExitCode.USAGE

        self._configure_logging(args.verbose)
        try:
            code = self._execute(args)
        except CommitRoasterError as exc:
            self._print_err(f"error: {exc}")
            code = exc.exit_code
        except KeyboardInterrupt:
            self._print_err("Interrupted.")
            return ExitCode.INTERRUPTED
        # In a git hook, a failed roast (no key, API down, nothing left to roast after
        # excluding lock files) must not block the commit.
        return ExitCode.OK if args.exit_zero else code

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
            "--max-tokens",
            type=_positive_int,
            metavar="N",
            help="cap on Claude's reply, thinking included, default 16000 (env: ROAST_MAX_TOKENS)",
        )
        parser.add_argument(
            "-x",
            "--exclude",
            action="append",
            default=[],
            metavar="PATTERN",
            help="leave matching files out of the diff, e.g. 'app/assets/builds'; "
            "repeat or comma-separate (env: ROAST_EXCLUDE)",
        )
        parser.add_argument(
            "--no-default-excludes",
            action="store_true",
            help="also send lock files, schema dumps, snapshots and vendored code",
        )
        review = parser.add_argument_group("what to review")
        review.add_argument(
            "--focus",
            action="append",
            default=[],
            metavar="AREAS",
            help="review only these areas, e.g. 'security,sql,performance' "
            "(see the list below; env: ROAST_FOCUS)",
        )
        review.add_argument(
            "--deep",
            action="store_true",
            default=None,
            help="let Claude read and search the codebase with read-only tools; "
            "slower and costs more (env: ROAST_DEEP)",
        )
        review.add_argument(
            "--max-tool-calls",
            type=_positive_int,
            metavar="N",
            help="limit for --deep, default 20 (env: ROAST_MAX_TOOL_CALLS)",
        )
        review.add_argument(
            "--stack",
            metavar="NAMES",
            help="skip detection and use these stacks, e.g. 'rails,react' (env: ROAST_STACK)",
        )
        rules = review.add_mutually_exclusive_group()
        rules.add_argument(
            "--rules",
            metavar="FILE",
            help="team review rules to apply (default: .roast.md if present; env: ROAST_RULES)",
        )
        rules.add_argument(
            "--no-rules", action="store_true", help="don't apply any team rules file"
        )
        parser.add_argument(
            "--exit-zero",
            action="store_true",
            help="exit 0 even when the roast fails, so a git hook never blocks a commit",
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
            model=args.model,
            effort=args.effort,
            max_diff_chars=args.max_diff_chars,
            max_tokens=args.max_tokens,
            exclude=args.exclude,
            default_excludes=False if args.no_default_excludes else None,
            focus=args.focus,
            deep=args.deep,
            max_tool_calls=args.max_tool_calls,
            stacks=args.stack,
            rules_file="" if args.no_rules else args.rules,
        )
        git = self._git_factory()
        diff = self._build_source(args, git, settings).load(settings.max_diff_chars)
        if diff.truncated:
            self._print_err(
                f"warning: diff is {diff.original_chars:,} characters; only the first "
                f"{len(diff.text):,} will be reviewed (raise with --max-diff-chars)."
            )
        if diff.redactions:
            self._warn_about_secrets(diff.redactions, "they were replaced before sending")

        root = self._repo_root(git)
        plan = self._plan(settings, git, root, diff)
        tools = None
        if plan.deep:
            if root is None:
                raise ConfigError("--deep needs a git repository: it reads files from it.")
            tools = self._tools_factory(git)
        elif any(area.needs_codebase for area in plan.focus):
            self._print_err(
                "note: side-effects, patterns and reuse need the rest of the codebase; "
                "add --deep to let Claude look at it."
            )
        if not args.json and self._is_tty(self._stderr):
            self._print_err(self._progress_line(diff, settings, plan))

        outcome = self._roaster_factory(settings, tools).roast(diff, SpiceLevel(args.spice), plan)
        if tools is not None and tools.redactions:
            self._warn_about_secrets(tools.redactions, "Claude saw them redacted")
        self._print_out(self._renderer(args).render(outcome.result, diff, outcome.usage))

        if args.commit:
            self._commit(git, outcome.result, diff, assume_yes=args.yes)
        return ExitCode.OK

    def _plan(
        self, settings: Settings, git: GitClient, root: Path | None, diff: Diff
    ) -> ReviewPlan:
        stacks = settings.stacks
        if stacks is None:
            stacks = self._detect_stacks(git, root, diff)
        rules_file = settings.rules_file
        if rules_file is None and DEFAULT_RULES_FILE in self._changed_files(diff):
            # The change under review could rewrite its own rules ("never report
            # security issues"), so a changed .roast.md is reviewed, not obeyed.
            self._print_err(
                f"note: {DEFAULT_RULES_FILE} is changed in this diff, so its rules were not "
                f"applied. Pass --rules {DEFAULT_RULES_FILE} to apply them anyway."
            )
            rules_file = ""
        return ReviewPlan(
            focus=settings.focus,
            stacks=stacks,
            rules=self._load_rules(rules_file, root),
            deep=settings.deep,
            max_tool_calls=settings.max_tool_calls,
        )

    @staticmethod
    def _changed_files(diff: Diff) -> set[str]:
        """Every file the diff touches, including those cut off by the size limit."""
        changed = set(diff.omitted_files)
        for section in split_sections(diff.text):
            changed |= section.paths
        return changed

    @staticmethod
    def _repo_root(git: GitClient) -> Path | None:
        try:
            return git.toplevel()
        except GitError:
            return None  # e.g. `roast --stdin` outside any repository

    @staticmethod
    def _detect_stacks(git: GitClient, root: Path | None, diff: Diff) -> tuple[str, ...]:
        if root is None:
            # No repository: the file names in the diff are all we have.
            names = [s.name for s in split_sections(diff.text) if s.name]
            return detect_stacks(names, lambda _path: None)

        def read(path: str) -> str | None:
            try:
                with (root / path).open("rb") as handle:
                    return handle.read(_MAX_DETECTION_FILE_BYTES).decode("utf-8", "replace")
            except OSError:
                return None

        return detect_stacks(git.ls_files(), read)

    @staticmethod
    def _load_rules(rules_file: str | None, root: Path | None) -> str | None:
        """The team's review rules: an explicit file must exist, the default is optional."""
        if rules_file == "":
            return None
        if rules_file is None:
            if root is None or not (root / DEFAULT_RULES_FILE).is_file():
                return None
            path = root / DEFAULT_RULES_FILE
        else:
            candidates = [Path(rules_file)] + ([root / rules_file] if root else [])
            found = next((c for c in candidates if c.is_file()), None)
            if found is None:
                raise ConfigError(f"Rules file {rules_file!r} not found.")
            path = found
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            raise ConfigError(f"Could not read rules file {path}: {exc.strerror}") from exc
        text, _ = redact_file(path.name, text[:_MAX_RULES_CHARS])
        return text.strip() or None

    @staticmethod
    def _progress_line(diff: Diff, settings: Settings, plan: ReviewPlan) -> str:
        details = []
        if plan.focus:
            details.append("focus: " + ", ".join(plan.focus))
        if plan.stacks:
            details.append("stack: " + ", ".join(plan.stacks))
        if plan.rules:
            details.append("team rules")
        if plan.deep:
            details.append(f"deep, up to {plan.max_tool_calls} tool calls")
        suffix = f" ({'; '.join(details)})" if details else ""
        return f"Roasting {diff.description} with {settings.model}{suffix}..."

    def _already_roasted(self) -> bool:
        """True only inside `roast --commit`, for the very change it roasted. Comparing
        tree ids means a leftover `export COMMIT_ROASTER_IN_COMMIT=...` never turns
        roasting off."""
        marker = self._env.get(IN_COMMIT_ENV)
        if not marker:
            return False
        try:
            return self._git_factory().staged_tree() == marker
        except GitError:
            return False

    def _warn_about_secrets(self, redactions: Sequence[Redaction], what_happened: str) -> None:
        lines = [f"warning: possible secrets found; {what_happened}:"]
        lines.extend(
            f"  {path or '(outside any file)'}: {kind}"
            for path, kind in dict.fromkeys((r.path, r.kind) for r in redactions)
        )
        lines.append("Check that no real credential is committed.")
        self._print_err("\n".join(lines))

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
