# Copyright (C) 2026 jpkrajewski

from collections.abc import Callable, Mapping
from typing import Any, Self
from urllib.parse import urlencode
lazy from datetime import date

from pydantic import BaseModel, Field, HttpUrl, JsonValue, model_validator

from ofmhelpers.db.short_links.catalog import (
    DayStat,
    HourStat,
    RemoteLink,
    Usage,
    provider_infos,
    targets,
)
from ofmhelpers.db.short_links.schema import (
    LinkPeriod,
    LinkSort,
    LinkStatus,
    SelectedLinks,
    ctr,
)
from ofmhelpers.db.tracked_accounts.schema import NO_CREATOR, CreatorName, SortOrder


class ShortLink(BaseModel):
    id: int
    user_id: int
    provider: str
    remote_id: str | None = None
    slug: str
    domain: str
    url: str = ""
    destination: str
    target: str | None = None
    creator: str | None = None
    account_id: int | None = None
    account_handle: str | None = None
    status: LinkStatus
    adult: bool | None = None
    error: str | None = None
    created_at: float
    days: list[DayStat] = []

    @property
    def provider_label(self) -> str:
        info = provider_infos().get(self.provider)
        return info.label if info else self.provider

    @property
    def target_label(self) -> str:
        found = targets().get(self.target or "")
        return found.label if found else "Other"

    @property
    def address(self) -> str:
        return self.url.removeprefix("https://") or f"{self.domain}/{self.slug}"

    @property
    def busy(self) -> bool:
        return self.status in ("creating", "deleting")

    def _in(self, days: int, today: date | None) -> list[DayStat]:
        start = LinkPeriod(days=days).start(today)
        return [d for d in self.days if d.day >= start]

    def views(self, days: int, today: date | None = None) -> int:
        return sum(d.views for d in self._in(days, today))

    def clicks(self, days: int, today: date | None = None) -> int:
        return sum(d.clicks for d in self._in(days, today))

    def ctr(self, days: int, today: date | None = None) -> float | None:
        return ctr(self.views(days, today), self.clicks(days, today))


class AccountLinks(BaseModel):
    account_id: int
    links: list[ShortLink]


class LinkSync(BaseModel):
    user_id: int
    provider: str
    synced_at: float | None = None
    claimed_at: float | None = None
    error: str | None = None
    usage: Usage | None = None


class LinkQuery(BaseModel):
    q: str = Field(default="", max_length=64)
    provider: str = Field(default="", max_length=32)
    creator: str = Field(default="", max_length=64)
    account: int | None = None
    sort: LinkSort = LinkSort.CLICKS
    order: SortOrder | None = None
    period: int | None = Field(default=None, ge=1, le=3650)
    page: int = Field(default=1, ge=1)
    per_page: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="before")
    @classmethod
    def _blank_is_unset(cls, data: JsonValue) -> JsonValue:
        if isinstance(data, Mapping):
            return {k: v for k, v in data.items() if v != ""}
        return data

    def matches(self, link: ShortLink) -> bool:
        needle = self.q.strip().lstrip("@").lower()
        haystack = (
            f"{link.address} {link.destination} {link.account_handle or ''} "
            f"{link.creator or ''}"
        ).lower()
        if self.creator == NO_CREATOR:
            creator_ok = link.creator is None
        else:
            creator_ok = not self.creator or link.creator == self.creator
        return (
            (not needle or needle in haystack)
            and (not self.provider or link.provider == self.provider)
            and (self.account is None or link.account_id == self.account)
            and creator_ok
        )

    @property
    def direction(self) -> SortOrder:
        if self.order is not None:
            return self.order
        return SortOrder.ASC if self.sort == LinkSort.ADDRESS else SortOrder.DESC

    def sort_link(self, sort: LinkSort) -> str:
        if sort != self.sort:
            return self.link(changes={"sort": sort, "order": None})
        flipped = SortOrder.ASC if self.direction == SortOrder.DESC else SortOrder.DESC
        return self.link(changes={"order": flipped})

    def ordered(self, links: list[ShortLink], days: int) -> list[ShortLink]:
        keys: dict[LinkSort, Callable[[ShortLink], Any]] = {
            LinkSort.CLICKS: lambda x: x.clicks(days),
            LinkSort.VIEWS: lambda x: x.views(days),
            LinkSort.CTR: lambda x: x.ctr(days),
            LinkSort.ADDRESS: lambda x: x.address,
        }
        reverse = self.direction == SortOrder.DESC
        if self.sort == LinkSort.NEWEST:
            return links if reverse else links[::-1]
        key = keys[self.sort]
        known = [x for x in links if key(x) is not None]
        unknown = [x for x in links if key(x) is None]
        return sorted(known, key=key, reverse=reverse) + unknown

    def link(
        self,
        path: str = "/links",
        changes: dict[str, object] | None = None,
    ) -> str:
        update = {"page": 1, **(changes or {})}
        data = self.model_copy(update=update).model_dump(mode="json")
        defaults = LinkQuery().model_dump(mode="json")
        params = {
            k: v for k, v in data.items() if v not in (None, "") and v != defaults[k]
        }
        return path + (f"?{urlencode(params)}" if params else "")


