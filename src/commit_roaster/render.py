"""Output formats. Each renderer turns a result into the text written to stdout."""

from __future__ import annotations

import json
import textwrap
from abc import ABC, abstractmethod

from commit_roaster.diff import Diff
from commit_roaster.models import RoastResult


class Renderer(ABC):
    @abstractmethod
    def render(self, result: RoastResult, diff: Diff) -> str:
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

    def render(self, result: RoastResult, diff: Diff) -> str:
        score = self._paint(f"{result.cringe_score}/10", self._score_color(result.cringe_score))
        lines = [
            self._heading(f"The roast  (cringe score: {score})"),
            textwrap.fill(result.roast, self._width),
            "",
        ]
        if result.issues:
            lines.append(self._heading("Issues spotted"))
            lines.extend(
                textwrap.fill(issue, self._width, initial_indent="  - ", subsequent_indent="    ")
                for issue in result.issues
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
        return "\n".join(lines)

    def _heading(self, text: str) -> str:
        return self._paint(text, self._BOLD)

    def _score_color(self, score: int) -> str:
        if score <= 3:
            return self._GREEN
        if score <= 6:
            return self._YELLOW
        return self._RED

    def _paint(self, text: str, code: str) -> str:
        return f"{code}{text}{self._RESET}" if self._color else text


class JsonRenderer(Renderer):
    """Machine-readable output for scripts and editor integrations."""

    def render(self, result: RoastResult, diff: Diff) -> str:
        payload = {
            **result.model_dump(),
            "commit_message": result.commit_message,
            "diff": {
                "source": diff.description,
                "truncated": diff.truncated,
                "chars_reviewed": len(diff.text),
                "chars_total": diff.original_chars,
            },
        }
        return json.dumps(payload, indent=2, ensure_ascii=False)
