"""
One capability, one method: hand a provider the reel and a prompt, get raw
text back. Parsing/validating that text is `schema.parse_analysis`'s job,
not the provider's -- so the provider stays a thin API call and the "is this
actually a usable prompt" rule lives in exactly one place.

There is one provider (`gemini_provider.GeminiProvider`). The Protocol stays
because `registry.get_provider` is typed against a capability rather than a
class, which is what keeps `pipeline.analyze` from knowing which API it is
talking to.
"""

from pathlib import Path
from typing import ClassVar, Protocol


class LLMProvider(Protocol):
    name: ClassVar[str]

    def analyze_video(self, video_path: Path, prompt: str) -> str: ...


__all__ = ["LLMProvider"]
