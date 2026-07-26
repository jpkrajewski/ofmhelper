"""
Optional, best-quality script writer -- the Claude API. Billed separately
from a claude.ai Pro subscription (pay-per-token, typically a few cents per
script); off by default, opt in via REEL_MACHINE_LLM_PROVIDER=anthropic.

Like GroqProvider, this does both the vision teardown (analyze_reel -- looks
at the contact sheet to figure out hook/viral_mechanic/camera_look) and the
prompt-package rewrite (write_prompt_package).

Requires ANTHROPIC_API_KEY and the optional `anthropic` package:
    pip install 'ofmhelpers[llm-anthropic]'
"""

import base64
import json
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.reel_machine.gender import DEFAULT_GENDER
from ofmhelpers.reel_machine.llm.base import strip_llm_preamble
from ofmhelpers.reel_machine.llm.groq_provider import (
    ANALYZE_SYSTEM_PROMPT,
    WRITE_SYSTEM_PROMPT,
)
from ofmhelpers.reel_machine.looks import Look
from ofmhelpers.reel_machine.prompt_builder import build_prompt_package
from ofmhelpers.reel_machine.shapes import Shape
from ofmhelpers.reel_machine.teardown import Teardown


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str = "claude-sonnet-5"):
        self.api_key = api_key or settings.reel_machine.anthropic_api_key
        if self.api_key is None:
            msg = "ANTHROPIC_API_KEY"
            raise KeyError(msg)
        self.model = model

    def analyze_reel(
        self,
        contact_sheet: Path,
        transcript_text: str,
        video_path: Path | None = None,
    ) -> dict:
        # Claude's Messages API vision support is image-only (no native
        # video input), so video_path is accepted only to satisfy the
        # shared LLMProvider interface and is never used -- always analyzes
        # the contact sheet. See gemini_provider.py for real video input.
        import anthropic

        client = anthropic.Anthropic(api_key=self.api_key)
        image_b64 = base64.b64encode(contact_sheet.read_bytes()).decode("ascii")
        response = client.messages.create(
            model=self.model,
            max_tokens=1024,
            system=ANALYZE_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/jpeg",
                                "data": image_b64,
                            },
                        },
                        {"type": "text", "text": f"Transcript:\n{transcript_text}"},
                    ],
                }
            ],
        )
        return json.loads(response.content[0].text or "{}")

    def write_prompt_package(
        self,
        teardown: Teardown,
        shape: Shape,
        look: Look,
        duration: int,
        target: str = "",
        gender: str = DEFAULT_GENDER,
    ) -> str:
        import anthropic

        draft = build_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=2048,
            system=WRITE_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": draft}],
        )
        return strip_llm_preamble(response.content[0].text or draft, draft)
