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

`?va=&not_uploaded=` (/clone's toolbar, owner 2026-11-14): only the runs
one person made (the reader or one of their VAs; anyone else is a 404), and
only the finished ones whose video is in no Drive folder yet -- the owner
picks a VA, sees what is left to post and uploads it. They ride in the same
query string as the dates (`GalleryQuery.filters`).

A gallery shows the reader's own jobs and their VAs' (`Viewer.actors`); a
job someone else made carries its maker's name (`Job.made_by`).

Hiding is not a VA's (`Action.HIDE`): their gallery shows every job of
theirs, also one their owner hid from the owner's own view, and has no
"Show hidden".
"""

from collections.abc import Callable, Collection, Mapping
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Self
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Cookie, Depends, HTTPException, Query
from pydantic import Field, PrivateAttr, model_validator

from ofmhelpers.auth.access import Action, allowed
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import count_hidden_jobs, gallery_page
from ofmhelpers.schemas.persistence import Created
from ofmhelpers.schemas.time_zones import time_zone
from ofmhelpers.web.forms import LoggedFields
lazy from ofmhelpers.schemas.persistence import GalleryJobs
lazy from ofmhelpers.schemas.users import Viewer
lazy from ofmhelpers.web.dependencies import Viewing

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
    # Only this person's runs: the reader's own id or one of their VAs'
    # (`gallery_query` refuses anyone else).
    va: int | None = Field(default=None, ge=1)
    # Only finished runs whose video is in no Drive folder yet.
    not_uploaded: bool = False
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

    def link(self, **changes: object) -> str:
        """The query string of this filter with `changes` made (a toolbar
        link: `link(va=7)`, `link(not_uploaded=True)`), from its first page."""
        return self.model_copy(update={"offset": 0, **changes}).filters

    @property
    def filters(self) -> str:
        """The filter as the query string the page's `/gallery` links carry
        ("?date_from=2026-11-01&date_to=2026-11-12&va=7&not_uploaded=1");
        "" with nothing picked."""
        picked = {
            "date_from": self.date_from.isoformat() if self.date_from else None,
            "date_to": self.date_to.isoformat() if self.date_to else None,
            "va": self.va,
            "not_uploaded": 1 if self.not_uploaded else None,
        }
        query = urlencode({k: v for k, v in picked.items() if v is not None})
        return f"?{query}" if query else ""


def gallery_query(
    query: Annotated[GalleryQuery, Query()],
    viewer: Viewing,
    ofm_timezone: Annotated[str | None, Cookie()] = None,
) -> GalleryQuery:
    """The gallery's query, its dates read as days of the reader's zone.
    `va` must be someone whose runs the reader sees."""
    if query.va is not None and query.va not in viewer.user_ids:
        raise HTTPException(status_code=404, detail="No such VA")
    return query.in_zone(time_zone(ofm_timezone).zone)


GalleryParams = Annotated[GalleryQuery, Depends(gallery_query)]


def _default_limit() -> int:
    return settings.web.gallery_limit


def _hides(viewer: Viewer) -> bool:
    return allowed(viewer.user.role, Action.HIDE)


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
            frozenset({str(asked.va)}) if asked.va is not None else viewer.actors,
            asked.hidden if _hides(viewer) else None,
            reader=viewer.user.id,
            by_use=self.by_use,
            created=asked.created,
            not_uploaded=asked.not_uploaded,
        )

    def hidden_count(self, viewer: Viewer) -> int:
        if not _hides(viewer):
            return 0
        return count_hidden_jobs(self.tasks, viewer.actors)
