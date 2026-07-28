"""
The default provider: Google's Gemini API, the free tier that actually takes
VIDEO input rather than a static image -- which is the whole point here,
since motion, timing, and transitions are exactly what a per-second Seedance
timeline needs.

Defaults to the `gemini-flash-latest` ALIAS, not a pinned version -- Google
documents this as always pointing at their current flash release
(https://ai.google.dev/gemini-api/docs/models), specifically so callers
don't have to chase model deprecations by hand. Pinning a dated model id
here (e.g. gemini-2.0-flash) is exactly what broke this the first time:
Google retired it and free-tier requests started 429ing with a `limit: 0`
quota instead of a normal "you used it all up" error. Override with
GEMINI_MODEL if Google ever drops the alias itself.

Requires GEMINI_API_KEY (https://aistudio.google.com/apikey -- free, no card
required) and the `google-genai` package (a base dependency, see
pyproject.toml). Unrelated to gdrive/ -- that's OAuth-as-a-user Drive
access; this is a plain API-key call.
"""

import time
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

# Gemini requires an uploaded file to reach state == "ACTIVE" before it can
# be referenced in a generate_content call.
_VIDEO_ACTIVE_TIMEOUT_S = 120
_VIDEO_ACTIVE_POLL_S = 2


class GeminiProvider:
    name = "gemini"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        s = settings.reel_machine
        self.api_key = api_key or s.gemini_api_key
        if self.api_key is None:
            msg = "GEMINI_API_KEY"
            raise KeyError(msg)
        self.model = model or s.gemini_model

    def analyze_video(self, video_path: Path, prompt: str) -> str:
        """Uploads the reel and asks for the prompt JSON. Unlike the old
        contact-sheet path there is no image fallback: a static grid can't
        answer half the questions the prompt asks (pacing, camera drift,
        per-second actions), so a failed upload fails the job instead of
        silently downgrading the analysis."""
        from google import genai
        from google.genai import types

        client = genai.Client(api_key=self.api_key)
        video_part = self._upload_video(client, video_path)

        response = client.models.generate_content(
            model=self.model,
            contents=[video_part, prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
            ),
        )
        return response.text or ""

    def _upload_video(self, client, video_path: Path):
        uploaded = client.files.upload(file=str(video_path))
        deadline = time.monotonic() + _VIDEO_ACTIVE_TIMEOUT_S
        while uploaded.state and uploaded.state.name == "PROCESSING":
            if time.monotonic() > deadline:
                msg = (
                    f"Gemini took longer than {_VIDEO_ACTIVE_TIMEOUT_S}s to process "
                    f"{video_path.name} -- try again, or upload a shorter clip"
                )
                raise RuntimeError(msg)
            time.sleep(_VIDEO_ACTIVE_POLL_S)
            uploaded = client.files.get(name=uploaded.name)
        if not uploaded.state or uploaded.state.name != "ACTIVE":
            msg = f"Gemini could not process {video_path.name} (state {uploaded.state})"
            raise RuntimeError(msg)
        return uploaded
