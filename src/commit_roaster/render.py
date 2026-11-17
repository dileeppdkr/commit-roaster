"""Output formats. Each renderer turns a result into the text written to stdout."""

from __future__ import annotations

import json
import textwrap
from abc import ABC, abstractmethod

from commit_roaster.diff import Diff
from commit_roaster.models import Issue, RoastResult, Severity, TokenUsage
from commit_roaster.pricing import describe_usage, estimate_cost

_SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


class Renderer(ABC):
    @abstractmethod
    def render(self, result: RoastResult, diff: Diff, usage: TokenUsage | None = None) -> str:
        """Return the full output for one roast."""


class TextRenderer(Renderer):
    """Human-friendly terminal output, with optional ANSI colors."""

    _BOLD = "\033[1m"
    _RESET = "\033[0m"
    _GREEN = "\033[32m"
    _YELLOW = "\033[33m"
    _RED = "\033[31m"
    _DIM = "\033[2m"

    def __init__(self, *, color: bool = False, width: int = 80) -> None:
        self._color = color
        self._width = width

    def render(self, result: RoastResult, diff: Diff, usage: TokenUsage | None = None) -> str:
        score = self._paint(f"{result.cringe_score}/10", self._score_color(result.cringe_score))
        lines = [
            self._heading(f"The roast  (cringe score: {score})"),
            textwrap.fill(result.roast, self._width),
            "",
        ]
        if result.issues:
            lines.append(self._heading("Issues spotted"))
            for issue in sorted(result.issues, key=lambda issue: _SEVERITY_ORDER[issue.severity]):
                where = f"{issue.file}:{issue.line}" if issue.line > 0 else issue.file
                tag = self._paint(f"{issue.severity.upper():<6}", self._severity_color(issue))
                lines.append(f"  {tag} {issue.category}  {where}")
                lines.append(
                    textwrap.fill(
                        issue.problem,
                        self._width,
                        initial_indent="         ",
                        subsequent_indent="         ",
                    )
                )
            lines.append("")
        lines.append(self._heading("Suggested commit message"))
        lines.extend(
            f"  {line}" if line else "" for line in result.commit_message.rstrip().splitlines()
        )
        if diff.truncated:
            lines.append("")
            lines.append(
                self._paint(
                    f"Note: only the first {len(diff.text):,} of "
                    f"{diff.original_chars:,} characters were reviewed.",
                    self._DIM,
                )
            )
        if usage is not None:
            lines.append("")
            lines.append(self._paint(describe_usage(usage), self._DIM))
        return "\n".join(lines)

    def _heading(self, text: str) -> str:
        return self._paint(text, self._BOLD)

    def _score_color(self, score: int) -> str:
        if score <= 3:
            return self._GREEN
        if score <= 6:
            return self._YELLOW
        return self._RED

    def _severity_color(self, issue: Issue) -> str:
        return {Severity.HIGH: self._RED, Severity.MEDIUM: self._YELLOW}.get(
            issue.severity, self._DIM
        )

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{self._RESET}" if self._color else text


class JsonRenderer(Renderer):
    """Machine-readable output for scripts and editor integrations."""

    def render(self, result: RoastResult, diff: Diff, usage: TokenUsage | None = None) -> str:
        payload: dict[str, object] = {
            **result.model_dump(mode="json"),
            "commit_message": result.commit_message,
            "diff": {
                "source": diff.description,
                "truncated": diff.truncated,
                "chars_reviewed": len(diff.text),
                "chars_total": diff.original_chars,
                "omitted_files": list(diff.omitted_files),
                "redactions": [{"file": r.path, "kind": r.kind} for r in diff.redactions],
            },
        }
        if usage is not None:
            payload["usage"] = {
                "model": usage.model,
                "input_tokens": usage.input_tokens,
                "output_tokens": usage.output_tokens,
                "cache_read_tokens": usage.cache_read_tokens,
                "cache_write_tokens": usage.cache_write_tokens,
                "tool_calls": usage.tool_calls,
                "estimated_cost_usd": estimate_cost(usage),
            }
        return json.dumps(payload, indent=2, ensure_ascii=False)
