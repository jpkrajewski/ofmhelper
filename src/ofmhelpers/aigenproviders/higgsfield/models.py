"""Higgsfield's answers, as Pydantic models.

`GET /requests/{id}/status` (docs: concepts/requests) carries the status and,
once completed, the outputs: images as `images[].url`, a video as
`video.url`, audio as `audio.url` / `audios[].url`. Unknown fields are
ignored, so new output types never break a parse. A submit answers with its
request id; an error with FastAPI's `{"detail": ...}`.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

HiggsfieldStatus = Literal[
    "queued", "in_progress", "completed", "failed", "nsfw", "canceled"
]
PENDING: frozenset[HiggsfieldStatus] = frozenset({"queued", "in_progress"})


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


class HiggsfieldSubmitted(_Lenient):
    """A submit's receipt: the request it made, or for an Idempotency-Key
    sent again the one it made before (docs: concepts/idempotency)."""

    request_id: str


class HiggsfieldRefusalDetail(_Lenient):
    """`detail` as an object: a full queue's 429 (docs: concepts/rate-limits)."""

    retry_after_seconds: float | None = Field(default=None, ge=0)


class HiggsfieldRefusal(_Lenient):
    """An error body: `detail` is a sentence, a validation list, or an object
    (docs: concepts/errors)."""

    detail: HiggsfieldRefusalDetail | str | list[Any] | None = None


class HiggsfieldMedia(_Lenient):
    url: str


class HiggsfieldRequest(_Lenient):
    status: HiggsfieldStatus
    request_id: str | None = None
    error: str | None = None
    images: list[HiggsfieldMedia] = []
    video: HiggsfieldMedia | None = None
    audio: HiggsfieldMedia | None = None

    @property
    def urls(self) -> list[str]:
        media = [*self.images, self.video, self.audio]
        return [m.url for m in media if m is not None]

    @property
    def failure(self) -> str:
        if self.status == "nsfw":
            return "rejected by content moderation"
        if self.status == "canceled":
            return "canceled"
        return self.error or "generation failed"
