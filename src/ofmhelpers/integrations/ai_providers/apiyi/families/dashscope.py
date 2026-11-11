"""Alibaba's video models over the DashScope API (docs.apiyi.com wan3 pages):
Wan 3.0 (`wan3.0-video`, `wan3.0-video-prime`).

- Submit `POST /wan/api/v1/services/aigc/video-generation/video-synthesis`
  with `X-DashScope-Async: enable` (never `/v1/videos`: it drops `media` and
  bills high) -> `output.task_id`; read `GET /v1/tasks/{id}` (a different
  prefix): submitted/in_progress/completed (`result_url`)/failed.
- The catalog keeps the form's usual shape (`reference_image_urls`,
  `first_frame_url` ... as media fields, `aspect_ratio`, `resolution`);
  `wire_body` turns it into DashScope's nested body,
  `{model, input: {prompt, negative_prompt, media: [{type, url}]}, parameters}`,
  each media field's references typed by its `x-role` in field order (Wan
  numbers them per kind in the order sent: "Image 1", "Video 1"). A
  reference goes as a link to this server (the upstream fetches it).
"""

import re
from typing import Any

from pydantic import BaseModel, model_serializer

from ofmhelpers.integrations.ai_providers.apiyi.client import (
    ApiyiClient,
    refused_read,
)
from ofmhelpers.integrations.ai_providers.apiyi.families import Family
from ofmhelpers.integrations.ai_providers.apiyi.models import (
    COMPLETED,
    FAILED,
    PENDING,
    PROVIDER,
    ApiyiSubmit,
    ApiyiTask,
)
from ofmhelpers.integrations.ai_providers.base import TaskState
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.http import parsed
from ofmhelpers.integrations.ai_providers.spec import Fields
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

logger = get_logger(__name__)

SUBMIT_PATH = "/wan/api/v1/services/aigc/video-generation/video-synthesis"
TASKS_PATH = "/v1/tasks"
ASYNC_HEADER = {"X-DashScope-Async": "enable"}
# Our schema keyword: the DashScope media `type` a media field's links go as.
ROLE = "x-role"
DEFAULT_ROLE = "reference_image"
# What goes in `input` (the rest of the body is `parameters`).
INPUT_FIELDS = frozenset({"prompt", "negative_prompt"})
# Our names for DashScope's `parameters` keys.
PARAMETER_NAMES = {"aspect_ratio": "ratio"}
# Our prompts (the /clone default "... @Video1 ... @Image1") cite inputs the
# other providers' way; Wan 3.0 reads "Video 1", "Image 1".
_CITE = re.compile(r"@(Image|Video|Audio)(\d+)")


def cited(prompt: str) -> str:
    return _CITE.sub(lambda m: f"{m.group(1)} {m.group(2)}", prompt)


class Media(BaseModel):
    type: str
    url: str


class Body(BaseModel):
    """DashScope's nested body. `prompts` (what goes in `input`) and
    `parameters` keep the names and order the catalog's schema gives them."""

    model: str
    prompts: dict[str, Any]
    media: list[Media]
    parameters: dict[str, Any]

    @model_serializer
    def _wire(self) -> dict[str, Any]:
        inputs = dict(self.prompts)
        if self.media:
            inputs["media"] = [m.model_dump() for m in self.media]
        return {"model": self.model, "input": inputs, "parameters": self.parameters}


def with_defaults(spec: ModelSpec, payload: Payload) -> Fields:
    """`payload` plus every schema default it left out (`resolution` sets the
    price: never left to the upstream's own), by field name."""
    raw = spec.input_schema.get("properties")
    schema = raw if isinstance(raw, dict) else {}
    values: dict[str, Any] = {
        name: field["default"]
        for name, field in schema.items()
        if isinstance(field, dict) and "default" in field
    }
    values.update(payload)
    return Fields(values=values)


def wire_body(spec: ModelSpec, payload: Payload, link: Any = str) -> Body:
    """What APIYI is sent: DashScope's nested body; `link` turns a reference
    into the URL the upstream fetches."""
    raw = spec.input_schema.get("properties")
    schema = raw if isinstance(raw, dict) else {}
    media_fields = {f.name for f in spec.media_fields}
    prompts: dict[str, Any] = {}
    parameters: dict[str, Any] = {}
    media: list[Media] = []
    for name, value in with_defaults(spec, payload).values.items():
        if name in media_fields:
            field = schema.get(name)
            role = (
                str(field.get(ROLE, DEFAULT_ROLE))
                if isinstance(field, dict)
                else DEFAULT_ROLE
            )
            refs = value if isinstance(value, list) else [value]
            media += [Media(type=role, url=link(str(v))) for v in refs if v]
        elif name in INPUT_FIELDS:
            prompts[name] = cited(value) if isinstance(value, str) else value
        else:
            parameters[PARAMETER_NAMES.get(name, name)] = value
    return Body(
        model=spec.model_id, prompts=prompts, media=media, parameters=parameters
    )


class DashScope(Family):
    tag = ""
    prefixes = ("wan3", "wan2.7", "happyhorse")

    def submit(
        self, client: ApiyiClient, spec: ModelSpec, payload: Payload, job: str
    ) -> str:
        body = wire_body(spec, payload, client.link)
        response = client.submit(
            SUBMIT_PATH, json=body.model_dump(), headers=ASYNC_HEADER
        )
        task_id = parsed(response, ApiyiSubmit, PROVIDER).task_id
        if not task_id:
            # A 200 without an id we can read: the task is likely made. Its
            # whole answer (no key in it) is the only way back to it.
            logger.error(
                "apiyi %s answered 200 without a task id (job %s): %s",
                spec.model_id,
                job,
                response.text[:2000],
            )
            msg = f"{PROVIDER}: {spec.model_id} answered without a task id"
            raise ProviderUnavailableError(msg)
        logger.info(
            "apiyi %s task %s %s (job %s)",
            spec.model_id,
            task_id,
            body.parameters,
            job,
        )
        return task_id

    def status(self, client: ApiyiClient, task_id: str) -> TaskState:
        response = client.call("GET", f"{TASKS_PATH}/{task_id}", refused_read)
        task = parsed(response, ApiyiTask, PROVIDER)
        if task.state in COMPLETED:
            if not task.urls:
                msg = f"{PROVIDER}: task {task_id} completed without a result"
                raise ProviderUnavailableError(msg)
            logger.info("apiyi task %s %s", task_id, task.state)
            return TaskState(state="done", urls=task.urls)
        if task.state in PENDING:
            return TaskState(state="pending")
        if task.state in FAILED:
            # Docs: a failed task is fully refunded.
            logger.info("apiyi task %s %s: %s", task_id, task.state, task.failure)
            return TaskState(state="failed", error=task.failure)
        # No verdict: failing here would forget a task that may be paid for.
        # Asked again until the deadline, then recovery takes over.
        msg = f"{PROVIDER}: task {task_id} answered status {task.state!r}"
        raise ProviderUnavailableError(msg)
