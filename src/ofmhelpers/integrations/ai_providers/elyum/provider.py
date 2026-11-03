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
from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

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

    def __init__(self, api_key: str) -> None:
        super().__init__(api_key)
        self.client = ElyumClient(api_key)

    @classmethod
    def default_key(cls) -> str | None:
        return settings.elyum.api_key_admin

    def upload(self, path: Path, max_bytes: int | None = None) -> str:
        return self.client.upload(path, max_bytes)

    def submit(self, spec: ModelSpec, payload: Payload, idempotency_key: str) -> str:
        return self.client.create(wire_body(spec, payload), idempotency_key).id

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


def _source(value: str) -> dict[str, str]:
    key = "file" if value.startswith(FILE_ID_PREFIX) else "url"
    return {key: value}


def wire_body(spec: ModelSpec, payload: Payload) -> dict[str, Any]:
    """What Elyum is sent: the model id, every setting as given, and each
    media field's files as `inputs` in field order (Elyum numbers them per
    kind in the order sent)."""
    raw = spec.input_schema.get("properties")
    schema = raw if isinstance(raw, dict) else {}
    media = {f.name for f in spec.media_fields}
    body: dict[str, Any] = {"model": spec.model_id}
    inputs: list[dict[str, str]] = []
    for name, value in payload.items():
        if name not in media:
            body[name] = value
            continue
        field = schema.get(name)
        role = (
            str(field.get(ROLE, DEFAULT_ROLE))
            if isinstance(field, dict)
            else DEFAULT_ROLE
        )
        values = value if isinstance(value, list) else [value]
        inputs += [{"role": role, **_source(str(v))} for v in values if v]
    if inputs:
        body["inputs"] = inputs
    if isinstance(body.get("prompt"), str):
        body["prompt"] = _CITE.sub(
            lambda m: f"@{m.group(1).lower()}{m.group(2)}", body["prompt"]
        )
    return body
