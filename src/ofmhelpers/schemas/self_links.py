"""Shapes for the self-hosted bridge pages (provider "self"): one hit on a
bridge page, the custom domains we serve them on, and the Self links page's
statistics over any time frame."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from ofmhelpers.schemas.links import ctr, utc_today


class SelfClick(BaseModel):
    """One hit on a bridge page, ready to store: which link, whether it was a
    landing (`view`) or a continue-through (`click`), and what Cloudflare told
    us about the visitor. Everything but `link_id`/`kind` is best-effort."""

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
    """A custom domain we serve bridge pages on. `owner_id` None = shared by
    everyone; `status`: pending | active | disabled."""

    id: int
    host: str
    owner_id: int | None = None
    status: str
    added_at: float

    model_config = {"from_attributes": True}


class SelfDomainForm(BaseModel):
    """Adding a domain from the Self links page."""

    host: str = Field(min_length=3, max_length=253)

    @model_validator(mode="after")
    def _clean(self) -> SelfDomainForm:
        host = self.host.strip().lower()
        # Accept a pasted URL or bare host; keep only the hostname.
        host = re.sub(r"^[a-z]+://", "", host).split("/")[0].split(":")[0]
        if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", host):
            msg = "Enter a domain like bridge.example.com"
            raise ValueError(msg)
        self.host = host
        return self


# --- the Self links page's statistics ---------------------------------------

# How far back the stats page may look, and the presets it offers (days).
STAT_PERIODS = (1, 7, 30, 90)
MAX_STAT_DAYS = 366


class SelfRange(BaseModel):
    """The window the Self links page reads: a `days` preset, or an explicit
    `from`/`to` (UTC dates). A single day is shown by the hour."""

    days: int | None = Field(default=None, ge=1, le=MAX_STAT_DAYS)
    start: date | None = Field(default=None, alias="from")
    end: date | None = Field(default=None, alias="to")
    # The scope picker: one link's slug, or None for all of them.
    page: str | None = None

    model_config = {"populate_by_name": True}

    def resolved(self, today: date | None = None) -> tuple[date, date]:
        today = today or utc_today()
        if self.start and self.end:
            lo, hi = sorted((self.start, self.end))
            lo = max(lo, today - timedelta(days=MAX_STAT_DAYS - 1))
            return lo, min(hi, today)
        days = self.days or 7
        return today - timedelta(days=days - 1), today

    def by_hour(self, today: date | None = None) -> bool:
        lo, hi = self.resolved(today)
        return lo == hi


class SelfSeriesPoint(BaseModel):
    bucket: str
    views: int
    clicks: int


class SelfBreakdownRow(BaseModel):
    value: str
    clicks: int


class SelfLinkStat(BaseModel):
    """One self link's numbers over the window."""

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
    """Round `value` up to a clean 1/2/5 x 10^k axis top."""
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
    """One point of the activity line: where it sits (percent of the plot) and
    what it stands for (for the hover tooltip)."""

    x: float
    y: float
    label: str
    views: int
    clicks: int


class SelfAxisTick(BaseModel):
    label: str
    at: float  # percent along the axis


class SelfChart(BaseModel):
    """The activity line chart as an SVG draws it: a 0..100 plot, y from the
    top. `line` is the polyline points; `points` carry the hover data."""

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
            # 6% headroom at the top, baseline at the bottom.
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
        # At most ~6 x labels, evenly spaced.
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
    """Everything the Self links page draws for one window."""

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
