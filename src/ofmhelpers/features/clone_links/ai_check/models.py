# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, Field

from ofmhelpers.db.clone_ai_checks.schema import (
    CloneAiCheck,
    ErrorKind,
    Glitch,
    Verdict,
)

__all__ = ["CloneAiCheck", "CloneAiVerdict", "ErrorKind", "Glitch", "Verdict"]


class CloneAiVerdict(BaseModel):
    reasoning: str = Field(description="One short sentence: what you saw and why.")
    glitches: list[Glitch] = Field(
        description="Every glitch you saw, minor ones too; empty when none.",
    )
    glitch_percent: int = Field(
        ge=0,
        le=100,
        description="How sure you are the reel is too glitchy to post:"
        " 0 = clean, 100 = certainly unpostable.",
    )

    def verdict(self, glitchy_percent: int) -> Verdict:
        blocking = any(g.severity == "blocking" for g in self.glitches)
        unsure = self.glitch_percent >= glitchy_percent
        return Verdict.GLITCHY if blocking or unsure else Verdict.GOOD
