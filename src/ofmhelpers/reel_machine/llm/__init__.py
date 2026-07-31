"""
Two capabilities, one method each: hand a provider the reel (or a block of
text) and a prompt, get raw text back. Parsing/validating that text is the
caller's job -- `schema.parse_analysis` for the video pass, `hunt.py` for the
text one -- so a provider stays a thin API call and the "is this actually
usable" rule lives in exactly one place per pass.

Two Protocols rather than one, because the two passes are not the same
capability: `LLMProvider` needs an API that takes video (Gemini is the only
free one that does), `TextLLMProvider` only needs chat completions, which is
why the cheap second pass can run on a different vendor entirely. Both are
resolved by name through `registry.py`, so `pipeline.analyze` and
`hunt.suggest_hunt` never know which API they are talking to.
"""

from pathlib import Path
from typing import ClassVar, Protocol


class LLMProvider(Protocol):
    """Pass 1: watch the reel, describe it as the Seedance prompt JSON."""

    name: ClassVar[str]

    def analyze_video(
        self, video_path: Path, prompt: str, *, system_prompt: str = ""
    ) -> str: ...


class TextLLMProvider(Protocol):
    """Pass 2: read pass 1's description, answer with JSON. No video, so this
    is the capability every cheap free-tier text model has."""

    name: ClassVar[str]

    def complete_json(self, prompt: str) -> str: ...


__all__ = ["LLMProvider", "TextLLMProvider"]
