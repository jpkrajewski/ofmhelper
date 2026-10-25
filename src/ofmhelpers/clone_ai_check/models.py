"""What a AI check asks for and what it gives back.

`CloneAiVerdict` is the answer the video model is told to give (its JSON
schema goes with the prompt). Field order is the order the model writes
them: what it saw and why first, how sure it is last, so the percent comes
out of the reasoning instead of before it. GOOD or GLITCHY is not asked:
`CloneAiVerdict.verdict` reads it off the answer.

`CloneAiCheck` is the whole check as the app keeps it (`clone_ai_checks` table):
the verdict plus everything needed to debug one -- the prompt, the raw
answer, who answered, the stills' times, how long each step took, the tokens
the answer used, and the error when the check itself failed.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

# Why a check has no verdict: every model out of quota or down ("quota"),
# none set up -- no key, unknown model ("setup"), anything else ("failed").
ErrorKind = Literal["quota", "setup", "failed"]


class Verdict(StrEnum):
    GOOD = "GOOD"
    GLITCHY = "GLITCHY"


class Glitch(BaseModel):
    at_s: float = Field(
        description="When it shows: the time label of the still, in seconds."
    )
    what: str = Field(description="What is wrong, a few words, e.g. 'no belly button'.")
    severity: Literal["minor", "blocking"] = Field(
        description="blocking: stops the reel from being posted; minor: noticeable"
        " but postable."
    )


class CloneAiVerdict(BaseModel):
    reasoning: str = Field(description="One short sentence: what you saw and why.")
    glitches: list[Glitch] = Field(
        description="Every glitch you saw, minor ones too; empty when none."
    )
    glitch_percent: int = Field(
        ge=0,
        le=100,
        description="How sure you are the reel is too glitchy to post:"
        " 0 = clean, 100 = certainly unpostable.",
    )

    # Not asked of the model: it follows from its answer by the rule it is
    # given. Asked, Groq's qwen kept leaving it out of an otherwise complete
    # answer and strict mode refused the whole answer (dev 2026-10-25).
    def verdict(self, glitchy_percent: int) -> Verdict:
        """GLITCHY when any glitch stops the post or the model is at least
        `glitchy_percent` sure the reel should not be posted (minor glitches
        can add up): when the two disagree, the reel goes where a person
        looks first."""
        blocking = any(g.severity == "blocking" for g in self.glitches)
        unsure = self.glitch_percent >= glitchy_percent
        return Verdict.GLITCHY if blocking or unsure else Verdict.GOOD


class CloneAiCheck(BaseModel):
    """One check, kept whole. `verdict` is None when the check itself
    failed: `error` says why in a few words (safe to show), `error_kind`
    what kind of failure, `error_detail` the provider's own text (logs and
    the row only, never a page). `*_tokens`: what the answer used, when the
    provider says."""

    video: str
    video_seconds: float | None = None
    stills_at: list[float] = []
    frame_px: int
    verdict: Verdict | None = None
    glitch_percent: int | None = None
    glitches: list[Glitch] = []
    reasoning: str = ""
    provider: str = ""
    model: str = ""
    prompt_system: str
    prompt_text: str
    raw_answer: str = ""
    attempts: int = 0
    sheet_s: float = 0.0
    llm_s: float = 0.0
    error: str | None = None
    error_kind: ErrorKind | None = None
    error_detail: str | None = None
    input_tokens: int | None = None
    thinking_tokens: int | None = None
    output_tokens: int | None = None

    @property
    def glitchy(self) -> bool:
        return self.verdict is Verdict.GLITCHY
