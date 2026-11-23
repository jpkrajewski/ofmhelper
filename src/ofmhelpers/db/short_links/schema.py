# Copyright (C) 2026 jpkrajewski

import re
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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


class LinkSort(StrEnum):
    CLICKS = "clicks"
    VIEWS = "views"
    CTR = "ctr"
    NEWEST = "newest"
    ADDRESS = "address"


class SelectedLinks(BaseModel):
    ids: list[int] = Field(min_length=1)


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
                ),
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
            line=line,
            points=points,
            x_ticks=x_ticks,
            y_ticks=y_ticks,
            y_max=y_max,
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
