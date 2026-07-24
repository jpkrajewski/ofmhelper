"""
Another free-tier option, specifically for VISION: Google's Gemini API has a
genuinely free tier that includes image input, unlike Groq's current
account/model lineup, which has no vision-capable model at all (confirmed
against the live /models list -- see groq_provider.py's docstring).

Defaults to the `gemini-flash-latest` ALIAS, not a pinned version -- Google
documents this as always pointing at their current flash release
(https://ai.google.dev/gemini-api/docs/models), specifically so callers
don't have to chase model deprecations by hand. Pinning a dated model id
here (e.g. gemini-2.0-flash) is exactly what broke this the first time:
Google retired it and free-tier requests started 429ing with a `limit: 0`
quota instead of a normal "you used it all up" error. If Google ever drops
the alias itself, pass a current model id explicitly (or set GEMINI_MODEL).

Requires GEMINI_API_KEY (https://aistudio.google.com/apikey -- free, no
card required for the free tier) and the `google-genai` package (a base
dependency of this project, see pyproject.toml). Unrelated to gdrive/ --
that's OAuth-as-a-user Google Drive access; this is a plain API-key call to
the Gemini API.
"""

import json
import os
from pathlib import Path

from ofmhelpers.reel_machine.gender import DEFAULT_GENDER
from ofmhelpers.reel_machine.llm.groq_provider import (
    ANALYZE_SYSTEM_PROMPT,
    WRITE_SYSTEM_PROMPT,
)
from ofmhelpers.reel_machine.looks import Look
from ofmhelpers.reel_machine.prompt_builder import build_prompt_package
from ofmhelpers.reel_machine.shapes import Shape
from ofmhelpers.reel_machine.teardown import Teardown


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = api_key or os.environ["GEMINI_API_KEY"]
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-flash-latest")

    def analyze_reel(self, contact_sheet: Path, transcript_text: str) -> dict:
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=[
                types.Part.from_bytes(
                    data=contact_sheet.read_bytes(), mime_type="image/jpeg"
                ),
                f"Transcript:\n{transcript_text}",
            ],
            config=types.GenerateContentConfig(
                system_instruction=ANALYZE_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.4,
            ),
        )
        return json.loads(response.text or "{}")

    def write_prompt_package(
        self,
        teardown: Teardown,
        shape: Shape,
        look: Look,
        duration: int,
        target: str = "",
        gender: str = DEFAULT_GENDER,
    ) -> str:
        from google import genai
        from google.genai import types

        draft = build_prompt_package(
            teardown, shape, look, duration, target=target, gender=gender
        )
        client = genai.Client(api_key=self.api_key)
        response = client.models.generate_content(
            model=self.model,
            contents=draft,
            config=types.GenerateContentConfig(
                system_instruction=WRITE_SYSTEM_PROMPT,
                temperature=0.7,
            ),
        )
        return response.text or draft
