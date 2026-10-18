"""Higgsfield's request-status body, as Pydantic models.

`GET /requests/{id}/status` (docs: concepts/requests) carries the status and,
once completed, the outputs: images as `images[].url`, a video as
`video.url`, audio as `audio.url` / `audios[].url`. Unknown fields are
ignored, so new output types never break a parse.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict

HiggsfieldStatus = Literal[
    "queued", "in_progress", "completed", "failed", "nsfw", "canceled"
]
PENDING: frozenset[HiggsfieldStatus] = frozenset({"queued", "in_progress"})


class _Lenient(BaseModel):
    model_config = ConfigDict(extra="ignore")


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
