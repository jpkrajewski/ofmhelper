"""A tool's gallery of past runs, newest first.

`gallery = Gallery({TASK})` once per router. The page's route and a
`/gallery/latest` call `gallery.page(actor)` for the newest page; `/gallery`
takes `query: GalleryParams` (`?offset=&hidden=`, gallery-scroll.js and
hidden-cards.js) and calls `gallery.page(actor, query)`. Turning jobs into cards
is the router's: each tool has its own card.
"""

from collections.abc import Callable, Collection
from typing import Annotated

from fastapi import Query
from pydantic import Field

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import count_hidden_jobs, gallery_page
from ofmhelpers.web.forms import LoggedFields
lazy from ofmhelpers.schemas.persistence import GalleryJobs


class GalleryQuery(LoggedFields):
    offset: int = Field(default=0, ge=0)
    hidden: bool = False


GalleryParams = Annotated[GalleryQuery, Query()]


def _default_limit() -> int:
    return settings.web.gallery_limit


class Gallery:
    def __init__(
        self, tasks: Collection[str], limit: Callable[[], int] = _default_limit
    ) -> None:
        self.tasks = set(tasks)
        self.limit = limit

    def page(self, actor: str, query: GalleryQuery | None = None) -> GalleryJobs:
        asked = query or GalleryQuery()
        return gallery_page(self.tasks, asked.offset, self.limit(), actor, asked.hidden)

    def hidden_count(self, actor: str) -> int:
        return count_hidden_jobs(self.tasks, actor)
