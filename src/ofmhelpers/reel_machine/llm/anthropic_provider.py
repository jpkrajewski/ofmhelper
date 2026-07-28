"""
The paid alternative: the Claude API. Billed per token, separately from a
claude.ai subscription; off by default, opt in with
REEL_MACHINE_LLM_PROVIDER=anthropic.

Claude's vision is IMAGE-ONLY -- the Messages API has no native video input
-- so this provider renders the reel down to a contact sheet
(intake.build_contact_sheet) and analyzes that. It is therefore strictly
weaker than Gemini here: a tiled grid of 1-fps frames loses motion, timing,
and audio, all of which ANALYSIS_PROMPT explicitly asks about. Use Gemini
unless you have a reason not to.

Requires ANTHROPIC_API_KEY and the optional `anthropic` package:
    pip install 'ofmhelpers[llm-anthropic]'
"""

import base64
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.reel_machine.intake import build_contact_sheet

logger = get_logger(__name__)


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, api_key: str | None = None, model: str = "claude-opus-5"):
        self.api_key = api_key or settings.reel_machine.anthropic_api_key
        if self.api_key is None:
            msg = "ANTHROPIC_API_KEY"
            raise KeyError(msg)
        self.model = model

    def analyze_video(self, video_path: Path, prompt: str) -> str:
        import anthropic

        contact_sheet = build_contact_sheet(video_path, video_path.parent)
        image_b64 = base64.b64encode(contact_sheet.read_bytes()).decode("ascii")

        client = anthropic.Anthropic(api_key=self.api_key)
        response = client.messages.create(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
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
                        {
                            "type": "text",
                            "text": (
                                "The image is a contact sheet: one frame per second "
                                "of the reel, tiled left-to-right, top-to-bottom.\n\n"
                                f"{prompt}"
                            ),
                        },
                    ],
                }
            ],
        )
        if response.stop_reason == "refusal":
            msg = "Claude declined to analyze this reel"
            raise RuntimeError(msg)
        return next((b.text for b in response.content if b.type == "text"), "")
