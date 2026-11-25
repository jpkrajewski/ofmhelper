# Copyright (C) 2026 jpkrajewski

import logging
import time
lazy from pathlib import Path

from pydantic import BaseModel, Field, JsonValue

from ofmhelpers.config import settings
from ofmhelpers.config.types import ImageLimits
from ofmhelpers.integrations.ai_providers.apiyi.client import (
    ApiyiClient,
    refused_read,
)
from ofmhelpers.integrations.ai_providers.apiyi.families import Family
from ofmhelpers.integrations.ai_providers.apiyi.families.dashscope import with_defaults
from ofmhelpers.integrations.ai_providers.apiyi.models import PROVIDER, Text
from ofmhelpers.integrations.ai_providers.base import TaskState
from ofmhelpers.integrations.ai_providers.errors import (
    ProviderError,
    ProviderUnavailableError,
)
from ofmhelpers.integrations.ai_providers.http import Lenient, parsed
lazy from ofmhelpers.integrations.ai_providers.spec import ModelSpec, Payload

logger = logging.getLogger(__name__)

VIDEOS = "/v1/videos"
CONTENT = "/content"
PENDING = frozenset({"queued", "in_progress", "processing", "submitted"})
COMPLETED = "completed"
FAILED = frozenset({"failed", "cancelled", "canceled", "expired"})
_SIZES = {
    ("720p", "16:9"): "1280x720",
    ("720p", "9:16"): "720x1280",
    ("1080p", "16:9"): "1920x1080",
    ("1080p", "9:16"): "1080x1920",
    ("4k", "16:9"): "3840x2160",
    ("4k", "9:16"): "2160x3840",
}
_IMAGE = ImageLimits(max_bytes=10 * 1000 * 1000, max_side=4096)
_CONTENT_TRIES = 8
_CONTENT_WAIT_S = 4


class VeoError(Lenient):
    message: Text = ""
    code: Text = ""


class VeoTask(Lenient):
    id: Text = Field(default="")
    task_id: Text = ""
    status: Text = ""
    video_url: str | None = None
    error: VeoError | str | None = None

    @property
    def own_id(self) -> str:
        return self.task_id or self.id

    @property
    def failure(self) -> str:
        if isinstance(self.error, VeoError):
            reason = self.error.message or self.error.code
        else:
            reason = self.error or ""
        reason = reason or f"task {self.status}"
        if "PUBLIC_" in reason or "safety" in reason.lower():
            return f"{PROVIDER}: flagged by moderation: {reason}"
        return f"{PROVIDER}: {reason}"


class Metadata(BaseModel):
    resolution: str
    aspect_ratio: str = Field(serialization_alias="aspectRatio")
    seed: JsonValue = None
    negative_prompt: JsonValue = Field(None, serialization_alias="negativePrompt")


class Body(BaseModel):
    model: str
    prompt: str
    seconds: str
    size: str
    metadata: Metadata


def fields(spec: ModelSpec, payload: Payload) -> Body:
    values = with_defaults(spec, payload).values
    resolution = str(values.get("resolution", "720p"))
    ratio = str(values.get("aspect_ratio", "16:9"))
    return Body(
        model=spec.model_id,
        prompt=str(values.get("prompt", "")),
        seconds=str(values.get("duration", 8)),
        size=_SIZES.get((resolution, ratio), "1280x720"),
        metadata=Metadata(
            resolution=resolution,
            aspect_ratio=ratio,
            seed=values.get("seed"),
            negative_prompt=values.get("negative_prompt") or None,
        ),
    )


class Veo(Family):
    tag = "veo"
    prefixes = ("veo",)

    def submit(
        self,
        client: ApiyiClient,
        spec: ModelSpec,
        payload: Payload,
        job: str,
    ) -> str:
        body = fields(spec, payload).model_dump(by_alias=True, exclude_none=True)
        frame = payload.get("first_frame_url")
        if frame:
            image = client.image_file(str(frame), _IMAGE)
            flat = {k: v for k, v in body.items() if k != "metadata"}
            flat.update(
                {k: str(v) for k, v in body["metadata"].items() if v is not None},
            )
            with image.open("rb") as handle:
                response = client.submit(
                    VIDEOS,
                    data={k: str(v) for k, v in flat.items()},
                    files={"input_reference": (image.name, handle)},
                )
        else:
            response = client.submit(VIDEOS, json=body)
        task_id = parsed(response, VeoTask, PROVIDER).own_id
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
        response = client.call("GET", f"{VIDEOS}/{task_id}", refused_read)
        task = parsed(response, VeoTask, PROVIDER)
        state = task.status.lower()
        if state == COMPLETED:
            url = task.video_url or f"{_base()}{VIDEOS}/{task_id}{CONTENT}"
            return TaskState(state="done", urls=[url])
        if state in PENDING:
            return TaskState(state="pending")
        if state in FAILED:
            logger.info("apiyi veo %s %s: %s", task_id, state, task.failure)
            return TaskState(state="failed", error=task.failure)
        msg = f"{PROVIDER}: task {task_id} answered status {task.status!r}"
        raise ProviderUnavailableError(msg)

    def download(self, client: ApiyiClient, url: str, dest: Path) -> Path | None:
        if not (url.startswith(f"{_base()}{VIDEOS}/") and url.endswith(CONTENT)):
            return None
        path = url.removeprefix(_base())
        for attempt in range(_CONTENT_TRIES):
            try:
                response = client.call(
                    "GET",
                    path,
                    refused_read,
                    timeout=settings.aigen.download_timeout_s,
                )
            except ProviderError:
                if attempt == _CONTENT_TRIES - 1:
                    raise
                time.sleep(_CONTENT_WAIT_S)
                continue
            dest.write_bytes(response.content)
            return dest
        return None


def _base() -> str:
    return settings.apiyi.base_url.rstrip("/")