class LinkPage(BaseModel):
    rows: list[ShortLink]
    total: int
    page: int
    pages: int
    per_page: int

    @property
    def first(self) -> int:
        return (self.page - 1) * self.per_page + 1 if self.total else 0

    @property
    def last(self) -> int:
        return self.first + len(self.rows) - 1 if self.rows else 0

    @classmethod
    def of(cls, links: list[ShortLink], page: int, per_page: int) -> Self:
        pages = max((len(links) + per_page - 1) // per_page, 1)
        page = min(page, pages)
        start = (page - 1) * per_page
        return cls(
            rows=links[start : start + per_page],
            total=len(links),
            page=page,
            pages=pages,
            per_page=per_page,
        )


class LinkSummary(BaseModel):
    links: int
    views: int
    clicks: int
    ungated: int

    @property
    def ctr(self) -> float | None:
        return ctr(self.views, self.clicks)

    @classmethod
    def of(cls, links: list[ShortLink], days: int) -> Self:
        return cls(
            links=len(links),
            views=sum(x.views(days) for x in links),
            clicks=sum(x.clicks(days) for x in links),
            ungated=sum(x.adult is False for x in links),
        )


class ProviderState(BaseModel):
    key: str
    label: str
    has_key: bool
    synced_at: float | None = None
    syncing: bool = False
    error: str | None = None
    usage: Usage | None = None


class GenerateLinks(BaseModel):
    provider: str
    creator: CreatorName
    target: str
    destination: HttpUrl
    domain: str
    account_ids: list[int] = Field(min_length=1)

    @model_validator(mode="after")
    def _known(self) -> Self:
        info = provider_infos().get(self.provider)
        if info is None:
            msg = f"Unknown link provider: {self.provider}"
            raise ValueError(msg)
        if info.domains and self.domain not in info.domains:
            msg = f"{info.label} has no domain {self.domain}"
            raise ValueError(msg)
        target = targets().get(self.target)
        if target is None:
            msg = f"Unknown target page: {self.target}"
            raise ValueError(msg)
        if problem := target.problem(str(self.destination)):
            raise ValueError(problem)
        if not self.creator:
            msg = "Pick a model"
            raise ValueError(msg)
        return self


class AssignLinkCreator(SelectedLinks):
    creator: CreatorName = ""


class SelfLink(BaseModel):
    id: int
    remote_id: str | None = None
    slug: str
    domain: str
    url: str
    destination: str
    active: bool
    adult: bool | None = None
    created_at: float | None = None

    def as_remote(self) -> RemoteLink:
        return RemoteLink.model_validate(self.model_dump(exclude={"id"}))


class SelfLinkDays(BaseModel):
    link_id: int
    days: list[DayStat]


class SelfLinkHours(BaseModel):
    link_id: int
    hours: list[HourStat]
