"""
The text provider: Groq's free tier, used for the module's second pass (see
`hunt.py`).

It is a separate vendor from the video pass on purpose. Pass 2 is a cheap
"rewrite this prose as search terms" call that runs on every intake, and
putting it on Gemini would spend the one quota that actually matters -- the
free tier that takes video -- on the nice-to-have.

Groq's API is OpenAI-shaped (`/openai/v1/chat/completions`), so this is one
plain `requests` POST, no SDK, in keeping with `aigenproviders/kaiai`.
GROQ_API_KEY comes from https://console.groq.com (free, no card);
GROQ_MODEL overrides the model.

Like every provider here it returns the model's answer as raw text and
validates nothing: `hunt.py` decides what a usable answer is.
"""

from typing import ClassVar

import requests

from ofmhelpers.config import settings

GROQ_URL = settings.reel_machine.groq_url
_TIMEOUT_S = settings.reel_machine.groq_timeout_s
# Warm, but not creative: this pass names a niche, it doesn't write.
_TEMPERATURE = settings.reel_machine.groq_temperature


class GroqProvider:
    name: ClassVar[str] = "groq"

    api_key: str
    model: str

    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        s = settings.reel_machine
        key = api_key or s.groq_api_key
        if key is None:
            msg = "GROQ_API_KEY"
            raise KeyError(msg)
        self.api_key = key
        self.model = model or s.groq_model

    def complete_json(self, prompt: str) -> str:
        """One chat completion, asked for JSON. No retry: the caller treats
        this whole pass as best-effort and has its own fallback, so a busy
        model costs a worker nothing to give up on."""
        payload: dict = {
            "model": self.model,
            "temperature": _TEMPERATURE,
            # Server-side JSON mode, so a chatty model can't wrap the answer
            # in prose the way the prompt alone doesn't always prevent.
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        }
        response = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {self.api_key}"},
            json=payload,
            timeout=_TIMEOUT_S,
        )
        response.raise_for_status()
        content: str = response.json()["choices"][0]["message"]["content"]
        return content
