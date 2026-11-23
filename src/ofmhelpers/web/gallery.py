# Copyright (C) 2026 jpkrajewski

from collections.abc import Callable, Collection, Mapping
from datetime import date, datetime, time, timedelta
from typing import Annotated, Self
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

from fastapi import Depends, HTTPException, Query
from pydantic import Field, PrivateAttr, model_validator

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.schema import Created
from ofmhelpers.features.jobs.gallery import GalleryAsk, gallery_jobs, hidden_jobs
from ofmhelpers.web.access import Action, allowed
from ofmhelpers.web.forms import LoggedFields
from ofmhelpers.web.time_zones import time_zone
lazy from ofmhelpers.db.jobs.schema import GalleryJobs
lazy from ofmhelpers.db.users.schema import Viewer
lazy from ofmhelpers.web.dependencies import Viewing, Zone

Day = Annotated[date, Field(ge=date(2000, 1, 1), le=date(2100, 1, 1))]


class GalleryQuery(LoggedFields):
    offset: int = Field(default=0, ge=0)
    hidden: bool = False
    date_from: Day | None = None
    date_to: Day | None = None
    va: int | None = Field(default=None, ge=1)
    not_uploaded: bool = False
    _zone: str = PrivateAttr(default_factory=lambda: time_zone(None).zone)

    @model_validator(mode="before")
    @classmethod
    def _blank_is_unset(cls, data: object) -> object:
        if isinstance(data, Mapping):
            return {k: v for k, v in data.items() if v != ""}
        return data

    @model_validator(mode="after")
    def _in_order(self) -> Self:
        if self.date_from and self.date_to and self.date_from > self.date_to:
            self.date_from, self.date_to = self.date_to, self.date_from
        return self

    def in_zone(self, zone: str) -> Self:
        self._zone = zone
        return self

    def newest(self) -> Self:
        return self.model_copy(update={"offset": 0, "hidden": False})

    @property
    def created(self) -> Created | None:
        if self.date_from is None and self.date_to is None:
            return None
        return Created(
            since=self._midnight(self.date_from) if self.date_from else None,
            until=self._midnight(self.date_to + timedelta(days=1))
            if self.date_to
            else None,
        )

    def _midnight(self, day: date) -> float:
        return datetime.combine(day, time.min, ZoneInfo(self._zone)).timestamp()

    @property
    def today(self) -> date:
        return datetime.now(ZoneInfo(self._zone)).date()

    def link(self, changes: dict[str, object]) -> str:
        return self.model_copy(update={"offset": 0, **changes}).filters

    @property
    def filters(self) -> str:
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
    zone: Zone,
) -> GalleryQuery:
    if query.va is not None and query.va not in viewer.user_ids:
        raise HTTPException(status_code=404, detail="No such VA")
    return query.in_zone(zone.zone)


GalleryParams = Annotated[GalleryQuery, Depends(gallery_query)]


def _default_limit() -> int:
    return settings.web.gallery_limit


def _hides(viewer: Viewer) -> bool:
    return allowed(viewer.access_role, Action.HIDE)


class Gallery:
    def __init__(
        self,
        tasks: Collection[str],
        limit: Callable[[], int] = _default_limit,
        *,
        by_use: bool = False,
    ) -> None:
        self.tasks = set(tasks)
        self.limit = limit
        self.by_use = by_use

    def page(self, viewer: Viewer, query: GalleryQuery | None = None) -> GalleryJobs:
        asked = query or GalleryQuery()
        readers = (
            frozenset({str(asked.va)})
            if asked.va is not None
            else frozenset(viewer.actors)
        )
        return gallery_jobs(
            GalleryAsk(
                tasks=frozenset(self.tasks),
                offset=asked.offset,
                limit=self.limit(),
                readers=readers,
                hidden=asked.hidden if _hides(viewer) else None,
                reader=viewer.user.id,
                by_use=self.by_use,
                created=asked.created,
                not_uploaded=asked.not_uploaded,
            ),
        )

    def hidden_count(self, viewer: Viewer) -> int:
        if not _hides(viewer):
            return 0
        return hidden_jobs(frozenset(self.tasks), frozenset(viewer.actors))
