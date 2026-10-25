"""Gemini through Google AI Studio (`google-genai`, Google's official SDK).

The free tier reads real video, not just frames (ai.google.dev, checked
2026-10-24): `gemini-3.8-flash` is free there; free-tier inputs may be used
by Google to improve its models. Key: https://aistudio.google.com/apikey
(`GEMINI_API_KEY`); model `OFM_GEMINI_MODEL`.

A media file up to `settings.llm.inline_max_bytes` travels inside the
request; a bigger one is uploaded through the Files API, waited on until
Google has processed it, and deleted once answered (Google drops it after
48 h anyway). The answer is asked as JSON following the caller's schema
(`response_json_schema`).

The SDK's own retries are off: `LLMProvider.ask` retries once for every
provider, honouring Google's `RetryInfo` delay on a 429.
"""

from __future__ import annotations

import re
import time
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types

from ofmhelpers.config import settings
from ofmhelpers.llm.base import LLMProvider, register_llm
from ofmhelpers.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMInputError,
    LLMOutputError,
    LLMRefusedError,
    LLMUnavailableError,
)
from ofmhelpers.llm.models import MediaKind, MediaPart, TextPart
from ofmhelpers.log import get_logger

if TYPE_CHECKING:
    from pydantic import BaseModel

    from ofmhelpers.llm.models import Prompt

logger = get_logger(__name__)

# Finish reasons meaning a safety filter stopped the answer.
_REFUSED = {
    "SAFETY",
    "PROHIBITED_CONTENT",
    "BLOCKLIST",
    "SPII",
    "RECITATION",
    "IMAGE_SAFETY",
    "IMAGE_PROHIBITED_CONTENT",
}
_FILE_POLL_S = 2.0
# Google's "client closed request": worth another try, like a timeout.
_CANCELLED = 499
_AUTH = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)
_TRANSIENT = (HTTPStatus.REQUEST_TIMEOUT, _CANCELLED)
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)s$")


@register_llm
class GeminiLLM(LLMProvider):
    name = "gemini"
    label = "Gemini"
    media = frozenset(MediaKind)

    @classmethod
    def default_key(cls) -> str | None:
        key = settings.llm.gemini_api_key
        return key.get_secret_value() if key else None

    @classmethod
    def default_model(cls) -> str:
        return settings.llm.gemini_model

    def _client(self) -> genai.Client:
        s = settings.llm
        fake = None
        if settings.infra.fake_externals:
            from ofmhelpers.llm.gemini.fake import fake_gemini

            fake = httpx.Client(transport=fake_gemini().transport())
        options = types.HttpOptions(
            timeout=int(s.timeout_s * 1000),
            retry_options=types.HttpRetryOptions(attempts=1),
            httpx_client=fake,
        )
        return genai.Client(api_key=self.api_key, http_options=options)

    def _ask(self, prompt: Prompt, answer: type[BaseModel]) -> str:
        client = self._client()
        uploaded: list[str] = []
        try:
            parts: list[types.PartUnionDict] = [
                self._part(client, p, uploaded) for p in prompt.parts
            ]
            response = client.models.generate_content(
                model=self.model,
                contents=parts,
                config=types.GenerateContentConfig(
                    system_instruction=prompt.system or None,
                    temperature=prompt.temperature,
                    response_mime_type="application/json",
                    response_json_schema=answer.model_json_schema(),
                ),
            )
        except genai_errors.APIError as exc:
            raise self._mapped(exc) from exc
        except httpx.HTTPError as exc:
            msg = f"{self.label}: {type(exc).__name__}"
            raise LLMUnavailableError(msg, detail=str(exc)) from exc
        finally:
            self._forget(client, uploaded)
        return self._text(response)

    def _part(
        self, client: genai.Client, part: TextPart | MediaPart, uploaded: list[str]
    ) -> types.Part:
        if isinstance(part, TextPart):
            return types.Part.from_text(text=part.text)
        try:
            size = part.path.stat().st_size
            if size <= settings.llm.inline_max_bytes:
                return types.Part.from_bytes(
                    data=part.path.read_bytes(), mime_type=part.mime_type
                )
        except OSError as exc:
            msg = f"Could not read {part.path.name}"
            raise LLMInputError(msg, detail=str(exc)) from exc
        file = client.files.upload(
            file=part.path, config=types.UploadFileConfig(mime_type=part.mime_type)
        )
        if file.name:
            uploaded.append(file.name)
        file = self._ready(client, file, part.path.name)
        return types.Part.from_uri(
            file_uri=file.uri or "", mime_type=file.mime_type or part.mime_type
        )

    def _ready(self, client: genai.Client, file: types.File, label: str) -> types.File:
        """`file` once Google has processed it (a video takes seconds)."""
        deadline = time.monotonic() + settings.llm.file_ready_timeout_s
        while file.state != types.FileState.ACTIVE:
            if file.state == types.FileState.FAILED:
                msg = f"{self.label} could not process {label}"
                raise LLMInputError(msg, detail=str(file.error))
            if time.monotonic() > deadline:
                msg = f"{self.label} is still processing {label}"
                raise LLMUnavailableError(msg)
            time.sleep(_FILE_POLL_S)
            file = client.files.get(name=file.name or "")
        return file

    def _forget(self, client: genai.Client, names: list[str]) -> None:
        for name in names:
            try:
                client.files.delete(name=name)
            except (genai_errors.APIError, httpx.HTTPError):
                # Google deletes it after 48 h anyway.
                logger.warning("gemini: could not delete %s", name, exc_info=True)

    def _text(self, response: types.GenerateContentResponse) -> str:
        blocked = response.prompt_feedback and response.prompt_feedback.block_reason
        if blocked:
            msg = f"{self.label} refused the prompt ({blocked.name})"
            raise LLMRefusedError(msg)
        candidate = response.candidates[0] if response.candidates else None
        reason = (
            candidate.finish_reason.name
            if candidate and candidate.finish_reason
            else ""
        )
        if reason in _REFUSED:
            msg = f"{self.label} refused to answer ({reason})"
            raise LLMRefusedError(msg)
        text = response.text
        if not text:
            msg = f"{self.label} gave no answer" + (f" ({reason})" if reason else "")
            raise LLMOutputError(msg)
        return text

    def _mapped(self, exc: genai_errors.APIError) -> LLMError:
        code, message = exc.code, exc.message or str(exc)
        detail = f"{code} {exc.status}: {message}"
        if code in _AUTH or "API key not valid" in message:
            return LLMAuthError(self.label, detail)
        if code == HTTPStatus.NOT_FOUND:
            return LLMConfigError(f"{self.label}: {message}", detail)
        if code == HTTPStatus.TOO_MANY_REQUESTS:
            return LLMUnavailableError(
                f"{self.label}: rate limited or out of free quota",
                detail,
                retry_after_s=_retry_delay(exc.details),
            )
        if code in _TRANSIENT or code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return LLMUnavailableError(f"{self.label} is unavailable ({code})", detail)
        return LLMInputError(f"{self.label} rejected the request: {message}", detail)


def _retry_delay(body: Any) -> float | None:
    """Google's `RetryInfo.retryDelay` ("32s") in an error body, if any."""
    error = body.get("error", {}) if isinstance(body, dict) else {}
    for item in error.get("details", []) or []:
        found = _DURATION.match(str(item.get("retryDelay", "")))
        if found:
            return float(found.group(1))
    return None
