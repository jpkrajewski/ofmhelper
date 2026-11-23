# Copyright (C) 2026 jpkrajewski

from pydantic import BaseModel, Field, computed_field

from ofmhelpers.features.clone_links.links import (
    BadLine,
)


class CheckRefused(BaseModel):
    message: str
    lines: list[BadLine] = []


class CheckedLinks(BaseModel):
    links: list[str]


class RecreateLinks(BaseModel):
    links: list[str]


class PreviewsRequest(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=500)
    provider: str = ""
    resolution: str = ""


class LinkPreview(BaseModel):
    url: str
    seconds: float | None = None
    thumb_url: str | None = None
    max_s: int
    too_long: bool = False
    credits: float = 0.0
    usd: float = 0.0
    error: str | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def ready(self) -> bool:
        return self.error is None and self.seconds is not None and not self.too_long
