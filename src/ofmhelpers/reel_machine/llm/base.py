"""
One capability, one method: hand a provider the reel and a prompt, get raw
text back. Parsing/validating that text is `schema.parse_analysis`'s job,
not the provider's -- so every provider stays a thin API call and the
"is this actually a usable prompt" rule lives in exactly one place.
"""

from pathlib import Path
from typing import Protocol


class LLMProvider(Protocol):
    name: str

    def analyze_video(self, video_path: Path, prompt: str) -> str: ...
