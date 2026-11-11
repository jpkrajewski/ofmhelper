"""Seedance 2.0 / 2.5 over Volcengine Ark's task API (docs.apiyi.com
seedance2 pages, 2026-11-06), on a SeeDance2-group key.

- Submit `POST /seedance/api/v3/contents/generations/tasks` -> top-level `id`
  ("cgt-..."); read `GET .../tasks/{id}`: queued/running (pending),
  succeeded (`content.video_url`: signed TOS link, 24 h, fetched without our
  key), failed/expired. Every call sends `Accept-Encoding: identity`: the
  gateway labels plain bodies gzip (truncated JSON, random 400s otherwise).
- Body `{model, content: [...], resolution, ratio, duration,
  generate_audio, watermark: false, seed}`: one `text` item, then the media
  items in field order with their `role` (`x-role`): first_frame,
  last_frame, reference_image, reference_video, reference_audio. Images,
  videos and audio go as links to this server.
- Realistic faces are refused as references (anti-deepfake: 403) unless
  ingested in APIYI's asset library (`asset://`); not wired here.
- 2.5: a first-frame task takes only `ratio: adaptive` (sent so).
"""

import re
from typing import Any

from pydantic import BaseModel, Field, model_serializer

from ofmhelpers.integrations.ai_providers.apiyi.client import (
    ApiyiClient,
    refused_moderated,
    refused_read,
)
from ofmhelpers.integrations.ai_providers.apiyi.families import Family
from ofmhelpers.integrations.ai_providers.apiyi.families.dashscope import with_defaults
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER, Text
from ofmhelpers.integrations.ai_providers.base import TaskState
from ofmhelpers.integrations.ai_providers.errors import ProviderUnavailableError
from ofmhelpers.integrations.ai_providers.http import Lenient, parsed
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

logger = get_logger(__name__)

TASKS = "/seedance/api/v3/contents/generations/tasks"
HEADERS = frozendict({"Accept-Encoding": "identity"})
ROLE = "x-role"
PENDING = frozenset({"queued", "running"})
SUCCEEDED = "succeeded"
FAILED = frozenset({"failed", "expired", "cancelled", "canceled"})
_KIND = {
    "first_frame": "image_url",
    "last_frame": "image_url",
    "reference_image": "image_url",
    "reference_video": "video_url",
    "reference_audio": "audio_url",
}
# Our prompts cite "@Image1"/"@Video1"; Seedance's examples "@图像1"/"@视频1".
_CITE = re.compile(r"@(Image|Video|Audio)(\d+)")
_CITE_AS = {"Image": "图像", "Video": "视频", "Audio": "音频"}
# Moderation, as Ark words it in a failed task's code.
_REFUSED = re.compile(r"sensitive|moderat|risk|safety|PUBLIC_", re.IGNORECASE)


def _cited(prompt: str) -> str:
    return _CITE.sub(lambda m: f"@{_CITE_AS[m.group(1)]}{m.group(2)}", prompt)


class SeedanceSubmit(Lenient):
    id: Text = ""


class SeedanceContent(Lenient):
    video_url: str | None = None


class SeedanceError(Lenient):
    code: Text = ""
    message: Text = ""


class SeedanceTask(Lenient):
    id: Text = ""
    status: Text = ""
    content: SeedanceContent | None = None
    error: SeedanceError | None = None
    usage: dict[str, Any] = Field(default_factory=dict)

    @property
    def failure(self) -> str:
        error = self.error or SeedanceError()
        reason = error.message or error.code or f"task {self.status}"
        if _REFUSED.search(f"{error.code} {error.message}"):
            return f"{PROVIDER}: flagged by moderation: {reason} ({error.code})"
        return f"{PROVIDER}: {reason}" + (f" ({error.code})" if error.code else "")


class Link(BaseModel):
    url: str


class Item(BaseModel):
    """One `content` entry: the text, or a reference under its kind's key."""

    type: str
    text: str | None = None
    image_url: Link | None = None
    video_url: Link | None = None
    audio_url: Link | None = None
    role: str | None = None


class Body(BaseModel):
    """Ark's task body: `settings` go beside `model` and `content`, by the
    names and in the order the catalog's schema gives them."""

    model: str
    content: list[Item]
    settings: dict[str, Any]

    @model_serializer
    def _wire(self) -> dict[str, Any]:
        content = [item.model_dump(exclude_none=True) for item in self.content]
        return {"model": self.model, "content": content, **self.settings}


def wire_body(spec: ModelSpec, payload: Payload, link: Any) -> Body:
    raw = spec.input_schema.get("properties")
    schema = raw if isinstance(raw, dict) else {}
    media_fields = {f.name for f in spec.media_fields}
    values = dict(with_defaults(spec, payload).values)
    content = [Item(type="text", text=_cited(str(values.pop("prompt", ""))))]
    first_frame = False
    for name in list(values):
        if name not in media_fields:
            continue
        value = values.pop(name)
        field = schema.get(name)
        role = (
            str(field.get(ROLE, "reference_image")) if isinstance(field, dict) else ""
        )
        kind = _KIND.get(role, "image_url")
        first_frame |= role == "first_frame"
        refs = value if isinstance(value, list) else [value]
        content.extend(
            Item.model_validate(
                {"type": kind, kind: {"url": link(str(ref))}, "role": role}
            )
            for ref in refs
            if ref
        )
    if "aspect_ratio" in values:
        values["ratio"] = values.pop("aspect_ratio")
    if first_frame and spec.model_id.startswith("doubao-seedance-2-5"):
        values["ratio"] = "adaptive"
    return Body(model=spec.model_id, content=content, settings=values)


class Seedance(Family):
    tag = "seedance"
    prefixes = ("doubao-seedance", "seedance")

    def submit(
        self, client: ApiyiClient, spec: ModelSpec, payload: Payload, job: str
    ) -> str:
        body = wire_body(spec, payload, client.link)
        response = client.submit(
            TASKS, json=body.model_dump(), headers=HEADERS, refuse=refused_moderated
        )
        task_id = parsed(response, SeedanceSubmit, PROVIDER).id
        if not task_id:
            logger.error(
                "apiyi %s answered 200 without a task id (job %s): %s",
                spec.model_id,
                job,
                response.text[:2000],
            )
            msg = f"{PROVIDER}: {spec.model_id} answered without a task id"
            raise ProviderUnavailableError(msg)
        logger.info("apiyi %s task %s (job %s)", spec.model_id, task_id, job)
        return task_id

    def status(self, client: ApiyiClient, task_id: str) -> TaskState:
        response = client.call(
            "GET", f"{TASKS}/{task_id}", refused_read, headers=HEADERS
        )
        task = parsed(response, SeedanceTask, PROVIDER)
        state = task.status.lower()
        if state == SUCCEEDED:
            url = task.content.video_url if task.content else None
            if not url:
                msg = f"{PROVIDER}: task {task_id} succeeded without a result"
                raise ProviderUnavailableError(msg)
            logger.info("apiyi seedance %s succeeded: %s", task_id, task.usage)
            return TaskState(state="done", urls=[url])
        if state in PENDING:
            return TaskState(state="pending")
        if state in FAILED:
            logger.info("apiyi seedance %s %s: %s", task_id, state, task.failure)
            return TaskState(state="failed", error=task.failure)
        msg = f"{PROVIDER}: task {task_id} answered status {task.status!r}"
        raise ProviderUnavailableError(msg)
