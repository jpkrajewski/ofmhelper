"""
The pluggable script-writer interface. Every provider below takes the same
Teardown/Shape/Look/duration and returns the same plain-text prompt package
-- callers (pipeline.py) never need to know which one is active.

Two separate capabilities, because they have very different costs:
- analyze_reel: VISION -- actually looks at the contact sheet to figure out
  the hook/viral mechanic/camera look. This is the "watch the reel" step the
  old bundle's interactive agent did by eye; the template provider can't do
  it at all (no vision, no cost) and leaves the (edit me) placeholders from
  teardown.build_teardown_draft in place.
- write_prompt_package: writes/punches up the actual prompt text. The
  template provider always does this (deterministic, free); Groq/Anthropic
  can also rewrite the dialogue on top of it.
"""

from pathlib import Path
from typing import Protocol

from ofmhelpers.reel_machine.gender import DEFAULT_GENDER
from ofmhelpers.reel_machine.looks import Look
from ofmhelpers.reel_machine.shapes import Shape
from ofmhelpers.reel_machine.teardown import Teardown


class LLMProvider(Protocol):
    name: str

    def analyze_reel(self, contact_sheet: Path, transcript_text: str) -> dict: ...

    def write_prompt_package(
        self,
        teardown: Teardown,
        shape: Shape,
        look: Look,
        duration: int,
        target: str = "",
        gender: str = DEFAULT_GENDER,
    ) -> str: ...
