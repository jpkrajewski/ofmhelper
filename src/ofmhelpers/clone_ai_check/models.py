"""What a AI check asks for and what it gives back.

`CloneAiVerdict` is the answer the video model is told to give (its JSON
schema goes with the prompt). Field order is the order the model writes
them: what it saw and why first, the verdict last, so the verdict comes out
of the reasoning instead of before it.

`CloneAiCheck` is the whole check as the app keeps it (`clone_ai_checks` table):
the verdict plus everything needed to debug one -- the prompt, the raw
answer, who answered, the stills' times, how long each step took, and the
error when the check itself failed.
"""

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


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

    # Not asked of the model: it follows from the glitches by the rule the
    # model is given (GLITCHY = at least one glitch stops the post). Asked,
    # Groq's qwen kept leaving it out of an otherwise complete answer and
    # strict mode refused the whole answer (dev 2026-10-25, twice in a row).
    @computed_field  # type: ignore[prop-decorator]
    @property
    def verdict(self) -> Verdict:
        blocking = any(g.severity == "blocking" for g in self.glitches)
        return Verdict.GLITCHY if blocking else Verdict.GOOD


class CloneAiCheck(BaseModel):
    """One check, kept whole. `verdict` is None when the check itself
    failed (`error` says why)."""

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

    @property
    def glitchy(self) -> bool:
        return self.verdict is Verdict.GLITCHY
