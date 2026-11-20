from typing import Any, Literal

from pydantic import Field

from ofmhelpers.integrations.ai_providers.http import Lenient

HiggsfieldStatus = Literal[
    "queued", "in_progress", "completed", "failed", "nsfw", "canceled"
]


class HiggsfieldSubmitted(Lenient):
    request_id: str


class HiggsfieldRefusalDetail(Lenient):
    retry_after_seconds: float | None = Field(default=None, ge=0)


class HiggsfieldRefusal(Lenient):
    detail: HiggsfieldRefusalDetail | str | list[Any] | None = None


class HiggsfieldMedia(Lenient):
    url: str


class HiggsfieldRequest(Lenient):
    status: HiggsfieldStatus
    request_id: str | None = None
    error: str | None = None
    images: list[HiggsfieldMedia] = Field(default_factory=list)
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

    @property
    def out_of_credits(self) -> bool:
        return (
            self.status == "failed" and "credit balance" in (self.error or "").lower()
        )
