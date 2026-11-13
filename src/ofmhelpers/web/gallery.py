"""A tool's gallery of past runs, newest first.

`gallery = Gallery({TASK})` once per router. Every route of it takes
`query: GalleryParams` and calls `gallery.page(viewer, query)`: the page
itself, `/gallery` (`?offset=&hidden=`, gallery-scroll.js and
hidden-cards.js) and `/gallery/latest` (`gallery.page(viewer,
query.newest())`, live-gallery.js). Turning jobs into cards is the router's:
each tool has its own card.

`?date_from=&date_to=` (`ui.date_range`, the toolbar's date filter): only the runs
started on those days, both included, days of the reader's own time zone
(the header clock's, cookie `ofm_timezone`). The page hands the same two
dates to its `/gallery` and `/gallery/latest` links (`GalleryQuery.dates`),
so the scroll and the live poll stay inside the filter.

A gallery shows the reader's own jobs and their VAs' (`Viewer.actors`); a
job someone else made carries its maker's name (`Job.made_by`).
"""

from collections.abc import Callable, Collection, Mapping
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Self
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, Query
from pydantic import Field, PrivateAttr, model_validator

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import count_hidden_jobs, gallery_page
from ofmhelpers.schemas.persistence import Created
from ofmhelpers.schemas.time_zones import time_zone
from ofmhelpers.web.forms import LoggedFields
lazy from ofmhelpers.schemas.persistence import GalleryJobs
lazy from ofmhelpers.schemas.users import Viewer

# A day the date filter takes: a typed-in "?date_to=9999-12-31" has no day
# after it (OverflowError, a 500), so a day outside these is refused (422).
Day = Annotated[date, Field(ge=date(2000, 1, 1), le=date(2100, 1, 1))]


class GalleryQuery(LoggedFields):
    offset: int = Field(default=0, ge=0)
    hidden: bool = False
    # The date filter: runs started from `date_from` to `date_to`, both
    # included. Not `from` / `to`: /clone's `?from=` is where "Clone it"
    # on /edit came from.
    date_from: Day | None = None
    date_to: Day | None = None
    # Whose days those are: the reader's time zone (`in_zone`).
    _zone: str = PrivateAttr(default_factory=lambda: time_zone(None).zone)

    @model_validator(mode="before")
    @classmethod
    def _blank_is_unset(cls, data: Any) -> Any:
        """The filter's form sends both dates, an empty one as `date_to=`."""
        if isinstance(data, Mapping):
            return {k: v for k, v in data.items() if v != ""}
        return data

    @model_validator(mode="after")
    def _in_order(self) -> Self:
        """The two dates the other way round mean the same days."""
        if self.date_from and self.date_to and self.date_from > self.date_to:
            self.date_from, self.date_to = self.date_to, self.date_from
        return self

    def in_zone(self, zone: str) -> Self:
        """This query, its dates read as days of `zone`."""
        self._zone = zone
        return self

    def newest(self) -> Self:
        """The same filter's first page of shown cards (`/gallery/latest`);
        a copy keeps the zone."""
        return self.model_copy(update={"offset": 0, "hidden": False})

    @property
    def created(self) -> Created | None:
        """The filter as moments: the start of `date_from` to the end of `date_to`
        in the reader's zone. None: no date was picked."""
        if self.date_from is None and self.date_to is None:
            return None
        return Created(
            since=self._midnight(self.date_from) if self.date_from else None,
            until=self._midnight(self.date_to + timedelta(days=1))
            if self.date_to
            else None,
        )

    def _midnight(self, day: date) -> float:
        """The moment `day` starts in the reader's zone."""
        return datetime.combine(day, time.min, ZoneInfo(self._zone)).timestamp()

    @property
    def today(self) -> date:
        """Today in the reader's zone: the last day the calendars offer."""
        return datetime.now(ZoneInfo(self._zone)).date()

    @property
    def dates(self) -> str:
        """The filter as the query string the page's `/gallery` links carry
        ("?date_from=2026-11-01&date_to=2026-11-12"); "" with no date picked."""
        picked = {"date_from": self.date_from, "date_to": self.date_to}
        query = urlencode({k: v.isoformat() for k, v in picked.items() if v})
        return f"?{query}" if query else ""


def gallery_query(
    query: Annotated[GalleryQuery, Query()],
    ofm_timezone: Annotated[str | None, Cookie()] = None,
) -> GalleryQuery:
    """The gallery's query, its dates read as days of the reader's zone."""
    return query.in_zone(time_zone(ofm_timezone).zone)


GalleryParams = Annotated[GalleryQuery, Depends(gallery_query)]


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
            created=asked.created,
        )

    def hidden_count(self, viewer: Viewer) -> int:
        return count_hidden_jobs(self.tasks, viewer.actors)
