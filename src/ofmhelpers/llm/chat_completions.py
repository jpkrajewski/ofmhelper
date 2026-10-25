"""Providers speaking the OpenAI-style `POST <base>/chat/completions` (Groq,
Cerebras, ... -- most hosted models do), over httpx.

One small client instead of an SDK per vendor: the wire is the same for all
of them, and each vendor's SDK (or OpenAI's, which follows them all) would be
one more dependency for one endpoint. A vendor is a subclass naming its base
URL, key, model, the media its model reads and how many images a request may
carry; its subpackage's `fake.py` builds a `chat_completions_fake` for tests.

Images travel inline as base64 data URLs, shrunk first
(`settings.llm.image_max_side`/`image_max_bytes`): free plans count tokens
per minute, and a phone photo is thousands of them. The answer is asked as
strict JSON Schema (`response_format: json_schema, strict: true`), the schema
made to fit strict mode's rules (`strict_schema`).
"""

from __future__ import annotations

import base64
import tempfile
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.llm.base import LLMProvider
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
from ofmhelpers.utils.image_fit import ImageLimits, fitted

if TYPE_CHECKING:
    from ofmhelpers.llm.models import Prompt

_AUTH = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)
# Said on top of the strict schema: a vendor may check the answer against
# the schema after generating it rather than constrain it (Groq: a plain
# "Hola" came back as a 400 `json_validate_failed`, 2026-10-25).
_JSON_ONLY = "Reply with one JSON object that follows the given schema, nothing else."
# The vendor's error code for an answer that failed its own schema check.
_BAD_JSON = "json_validate_failed"
_TRANSIENT = (HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.CONFLICT)
# Keywords strict mode rejects (Cerebras' list, the strictest; OpenAI's and
# Groq's are subsets). Pydantic writes some of them for constrained fields.
_NOT_STRICT = frozenset(
    {
        "$defs",
        "default",
        "examples",
        "format",
        "maxItems",
        "maxLength",
        "minItems",
        "minLength",
        "pattern",
        "title",
    }
)


def strict_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """`schema` (as Pydantic writes it) in strict mode's form: `$ref`s
    inlined, every object closed (`additionalProperties: false`) with every
    property required, keywords strict mode rejects dropped."""
    defs = schema.get("$defs", {})

    def walk(node: Any) -> Any:
        if isinstance(node, list):
            return [walk(n) for n in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[str(node["$ref"]).rsplit("/", 1)[-1]])
        out = {
            key: (
                {name: walk(sub) for name, sub in value.items()}
                if key == "properties"
                else walk(value)
            )
            for key, value in node.items()
            if key not in _NOT_STRICT
        }
        if out.get("type") == "object":
            out["required"] = list(out.get("properties", {}))
            out["additionalProperties"] = False
        return out

    return walk(schema)


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: str | None = None


class _Choice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: _Message
    finish_reason: str | None = None


class _Completion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    choices: list[_Choice] = []


class ChatCompletionsLLM(LLMProvider):
    """One vendor's `/chat/completions`; subclasses set the class vars and
    the key/model/fake hooks."""

    base_url: ClassVar[str]
    media = frozenset({MediaKind.IMAGE})
    # Vendor-specific request fields.
    extra_body: ClassVar[dict[str, Any]] = {}

    @classmethod
    def fake_transport(cls) -> httpx.BaseTransport:
        """The vendor's wire fake (OFM_FAKE_EXTERNALS)."""
        raise NotImplementedError

    def _client(self) -> httpx.Client:
        transport = self.fake_transport() if settings.infra.fake_externals else None
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=settings.llm.timeout_s,
            transport=transport,
        )

    def _ask(self, prompt: Prompt, answer: type[BaseModel]) -> str:
        with tempfile.TemporaryDirectory(prefix="llm-img-") as work:
            body = {
                "model": self.model,
                "messages": self._messages(prompt, Path(work)),
                "temperature": prompt.temperature,
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": answer.__name__,
                        "strict": True,
                        "schema": strict_schema(answer.model_json_schema()),
                    },
                },
                **self.extra_body,
            }
        try:
            with self._client() as client:
                response = client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            msg = f"{self.label}: {type(exc).__name__}"
            raise LLMUnavailableError(msg, detail=str(exc)) from exc
        if response.is_error:
            raise self._mapped(response)
        return self._text(response)

    def _messages(self, prompt: Prompt, work: Path) -> list[dict[str, Any]]:
        content = [self._part(p, work) for p in prompt.parts]
        system = "\n\n".join(filter(None, (prompt.system, _JSON_ONLY)))
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": content},
        ]

    def _part(self, part: TextPart | MediaPart, work: Path) -> dict[str, Any]:
        if isinstance(part, TextPart):
            return {"type": "text", "text": part.text}
        s = settings.llm
        limits = ImageLimits(max_bytes=s.image_max_bytes, max_side=s.image_max_side)
        try:
            image = fitted(part.path, work, limits)
            data = base64.b64encode(image.read_bytes()).decode()
        except OSError as exc:
            msg = f"Could not read {part.path.name}"
            raise LLMInputError(msg, detail=str(exc)) from exc
        mime = "image/jpeg" if image != part.path else part.mime_type
        return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}

    def _text(self, response: httpx.Response) -> str:
        try:
            found = _Completion.model_validate_json(response.content)
        except ValidationError as exc:
            msg = f"{self.label} sent an unreadable answer"
            raise LLMOutputError(msg, detail=response.text[:2000]) from exc
        choice = found.choices[0] if found.choices else None
        reason = (choice.finish_reason if choice else None) or ""
        if reason == "content_filter":
            msg = f"{self.label} refused to answer ({reason})"
            raise LLMRefusedError(msg)
        if choice is None or not choice.message.content:
            msg = f"{self.label} gave no answer" + (f" ({reason})" if reason else "")
            raise LLMOutputError(msg)
        return choice.message.content

    def _mapped(self, response: httpx.Response) -> LLMError:
        code = response.status_code
        message, error_code, generated = _error(response)
        detail = f"{code} {error_code}: {message}" + (
            f"\ngenerated: {generated[:2000]}" if generated else ""
        )
        if error_code == _BAD_JSON:
            return LLMOutputError(f"{self.label} answered in the wrong shape", detail)
        if code in _AUTH:
            return LLMAuthError(self.label, detail)
        if code == HTTPStatus.NOT_FOUND:
            return LLMConfigError(f"{self.label}: {message}", detail)
        if code == HTTPStatus.TOO_MANY_REQUESTS:
            return LLMUnavailableError(
                f"{self.label}: rate limited or out of free quota",
                detail,
                retry_after_s=_seconds(response.headers.get("retry-after")),
            )
        if code in _TRANSIENT or code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return LLMUnavailableError(f"{self.label} is unavailable ({code})", detail)
        return LLMInputError(f"{self.label} rejected the request: {message}", detail)


def _error(response: httpx.Response) -> tuple[str, str, str]:
    """The vendor's `{"error": {"message", "code", "failed_generation"}}`;
    the body as the message when it is not that."""
    try:
        body = response.json()
    except ValueError:
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if not (isinstance(error, dict) and error.get("message")):
        return response.text[:500], "", ""
    return (
        str(error["message"]),
        str(error.get("code") or ""),
        str(error.get("failed_generation") or ""),
    )


def _seconds(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None
