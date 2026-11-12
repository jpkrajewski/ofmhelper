"""A tool's gallery of past runs, newest first.

`gallery = Gallery({TASK})` once per router. The page's route and a
`/gallery/latest` call `gallery.page(viewer)` for the newest page; `/gallery`
takes `query: GalleryParams` (`?offset=&hidden=`, gallery-scroll.js and
hidden-cards.js) and calls `gallery.page(viewer, query)`. Turning jobs into cards
is the router's: each tool has its own card.

A gallery shows the reader's own jobs and their VAs' (`Viewer.actors`); a
job someone else made carries its maker's name (`Job.made_by`).
"""

from collections.abc import Callable, Collection
from typing import Annotated

from fastapi import Query
from pydantic import Field

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import count_hidden_jobs, gallery_page
from ofmhelpers.web.forms import LoggedFields
lazy from ofmhelpers.schemas.persistence import GalleryJobs
lazy from ofmhelpers.schemas.users import Viewer


class GalleryQuery(LoggedFields):
    offset: int = Field(default=0, ge=0)
    hidden: bool = False


GalleryParams = Annotated[GalleryQuery, Query()]


def _default_limit() -> int:
    return settings.web.gallery_limit


class Gallery:
    def __init__(
        self,
        tasks: Collection[str],
        limit: Callable[[], int] = _default_limit,
        *,
        by_use: bool = False,
    ) -> None:
        """`by_use`: the job whose download was asked for or used last
        first (/download-assets), not the newest."""
        self.tasks = set(tasks)
        self.limit = limit
        self.by_use = by_use

    def page(self, viewer: Viewer, query: GalleryQuery | None = None) -> GalleryJobs:
        asked = query or GalleryQuery()
        return gallery_page(
            self.tasks,
            asked.offset,
            self.limit(),
            viewer.actors,
            asked.hidden,
            reader=viewer.user.id,
            by_use=self.by_use,
        )

    def hidden_count(self, viewer: Viewer) -> int:
        return count_hidden_jobs(self.tasks, viewer.actors)
