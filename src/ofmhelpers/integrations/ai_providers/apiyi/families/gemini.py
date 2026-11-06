"""Nano Banana over Google's Gemini API (docs.apiyi.com nano-banana dev guide,
2026-11-06): `POST /v1beta/models/{model}:generateContent`, answered in the
request (`sync.py`). Text-to-image and edit are one call: a text part plus
one `inlineData` part per reference image (base64; at most 14, 7 MB each,
under 100 MB a request). `generationConfig.imageConfig`: `aspectRatio`
(left out = the last input image's shape) and `imageSize` (1K/2K/4K; "512"
on Nano Banana 2; Lite only 1K).

The answer's image is the LAST `inlineData` part (Pro returns drafts before
it). No image = refused: `finishReason` IMAGE_SAFETY / SAFETY /
PROHIBITED_CONTENT ..., `promptFeedback.blockReason`, or a text-only answer;
read as "flagged by moderation".
"""

from typing import Any

from pydantic import Field

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.apiyi.client import ApiyiClient
from ofmhelpers.integrations.ai_providers.apiyi.families.sync import Made, SyncImages
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER, Text
from ofmhelpers.integrations.ai_providers.errors import ProviderGenerationError
from ofmhelpers.integrations.ai_providers.http import Lenient, parsed
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload
from ofmhelpers.media.image_fit import ImageLimits

# Per image before base64 (docs: 7 MB), all of them under APIYI's 100 MB.
_MAX_IMAGE_BYTES = 7 * 1000 * 1000
_MAX_REQUEST_IMAGE_BYTES = 60 * 1000 * 1000
_MAX_SIDE = 8000
AUTO = "auto"


class GeminiInline(Lenient):
    mime_type: Text = Field(default="", alias="mimeType")
    data: Text = ""


class GeminiPart(Lenient):
    text: str | None = None
    inline_data: GeminiInline | None = Field(default=None, alias="inlineData")


class GeminiContent(Lenient):
    parts: list[GeminiPart] | None = None


class GeminiCandidate(Lenient):
    content: GeminiContent | None = None
    finish_reason: Text = Field(default="", alias="finishReason")
    finish_message: Text = Field(default="", alias="finishMessage")


class GeminiFeedback(Lenient):
    block_reason: Text = Field(default="", alias="blockReason")


class GeminiAnswer(Lenient):
    candidates: list[GeminiCandidate] | None = None
    prompt_feedback: GeminiFeedback | None = Field(default=None, alias="promptFeedback")

    def _parts(self) -> list[GeminiPart]:
        return [
            part
            for candidate in self.candidates or []
            for part in (candidate.content.parts if candidate.content else None) or []
        ]

    def image(self) -> GeminiInline | None:
        found = [
            p.inline_data for p in self._parts() if p.inline_data and p.inline_data.data
        ]
        return found[-1] if found else None

    def refusal(self) -> str:
        """Why no image came, worded so the card reads it."""
        if self.prompt_feedback and self.prompt_feedback.block_reason:
            return (
                f"{PROVIDER}: flagged by moderation: input blocked "
                f"({self.prompt_feedback.block_reason})"
            )
        reasons = [c.finish_reason for c in self.candidates or [] if c.finish_reason]
        said = " ".join(p.text for p in self._parts() if p.text).strip()
        reason = reasons[-1] if reasons else ""
        if reason and reason not in ("STOP", "MAX_TOKENS", "NO_IMAGE"):
            return f"{PROVIDER}: flagged by moderation: {reason} {said[:200]}".strip()
        if said:
            return f"{PROVIDER}: answered text, no image: {said[:300]}"
        return f"{PROVIDER}: no image ({reason or 'empty answer'})"


class Gemini(SyncImages):
    prefixes = ("gemini", "nano-banana")

    def make(self, client: ApiyiClient, spec: ModelSpec, payload: Payload) -> Made:
        refs = payload.get("image_urls") or []
        images = refs if isinstance(refs, list) else [refs]
        each = min(_MAX_IMAGE_BYTES, _MAX_REQUEST_IMAGE_BYTES // max(len(images), 1))
        limits = ImageLimits(max_bytes=each, max_side=_MAX_SIDE)
        parts: list[dict[str, Any]] = [{"text": str(payload.get("prompt", ""))}]
        for ref in images:
            mime, data = client.inline(str(ref), limits)
            parts.append({"inlineData": {"mimeType": mime, "data": data}})
        image_config: dict[str, Any] = {}
        ratio = payload.get("aspect_ratio")
        if ratio and ratio != AUTO:
            image_config["aspectRatio"] = ratio
        if payload.get("resolution"):
            image_config["imageSize"] = payload["resolution"]
        body = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {
                "responseModalities": ["IMAGE"],
                **({"imageConfig": image_config} if image_config else {}),
            },
        }
        response = client.submit(
            f"/v1beta/models/{spec.model_id}:generateContent",
            json=body,
            timeout=settings.apiyi.image_timeout_s,
        )
        answer = parsed(response, GeminiAnswer, PROVIDER)
        image = answer.image()
        if image is None:
            raise ProviderGenerationError(answer.refusal())
        return Made(images=[(image.mime_type or "image/png", image.data)])
