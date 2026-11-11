"""What a AI check asks for and what it gives back.

`CloneAiVerdict` is the answer the video model is told to give (its JSON
schema goes with the prompt). Field order is the order the model writes
them: what it saw and why first, how sure it is last, so the percent comes
out of the reasoning instead of before it. GOOD or GLITCHY is not asked:
`CloneAiVerdict.verdict` reads it off the answer.

`CloneAiCheck` (`schemas/clone_ai_checks.py`) is the whole check as the app
keeps it (`clone_ai_checks` table):
the verdict plus everything needed to debug one -- the prompt, the raw
answer, who answered, the stills' times, how long each step took, the tokens
the answer used, and the error when the check itself failed.
"""

from pydantic import BaseModel, Field

from ofmhelpers.schemas.clone_ai_checks import (
    CloneAiCheck,
    ErrorKind,
    Glitch,
    Verdict,
)

__all__ = ["CloneAiCheck", "CloneAiVerdict", "ErrorKind", "Glitch", "Verdict"]


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
