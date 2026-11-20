import re
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal, Self
from urllib.parse import urlencode

from pydantic import BaseModel, Field, HttpUrl, model_validator

from ofmhelpers.db.short_links.catalog import (
    DayStat,
    HourStat,
    RemoteLink,
    Usage,
    provider_infos,
    targets,
)
from ofmhelpers.db.tracked_accounts.schema import NO_CREATOR, CreatorName, SortOrder

LinkStatus = Literal["creating", "active", "inactive", "failed", "deleting"]


def utc_today() -> date:
    return datetime.now(UTC).date()


def ctr(views: int, clicks: int) -> float | None:
    return clicks / views * 100 if views else None


class LinkPeriod(BaseModel):
    days: int

    @property
    def label(self) -> str:
        return "Today" if self.days == 1 else f"Last {self.days} days"

    @property
    def short(self) -> str:
        return "today" if self.days == 1 else f"{self.days}d"

    def start(self, today: date | None = None) -> date:
        return (today or utc_today()) - timedelta(days=self.days - 1)


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


class PlannedLink(BaseModel):
    slug: str
    domain: str
    destination: str
    target: str | None = None
    account_id: int | None = None
    creator: str = ""


class LinkAddress(BaseModel, frozen=True):
    domain: str
    slug: str


class LinkSyncKey(BaseModel, frozen=True):
    user_id: int
    provider: str


class LinkSync(BaseModel):
    user_id: int
    provider: str
    synced_at: float | None = None
    claimed_at: float | None = None
    error: str | None = None
    usage: Usage | None = None


class LinkSort(StrEnum):
    CLICKS = "clicks"
    VIEWS = "views"
    CTR = "ctr"
    NEWEST = "newest"
    ADDRESS = "address"


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
    def _blank_is_unset(cls, data: Any) -> Any:
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
            return self.link(sort=sort, order=None)
        flipped = SortOrder.ASC if self.direction == SortOrder.DESC else SortOrder.DESC
        return self.link(order=flipped)

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

    def link(self, path: str = "/links", **changes: object) -> str:
        update = {"page": 1, **changes}
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


class SelectedLinks(BaseModel):
    ids: list[int] = Field(min_length=1)


class AssignLinkCreator(SelectedLinks):
    creator: CreatorName = ""


class LinksQueued(BaseModel):
    count: int


class SelfClick(BaseModel):
    link_id: int
    kind: Literal["view", "click"]
    ip: str | None = None
    country: str | None = None
    region: str | None = None
    city: str | None = None
    tz: str | None = None
    referrer: str | None = None
    platform: str | None = None
    device: str | None = None
    browser: str | None = None
    ua: str | None = None


class SelfDomain(BaseModel):
    id: int
    host: str
    owner_id: int | None = None
    status: str
    added_at: float

    model_config = {"from_attributes": True}


class BridgeTarget(BaseModel):
    id: int
    destination: str
    slug: str
    domain: str


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


class SelfTotals(BaseModel):
    views: int = 0
    clicks: int = 0
    visitors: int = 0


class SelfLinkCount(BaseModel):
    link_id: int
    views: int
    clicks: int


class DateSpan(BaseModel, frozen=True):
    start: date
    end: date


class SelfDomainForm(BaseModel):
    host: str = Field(min_length=3, max_length=253)

    @model_validator(mode="after")
    def _clean(self) -> SelfDomainForm:
        host = self.host.strip().lower()
        host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split(":")[0]
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
            msg = "Enter a domain like bridge.example.com"
            raise ValueError(msg)
        self.host = host
        return self


STAT_PERIODS = (1, 7, 30, 90)
MAX_STAT_DAYS = 366


class SelfRange(BaseModel):
    days: int | None = Field(default=None, ge=1, le=MAX_STAT_DAYS)
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")
    page: str | None = None

    model_config = {"populate_by_name": True}

    def resolved(self, today: date | None = None) -> DateSpan:
        today = today or utc_today()
        if self.start and self.end:
            lo, hi = sorted((self.start, self.end))
            lo = max(lo, today - timedelta(days=MAX_STAT_DAYS - 1))
            return DateSpan(start=lo, end=min(hi, today))
        days = self.days or 7
        return DateSpan(start=today - timedelta(days=days - 1), end=today)

    def by_hour(self, today: date | None = None) -> bool:
        span = self.resolved(today)
        return span.start == span.end


class SelfSeriesPoint(BaseModel):
    bucket: str
    views: int
    clicks: int


class SelfBreakdownRow(BaseModel):
    value: str
    clicks: int


class SelfLinkStat(BaseModel):
    id: int
    slug: str
    domain: str
    url: str
    destination: str
    views: int
    clicks: int

    @property
    def ctr(self) -> float | None:
        return ctr(self.views, self.clicks)


_AXIS_STEPS = (1, 2, 2.5, 5, 10)


def _nice_ceil(value: int) -> int:
    small = 5
    if value <= small:
        return max(value, 1)
    power = 10 ** (len(str(value)) - 1)
    for step in _AXIS_STEPS:
        top = int(step * power)
        if top >= value:
            return top
    return int(10 * power)


def _tick_label(bucket: str, *, by_hour: bool) -> str:
    when = datetime.fromisoformat(bucket)
    return when.strftime("%H:%M") if by_hour else when.strftime("%b %-d")


class SelfChartPoint(BaseModel):
    x: float
    y: float
    label: str
    views: int
    clicks: int


class SelfAxisTick(BaseModel):
    label: str
    at: float


class SelfChart(BaseModel):
    line: str = ""
    points: list[SelfChartPoint] = []
    x_ticks: list[SelfAxisTick] = []
    y_ticks: list[SelfAxisTick] = []
    y_max: int = 0

    @classmethod
    def build(cls, series: list[SelfSeriesPoint], *, by_hour: bool) -> SelfChart:
        if not series:
            return cls()
        y_max = _nice_ceil(max(p.views for p in series))
        n = len(series)
        points: list[SelfChartPoint] = []
        for i, p in enumerate(series):
            x = round(i / (n - 1) * 100, 2) if n > 1 else 50.0
            y = round(100 - (p.views / y_max) * 94, 2)
            points.append(
                SelfChartPoint(
                    x=x,
                    y=y,
                    label=_tick_label(p.bucket, by_hour=by_hour),
                    views=p.views,
                    clicks=p.clicks,
                )
            )
        line = " ".join(f"{pt.x},{pt.y}" for pt in points)
        step = max(1, (n + 5) // 6)
        x_ticks = [
            SelfAxisTick(label=points[i].label, at=points[i].x)
            for i in range(0, n, step)
        ]
        y_ticks = [
            SelfAxisTick(label=str(y_max), at=0.0),
            SelfAxisTick(label=str(y_max // 2), at=50.0),
            SelfAxisTick(label="0", at=100.0),
        ]
        return cls(
            line=line, points=points, x_ticks=x_ticks, y_ticks=y_ticks, y_max=y_max
        )


class SelfStats(BaseModel):
    start: date
    end: date
    by_hour: bool
    views: int
    clicks: int
    visitors: int
    series: list[SelfSeriesPoint]
    chart: SelfChart
    countries: list[SelfBreakdownRow]
    devices: list[SelfBreakdownRow]
    platforms: list[SelfBreakdownRow]
    referrers: list[SelfBreakdownRow]
    browsers: list[SelfBreakdownRow]
    links: list[SelfLinkStat]

    @property
    def ctr(self) -> float | None:
        return ctr(self.views, self.clicks)
