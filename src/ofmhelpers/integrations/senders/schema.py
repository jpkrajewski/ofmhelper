# Copyright (C) 2026 jpkrajewski

import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_TITLE = 256
MAX_TEXT = 3000
_CUT = "…"


def _cut(value: str, size: int) -> str:
    return value if len(value) <= size else value[: size - 1] + _CUT


class Event(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1)
    text: str = ""
    url: str | None = None
    key: str | None = Field(default=None, min_length=1, max_length=200)
    replaces: str | None = Field(default=None, min_length=1, max_length=64)
    at: float = Field(default_factory=time.time)
    owner_id: int | None = None

    @field_validator("title")
    @classmethod
    def _short_title(cls, value: str) -> str:
        return _cut(value, MAX_TITLE)

    @field_validator("text")
    @classmethod
    def _short_text(cls, value: str) -> str:
        return _cut(value, MAX_TEXT)

    @field_validator("url")
    @classmethod
    def _a_page_or_a_web_address(cls, value: str | None) -> str | None:
        if not value:
            return None
        if value.startswith(("/", "https://", "http://")):
            return value
        msg = "url must be a site path (/links) or an http(s) address"
        raise ValueError(msg)
