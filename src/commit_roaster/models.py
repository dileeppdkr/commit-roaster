"""Domain types: roast intensity, review focus, and the structured result Claude returns."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Git convention. Not a schema constraint: a reply that breaks it would fail
# validation and waste the API call, so the CLI warns instead.
SUBJECT_MAX_CHARS = 72


class SpiceLevel(StrEnum):
    """How hard the roast goes. It is always about the code, never the person."""

    MILD = "mild"
    MEDIUM = "medium"
    SAVAGE = "savage"

    @property
    def tone(self) -> str:
        return _TONES[self]


_TONES: dict[SpiceLevel, str] = {
    SpiceLevel.MILD: (
        "Gentle teasing, like a friendly reviewer who smiles while pointing things out. "
        "Two sentences."
    ),
    SpiceLevel.MEDIUM: "Dry, sarcastic senior-engineer humor. Three or four sentences.",
    SpiceLevel.SAVAGE: (
        "Merciless and theatrical, with vivid comparisons, but still only about the code. "
        "Four or five sentences."
    ),
}


class ReviewFocus(StrEnum):
    """An area the review can concentrate on. Also the category of each issue."""

    BUGS = "bugs"
    SECURITY = "security"
    SQL = "sql"
    PERFORMANCE = "performance"
    SIDE_EFFECTS = "side-effects"
    PATTERNS = "patterns"
    REUSE = "reuse"
    BEST_PRACTICES = "best-practices"
    TESTS = "tests"

    @property
    def guide(self) -> str:
        """What to look for, as told to the model."""
        return _FOCUS_GUIDES[self]

    @property
    def needs_codebase(self) -> bool:
        """True when the diff alone can't answer it well: Claude must look at other files."""
        return self in {ReviewFocus.SIDE_EFFECTS, ReviewFocus.PATTERNS, ReviewFocus.REUSE}


_FOCUS_GUIDES: dict[ReviewFocus, str] = {
    ReviewFocus.BUGS: "Logic errors, wrong conditions, unhandled errors and edge cases, "
    "race conditions, leftover debug code.",
    ReviewFocus.SECURITY: "Injection, missing authorization, unsafe input handling, XSS, "
    "secrets in code, insecure defaults, unsafe deserialization.",
    ReviewFocus.SQL: "Queries built from strings, missing bind parameters, risky migrations "
    "(locks, missing indexes or constraints, irreversible changes).",
    ReviewFocus.PERFORMANCE: "N+1 queries, queries or HTTP calls inside loops, missing "
    "eager loading or pagination, needless work in hot paths.",
    ReviewFocus.SIDE_EFFECTS: "Behavior changes for existing callers of changed code: "
    "changed signatures, defaults, callbacks, shared state.",
    ReviewFocus.PATTERNS: "Code that ignores how the codebase already does the same thing "
    "(structure, naming, error handling, layering).",
    ReviewFocus.REUSE: "New code that duplicates an existing helper, service, component or "
    "library already in the codebase.",
    ReviewFocus.BEST_PRACTICES: "Idioms and conventions of the language and framework.",
    ReviewFocus.TESTS: "New behavior without tests, tests that can't fail, missing edge cases.",
}


class Severity(StrEnum):
    HIGH = "high"  # broken behavior, data loss or a security hole
    MEDIUM = "medium"  # a real problem that should be fixed before merging
    LOW = "low"  # worth fixing, but not urgent


class Issue(BaseModel):
    """One concrete problem. Field descriptions are sent to the model as part of the schema."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    file: str = Field(description="Path of the file with the problem, as shown in the diff.")
    line: int = Field(description="Line number in the new version of the file, or 0 if none.")
    category: ReviewFocus = Field(description="Which review area this problem belongs to.")
    severity: Severity = Field(
        description="high: broken behavior, data loss or security hole. "
        "medium: should be fixed before merging. low: worth fixing, not urgent."
    )
    problem: str = Field(
        description="The concrete failure: what situation triggers it and what goes wrong, "
        "plus the fix in a few words."
    )


class RoastResult(BaseModel):
    """What Claude returns. Field descriptions are sent to the model as part of the schema."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    roast: str = Field(description="The roast, specific to this diff.")
    cringe_score: int = Field(ge=0, le=10, description="0 = flawless, 10 = delete the branch.")
    issues: list[Issue] = Field(
        description="Concrete problems in the change, most severe first. Empty if none."
    )
    commit_subject: str = Field(
        description=(
            f"Commit subject line: imperative mood, at most {SUBJECT_MAX_CHARS} characters, "
            "no period."
        )
    )
    commit_body: str = Field(
        description="What changed and why, wrapped at 72 characters. Empty string if not needed."
    )

    @field_validator("commit_subject")
    @classmethod
    def _tidy_subject(cls, subject: str) -> str:
        """Keep the subject one line with no trailing period, so the message stays valid."""
        return " ".join(subject.split()).rstrip(".").rstrip()

    @property
    def subject_too_long(self) -> bool:
        return len(self.commit_subject) > SUBJECT_MAX_CHARS

    @property
    def commit_message(self) -> str:
        """The full commit message as git expects it."""
        if self.commit_body:
            return f"{self.commit_subject}\n\n{self.commit_body}\n"
        return f"{self.commit_subject}\n"


@dataclass(frozen=True, slots=True)
class ReviewPlan:
    """What this review should look at, beyond the diff itself."""

    focus: tuple[ReviewFocus, ...] = ()  # empty: a general review of everything
    stacks: tuple[str, ...] = ()  # detected or given, e.g. ("rails", "react")
    rules: str | None = None  # the team's own review rules (e.g. from .roast.md)
    deep: bool = False  # let Claude explore the repository with read-only tools
    max_tool_calls: int = 0


@dataclass(frozen=True, slots=True)
class RequestTokens:
    """What one API request used. Pricing tiers depend on each request's prompt size."""

    input_tokens: int  # uncached input
    output_tokens: int
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0

    @property
    def prompt_tokens(self) -> int:
        """The whole prompt, cached or not."""
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """What a roast used, summed over every API call it made."""

    model: str  # the model that answered (a refusal fallback can differ from the one asked)
    input_tokens: int  # uncached input
    output_tokens: int  # includes thinking tokens, which are billed as output
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    tool_calls: int = 0
    # Each request on its own, for per-request pricing; empty means one request
    # with the totals above.
    requests: tuple[RequestTokens, ...] = ()

    def per_request(self) -> tuple[RequestTokens, ...]:
        return self.requests or (
            RequestTokens(
                self.input_tokens,
                self.output_tokens,
                self.cache_read_tokens,
                self.cache_write_tokens,
            ),
        )


@dataclass(frozen=True, slots=True)
class RoastOutcome:
    """A roast plus what it cost to make."""

    result: RoastResult
    usage: TokenUsage | None = None
