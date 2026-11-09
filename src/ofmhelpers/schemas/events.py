"""Something that happened, as every sender gets it (features/events,
integrations/senders): any producer, any meaning. The producer words it; a sender
only lays it out for its service."""

import time

from pydantic import BaseModel, ConfigDict, Field, field_validator

# What one event may carry: a message, not a document (it is copied once per
# target into Redis). Longer is cut, never refused: the words often come from
# outside (a fan's name, a caption).
MAX_TITLE = 256
MAX_TEXT = 3000
_CUT = "…"


def _cut(value: str, size: int) -> str:
    return value if len(value) <= size else value[: size - 1] + _CUT


class Event(BaseModel):
    """`name`: the producer's own dotted name (`links.clicks`, `accounts.views`),
    what logs, spans and a later per-event rule go by. `title` is the first line a
    person reads, `text` the rest (plain text: no markup). `url`: a page of this
    site (`/links`, made absolute on `emit`) or an http(s) address. `key`: the same
    key goes out once per sender and target (`settings.events.key_ttl_s`), e.g.
    `links.clicks:3000:<link id>`. `replaces`: a name; this message takes the place
    of the one sent before under it (the sender deletes that one once this one is
    there), e.g. `chatting.unanswered`: one list in the chat, always the newest.

    A misspelt field (`link=`) or another kind of address (`javascript:`) is
    refused when the event is made: a bug in its producer, found by its test."""

    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1)
    text: str = ""
    url: str | None = None
    key: str | None = Field(default=None, min_length=1, max_length=200)
    replaces: str | None = Field(default=None, min_length=1, max_length=64)
    # When it was made: an older one arriving late never replaces a newer one.
    at: float = Field(default_factory=time.time)

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
