"""Elyum (elyum.ai) behind the shared `Provider` interface: one endpoint for
every model, `POST /v1/generations {model, prompt, inputs, ...settings}`.

The catalog keeps the form's usual shape (`image_urls`, `video_urls` as
media fields, the settings by Elyum's own names); `wire_body` turns the
media fields into Elyum's `inputs` -- each `{"role", "file"}` (an upload's
`file_...` id) or `{"role", "url"}` -- with the role from the field's
`x-role` (default `reference`)."""

import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, model_serializer

from ofmhelpers.config import settings
from ofmhelpers.integrations.ai_providers.base import (
    Provider,
    TaskState,
    register_provider,
)
from ofmhelpers.integrations.ai_providers.elyum.client import ElyumClient
from ofmhelpers.integrations.ai_providers.elyum.models import (
    FAILED,
    PENDING,
    PROVIDER,
    SUCCEEDED,
)
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

# Our schema keyword: the input role a media field's files go in as.
ROLE = "x-role"
DEFAULT_ROLE = "reference"
FILE_ID_PREFIX = "file_"
# Elyum numbers inputs @image1, @video1, @audio1; our prompts (the /clone
# default "... @Video1 ... @Image1") use the other providers' capitals.
_CITE = re.compile(r"@(Image|Video|Audio)(\d+)")


@register_provider
class ElyumProvider(Provider):
    name = "elyum"
    label = PROVIDER
    rank = 30
    # Idempotency-Key = our job key: a resend answers the generation made.
    idempotent_submit = True

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self.client = ElyumClient(api_key)

    @classmethod
    def default_key(cls) -> str | None:
        return settings.elyum.api_key_admin

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        return self.client.upload(path, max_bytes)

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        body = wire_body(spec, payload).model_dump()
        return self.client.create(body, idempotency_key).id

    def status(self, task_id: str) -> TaskState:
        generation = self.client.get(task_id)
        if generation.status == SUCCEEDED:
            if not generation.urls:
                msg = f"{PROVIDER}: generation {task_id} succeeded without a result"
                raise ProviderUnavailableError(msg)
            return TaskState(
                state="done",
                urls=generation.urls,
                cost_usd=generation.usage.usd if generation.usage else None,
            )
        if generation.status in PENDING:
            return TaskState(state="pending")
        if generation.status in FAILED:
            return TaskState(state="failed", error=generation.failure)
        # No verdict: failing here would forget a generation that may be paid
        # for. Asked again until the deadline, then recovery takes over.
        msg = f"{PROVIDER}: generation {task_id} answered status {generation.status!r}"
        raise ProviderUnavailableError(msg)


class Input(BaseModel):
    """One of `inputs`: an upload's id (`file`) or a link (`url`), and its role."""

    role: str
    file: str | None = None
    url: str | None = None


class Body(BaseModel):
    """`POST /v1/generations`: `settings` go beside `model` by the names and
    in the order given, `inputs` last and only when there are any."""

    model: str
    settings: dict[str, Any]
    inputs: list[Input]

    @model_serializer
    def _wire(self) -> dict[str, Any]:
        body = {"model": self.model, **self.settings}
        if self.inputs:
            body["inputs"] = [i.model_dump(exclude_none=True) for i in self.inputs]
        return body


def _input(role: str, value: str) -> Input:
    if value.startswith(FILE_ID_PREFIX):
        return Input(role=role, file=value)
    return Input(role=role, url=value)


def wire_body(spec: ModelSpec, payload: Payload) -> Body:
    """What Elyum is sent: the model id, every setting as given, and each
    media field's files as `inputs` in field order (Elyum numbers them per
    kind in the order sent)."""
    raw = spec.input_schema.get("properties")
    schema = raw if isinstance(raw, dict) else {}
    media = {f.name for f in spec.media_fields}
    given: dict[str, Any] = {}
    inputs: list[Input] = []
    for name, value in payload.items():
        if name not in media:
            given[name] = value
            continue
        field = schema.get(name)
        role = (
            str(field.get(ROLE, DEFAULT_ROLE))
            if isinstance(field, dict)
            else DEFAULT_ROLE
        )
        values = value if isinstance(value, list) else [value]
        inputs += [_input(role, str(v)) for v in values if v]
    if isinstance(given.get("prompt"), str):
        given["prompt"] = _CITE.sub(
            lambda m: f"@{m.group(1).lower()}{m.group(2)}", given["prompt"]
        )
    return Body(model=spec.model_id, settings=given, inputs=inputs)
