"""/clone-links' data: what Check answers (or why it refuses the paste),
and Generate's preview of one link: its cover, length and price, or why it
cannot be cloned."""

from __future__ import annotations

from pydantic import BaseModel, Field, computed_field

from ofmhelpers.web.clone_links.links import BadLine  # noqa: TC001 -- pydantic reads it


class CheckRefused(BaseModel):
    """Check's 400 `detail` when the paste is refused before anything is
    checked: the page's dialog shows `message` and a row per line."""

    message: str
    lines: list[BadLine] = []


class CheckedLinks(BaseModel):
    """What Check answers: the pasted links, cleaned, each once (the box
    shows them instead of what was pasted)."""

    links: list[str]


class RecreateLinks(BaseModel):
    """What a card's Recreate puts back in the box, one link per line."""

    links: list[str]


class PreviewsRequest(BaseModel):
    """Generate's look at these links, priced at this provider and
    resolution."""

    urls: list[str] = Field(min_length=1, max_length=500)
    provider: str = ""
    resolution: str = ""


class LinkPreview(BaseModel):
    """One link in Generate's dialog: the video's cover and length and what
    cloning it costs, read without downloading it. `error`: why it cannot
    be read, in a few fixed words (yt-dlp's own answer goes to the log
    only: it names hosts and ports); `too_long`: longer than the provider
    takes (`max_s`; `seconds` rounded up, None when that would read as
    fitting). Only a `ready` one can be ticked."""

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
