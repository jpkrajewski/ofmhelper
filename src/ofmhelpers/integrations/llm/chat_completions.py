# Copyright (C) 2026 jpkrajewski

import base64
import copy
import json
import tempfile
from http import HTTPStatus
from pathlib import Path
from typing import Any, ClassVar

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.integrations.llm.base import LLMProvider
from ofmhelpers.integrations.llm.errors import (
    LLMAuthError,
    LLMConfigError,
    LLMError,
    LLMInputError,
    LLMOutputError,
    LLMQuotaError,
    LLMRefusedError,
    LLMUnavailableError,
)
from ofmhelpers.integrations.llm.models import (
    MediaKind,
    MediaPart,
    Reply,
    TextPart,
    Usage,
)
from ofmhelpers.media.image_fit import ImageLimits, fitted
lazy from ofmhelpers.integrations.llm.models import Prompt

_AUTH = (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN)
_JSON_ONLY = "Reply with one JSON object that follows this JSON Schema, nothing else:"
_BAD_JSON = "json_validate_failed"
_TRANSIENT = (HTTPStatus.REQUEST_TIMEOUT, HTTPStatus.CONFLICT)
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
    },
)


class StrictSchema(BaseModel):
    name: str
    strict: bool = True
    json_schema: dict[str, Any] = Field(serialization_alias="schema")


def strict_schema(answer: type[BaseModel]) -> StrictSchema:
    schema = answer.model_json_schema()
    defs = schema.get("$defs", {})

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if not isinstance(node, dict):
            return
        if "$ref" in node:
            target = copy.deepcopy(defs[str(node["$ref"]).rsplit("/", 1)[-1]])
            node.clear()
            node.update(target)
            walk(node)
            return
        for key in [key for key in node if key in _NOT_STRICT]:
            del node[key]
        for key, value in node.items():
            if key == "properties":
                for sub in value.values():
                    walk(sub)
            else:
                walk(value)
        if node.get("type") == "object":
            node["required"] = list(node.get("properties", {}))
            node["additionalProperties"] = False

    walk(schema)
    return StrictSchema(name=answer.__name__, json_schema=schema)


class TextContent(BaseModel):
    type: str = "text"
    text: str


class ImageUrl(BaseModel):
    url: str


class ImageContent(BaseModel):
    type: str = "image_url"
    image_url: ImageUrl


class ChatMessage(BaseModel):
    role: str
    content: str | list[TextContent | ImageContent]


class ErrorRead(BaseModel):
    message: str
    code: str = ""
    generated: str = ""


class _Message(BaseModel):
    model_config = ConfigDict(extra="ignore")
    content: str | None = None


class _Choice(BaseModel):
    model_config = ConfigDict(extra="ignore")
    message: _Message
    finish_reason: str | None = None


class _Usage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


class _Completion(BaseModel):
    model_config = ConfigDict(extra="ignore")
    choices: list[_Choice] = []
    usage: _Usage | None = None


class ChatCompletionsLLM(LLMProvider):
    base_url: ClassVar[str]
    media = frozenset({MediaKind.IMAGE})
    temperature: ClassVar[float | None] = 0.0
    extra_body: ClassVar[dict[str, Any]] = {}

    @classmethod
    def fake_transport(cls) -> httpx.BaseTransport:
        raise NotImplementedError

    def _client(self, timeout_s: float) -> httpx.Client:
        transport = self.fake_transport() if settings.infra.fake_externals else None
        return httpx.Client(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout_s,
            transport=transport,
        )

    def _ask(self, prompt: Prompt, answer: type[BaseModel], timeout_s: float) -> Reply:
        schema = strict_schema(answer)
        temperature = self._temperature(prompt)
        with tempfile.TemporaryDirectory(prefix="llm-img-") as work:
            body = {
                "model": self.model,
                "messages": [
                    message.model_dump()
                    for message in self._messages(
                        prompt,
                        schema.json_schema,
                        Path(work),
                    )
                ],
                **({} if temperature is None else {"temperature": temperature}),
                "response_format": {
                    "type": "json_schema",
                    "json_schema": schema.model_dump(by_alias=True),
                },
                **self.extra_body,
            }
        try:
            with self._client(timeout_s) as client:
                response = client.post("/chat/completions", json=body)
        except httpx.HTTPError as exc:
            msg = f"{self.label}: {type(exc).__name__}"
            raise LLMUnavailableError(msg, detail=str(exc)) from exc
        if response.is_error:
            raise self._mapped(response)
        return self._reply(response)

    def _messages(
        self,
        prompt: Prompt,
        schema: dict[str, Any],
        work: Path,
    ) -> list[ChatMessage]:
        content = [self._part(p, work) for p in prompt.parts]
        shape = f"{_JSON_ONLY}\n{json.dumps(schema, separators=(',', ':'))}"
        system = "\n\n".join(filter(None, (prompt.system, shape)))
        return [
            ChatMessage(role="system", content=system),
            ChatMessage(role="user", content=content),
        ]

    def _part(
        self,
        part: TextPart | MediaPart,
        work: Path,
    ) -> TextContent | ImageContent:
        if isinstance(part, TextPart):
            return TextContent(text=part.text)
        s = settings.llm
        limits = ImageLimits(max_bytes=s.image_max_bytes, max_side=s.image_max_side)
        try:
            image = fitted(part.path, work, limits)
            data = base64.b64encode(image.read_bytes()).decode()
        except OSError as exc:
            msg = f"Could not read {part.path.name}"
            raise LLMInputError(msg, detail=str(exc)) from exc
        mime = "image/jpeg" if image != part.path else part.mime_type
        return ImageContent(image_url=ImageUrl(url=f"data:{mime};base64,{data}"))

    def _reply(self, response: httpx.Response) -> Reply:
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
        used = found.usage or _Usage()
        usage = Usage(
            input_tokens=used.prompt_tokens,
            output_tokens=used.completion_tokens,
        )
        return Reply(text=choice.message.content, usage=usage)

    def _mapped(self, response: httpx.Response) -> LLMError:
        code = response.status_code
        read = _error(response)
        message, error_code, generated = read.message, read.code, read.generated
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
            return LLMQuotaError(
                f"{self.label}: rate limited or out of free quota",
                detail,
                retry_after_s=_seconds(response.headers.get("retry-after")),
            )
        if code in _TRANSIENT or code >= HTTPStatus.INTERNAL_SERVER_ERROR:
            return LLMUnavailableError(f"{self.label} is unavailable ({code})", detail)
        return LLMInputError(f"{self.label} rejected the request: {message}", detail)


def _error(response: httpx.Response) -> ErrorRead:
    try:
        body = response.json()
    except ValueError:  # silent: not JSON: the body is the message
        body = None
    error = body.get("error") if isinstance(body, dict) else None
    if not (isinstance(error, dict) and error.get("message")):
        return ErrorRead(message=response.text[:500])
    return ErrorRead(
        message=str(error["message"]),
        code=str(error.get("code") or ""),
        generated=str(error.get("failed_generation") or ""),
    )


def _seconds(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:  # silent: not a number
        return None
