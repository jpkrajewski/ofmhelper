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
import time
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

# How long to wait for an uploaded video to finish processing (Gemini
# requires state == "ACTIVE" before it can be referenced in a generate_content
# call) before giving up and falling back to the contact-sheet image.
_VIDEO_ACTIVE_TIMEOUT_S = 60
_VIDEO_ACTIVE_POLL_S = 2


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        s = settings.reel_machine
        self.api_key = api_key or s.gemini_api_key
        if self.api_key is None:
            raise KeyError("GEMINI_API_KEY")
        self.model = model or s.gemini_model

    def analyze_reel(
        self,
        contact_sheet: Path,
        transcript_text: str,
        video_path: Path | None = None,
    ) -> dict:
        """Prefers sending the actual video -- a static contact sheet loses
        motion/timing/transitions, which is what made action descriptions
        unreliable. Falls back to the contact-sheet image (same as before)
        if no video_path is given, or if the upload/processing step fails,
        so a flaky upload never fails the whole intake job."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)

        video_part = self._upload_video_part(client, video_path) if video_path else None
        media_part = video_part or types.Part.from_bytes(
            data=contact_sheet.read_bytes(), mime_type="image/jpeg"
        )

        response = client.models.generate_content(
            model=self.model,
            contents=[media_part, f"Transcript:\n{transcript_text}"],
            config=types.GenerateContentConfig(
                system_instruction=ANALYZE_SYSTEM_PROMPT,
                response_mime_type="application/json",
                temperature=0.4,
            ),
        )
        return json.loads(response.text or "{}")

    def _upload_video_part(self, client, video_path: Path):
        """Uploads the reel via the Files API and waits for it to become
        ACTIVE (required before Gemini can reference it in a generate_content
        call). Returns None (triggering the contact-sheet fallback) rather
        than raising, on any failure or timeout."""
        try:
            uploaded = client.files.upload(file=str(video_path))
            deadline = time.monotonic() + _VIDEO_ACTIVE_TIMEOUT_S
            while uploaded.state and uploaded.state.name == "PROCESSING":
                if time.monotonic() > deadline:
                    print(
                        f"[reel_machine] gemini video upload for {video_path.name} "
                        "timed out waiting to become ACTIVE, falling back to "
                        "contact sheet",
                        flush=True,
                    )
                    return None
                time.sleep(_VIDEO_ACTIVE_POLL_S)
                uploaded = client.files.get(name=uploaded.name)
            if not uploaded.state or uploaded.state.name != "ACTIVE":
                print(
                    f"[reel_machine] gemini video upload for {video_path.name} "
                    f"ended in state {uploaded.state}, falling back to contact sheet",
                    flush=True,
                )
                return None
            return uploaded
        except Exception as exc:
            print(
                f"[reel_machine] gemini video upload failed ({exc}), falling "
                "back to contact sheet",
                flush=True,
            )
            return None

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
        return strip_llm_preamble(response.text or draft, draft)
