"""Domain types: roast intensity and the structured result Claude returns."""

from __future__ import annotations

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


class RoastResult(BaseModel):
    """What Claude returns. Field descriptions are sent to the model as part of the schema."""

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True)

    roast: str = Field(description="The roast, specific to this diff.")
    cringe_score: int = Field(ge=0, le=10, description="0 = flawless, 10 = delete the branch.")
    issues: list[str] = Field(
        description="Concrete problems visible in the diff, one sentence each. Empty if none."
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
