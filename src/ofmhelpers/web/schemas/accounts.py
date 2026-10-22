"""/accounts: a tracked social account with its numbers for a reporting
period, the list's query (search, filters, sort, period, paging), a page of
it and its totals, the analytics page's history and top posts, and what the
page posts.

Metric rules, everywhere: followers are a point-in-time total; views are
per period (posts published in it, compared with the period before). A
number is shown only from data that covers it: 0 is a confirmed zero, None
(a dash) is "no data for that yet", a failed sync is its own warning."""

import itertools
import math
import time
from collections.abc import Callable, Iterator, Mapping
from enum import StrEnum
from typing import Annotated, Any, Self
from urllib.parse import urlencode

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    computed_field,
    model_validator,
)

from ofmhelpers.socials import (
    AccountMetrics,
    AccountProfile,
    PostStat,
    PostViews,
    ViewWindow,
    get_platform,
    platforms,
)

# The creator filter's value for accounts with no creator. Creator names
# cannot start with "(", so no real one collides with it.
NO_CREATOR = "(none)"


def _creator_name(value: str) -> str:
    name = " ".join(value.split())[:64]
    if name.startswith("("):
        msg = "A creator name cannot start with '('"
        raise ValueError(msg)
    return name


# A creator as stored: trimmed, single-spaced; "" = none.
CreatorName = Annotated[str, AfterValidator(_creator_name)]


def ago(ts: float, now: float | None = None) -> str:
    """How long ago `ts` was: "just now", "5 min ago", "3 h ago", "2 d ago"."""
    seconds = max((now or time.time()) - ts, 0)
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{int(seconds // size)} {unit} ago"
    return "just now"


def pct_change(now: int, before: int) -> float | None:
    """Percent change; None without a non-zero base to compare with."""
    return (now - before) / before * 100 if before else None


class Period(BaseModel):
    """A reporting period of the period selector."""

    days: int

    @property
    def label(self) -> str:
        return "Last 24 hours" if self.days == 1 else f"Last {self.days} days"

    @property
    def short(self) -> str:
        return "24h" if self.days == 1 else f"{self.days}d"


class AccountStatus(StrEnum):
    SCANNING = "scanning"
    ACTIVE = "active"
    PRIVATE = "private"
    ERROR = "error"


class Baseline(BaseModel):
    """A snapshot's followers, and when it was taken (`days`: the period it
    is the baseline of; 0 for the first/latest snapshot)."""

    days: int
    followers: int
    taken_at: float


class Growth(BaseModel):
    """Follower change. `full`: measured over the whole period; otherwise
    since the account's first snapshot (tracked for less than the period)."""

    delta: int
    pct: float | None
    since: float
    full: bool


class ViewsChange(BaseModel):
    """A period's views and the change from the period before; `previous`
    is None when the scan did not reach back that far."""

    views: int
    previous: int | None

    @property
    def pct(self) -> float | None:
        return None if self.previous is None else pct_change(self.views, self.previous)


class TopPosts(BaseModel):
    """The analytics page's post ranking: every post of the period
    (`in_period`), else the `top_posts` best of the latest scanned ones."""

    posts: list[PostStat]
    in_period: bool

    @property
    def max_views(self) -> int:
        return max((p.views for p in self.posts), default=0)


class TrackedAccount(BaseModel):
    id: int
    # Whose it is.
    user_id: int
    platform: str
    handle: str
    creator: str | None = None
    notes: str = ""
    added_at: float
    scanned_at: float | None = None
    # Set once removed from tracking: not synced or listed, history kept.
    untracked_at: float | None = None
    error: str | None = None
    # None until the first scan succeeds.
    profile: AccountProfile | None = None
    metrics: AccountMetrics | None = None
    # Per period, the newest snapshot at least that old.
    baselines: list[Baseline] = []
    first: Baseline | None = None
    latest: Baseline | None = None
    # The kept posts (every scan's, within retention), as much as view
    # windows need: every period is derived from them on read. Complete
    # from `covered_since` on (None: no scan read any yet).
    post_views: list[PostViews] = []
    covered_since: float | None = None
    # Only loaded for the analytics page.
    posts: list[PostStat] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> AccountStatus:
        """An error wins (the previous numbers stay on show next to it)."""
        if self.error:
            return AccountStatus.ERROR
        if self.profile is None:
            return AccountStatus.SCANNING
        if self.profile.is_private:
            return AccountStatus.PRIVATE
        return AccountStatus.ACTIVE

    @property
    def platform_label(self) -> str:
        return get_platform(self.platform).label

    @property
    def profile_url(self) -> str:
        return get_platform(self.platform).profile_url(self.handle)

    @property
    def name(self) -> str:
        """Display name, else the handle."""
        return (self.profile.display_name if self.profile else "") or self.handle

    @property
    def followers(self) -> int | None:
        return self.metrics.followers if self.metrics else None

    def growth(self, days: int) -> Growth | None:
        """Followers gained over the period; since the first snapshot while
        tracked for less; None until there are two snapshots."""
        if self.metrics is None:
            return None
        now = self.metrics.followers
        base = next((b for b in self.baselines if b.days == days), None)
        if base is not None:
            full = True
        elif self.first and self.latest and self.first.taken_at < self.latest.taken_at:
            base, full = self.first, False
        else:
            return None
        return Growth(
            delta=now - base.followers,
            pct=pct_change(now, base.followers),
            since=base.taken_at,
            full=full,
        )

    def window(self, days: int) -> ViewWindow | None:
        """The last `days` of the latest successful sync; None before one,
        or when its read did not reach back that far."""
        if self.metrics is None or self.latest is None:
            return None
        found = ViewWindow.of(
            self.post_views, days, self.latest.taken_at, self.covered_since
        )
        return found if found.covered else None

    def views(self, days: int) -> ViewsChange | None:
        """The period's views, and those of the `days` before it."""
        if (last := self.window(days)) is None:
            return None
        both = self.window(2 * days)
        return ViewsChange(
            views=last.views, previous=both.views - last.views if both else None
        )

    def posts_in(self, days: int) -> int | None:
        return found.posts if (found := self.window(days)) else None

    def top_posts(self, days: int, limit: int) -> TopPosts:
        since = (self.scanned_at or time.time()) - days * 86400
        recent = [p for p in self.posts if p.posted_at >= since]
        if recent:
            # Every post of the period, not a top N.
            ranked = sorted(recent, key=lambda p: p.views, reverse=True)
            return TopPosts(posts=ranked, in_period=True)
        ranked = sorted(self.posts, key=lambda p: p.views, reverse=True)[:limit]
        return TopPosts(posts=ranked, in_period=False)


class SortOrder(StrEnum):
    DESC = "desc"
    ASC = "asc"


class AccountSort(StrEnum):
    FOLLOWERS = "followers"
    NEWEST = "newest"
    GROWTH = "growth"
    VIEWS = "views"
    HANDLE = "handle"


class AccountQuery(BaseModel):
    """The list's query string. `period` None: the configured default."""

    q: str = Field(default="", max_length=64)
    platform: str = Field(default="", max_length=32)
    creator: str = Field(default="", max_length=64)
    status: AccountStatus | None = None
    sort: AccountSort = AccountSort.FOLLOWERS
    # None: the sort's natural order (names A-Z, numbers high to low).
    order: SortOrder | None = None
    period: int | None = Field(default=None, ge=1, le=3650)
    page: int = Field(default=1, ge=1)
    per_page: int | None = Field(default=None, ge=1, le=1000)

    @model_validator(mode="before")
    @classmethod
    def _blank_is_unset(cls, data: Any) -> Any:
        """The filter form submits every field, an untouched select as
        `status=`: a blank parameter means its default, not a value."""
        if isinstance(data, Mapping):
            return {k: v for k, v in data.items() if v != ""}
        return data

    def matches(self, a: TrackedAccount) -> bool:
        needle = self.q.strip().lstrip("@").lower()
        haystack = f"{a.handle} {a.name} {a.notes} {a.creator or ''}".lower()
        if self.creator == NO_CREATOR:
            creator_ok = a.creator is None
        else:
            creator_ok = not self.creator or a.creator == self.creator
        return (
            (not needle or needle in haystack)
            and (not self.platform or a.platform == self.platform)
            and creator_ok
            and (self.status is None or a.status == self.status)
        )

    @property
    def direction(self) -> SortOrder:
        """The order in effect: the query's, else the sort's natural one."""
        if self.order is not None:
            return self.order
        return SortOrder.ASC if self.sort == AccountSort.HANDLE else SortOrder.DESC

    def sort_link(self, sort: AccountSort) -> str:
        """A column header's link: that sort, its order flipped when it is
        already the current one."""
        if sort != self.sort:
            return self.link(sort=sort, order=None)
        flipped = SortOrder.ASC if self.direction == SortOrder.DESC else SortOrder.DESC
        return self.link(order=flipped)

    def ordered(
        self, accounts: list[TrackedAccount], days: int
    ) -> list[TrackedAccount]:
        """In `direction`; accounts without the number last either way."""

        def views(a: TrackedAccount) -> int | None:
            change = a.views(days)
            return change.views if change else None

        def growth(a: TrackedAccount) -> int | None:
            change = a.growth(days)
            return change.delta if change else None

        keys: dict[AccountSort, Callable[[TrackedAccount], Any]] = {
            AccountSort.FOLLOWERS: lambda a: a.followers,
            AccountSort.GROWTH: growth,
            AccountSort.VIEWS: views,
            AccountSort.HANDLE: lambda a: a.handle,
        }
        reverse = self.direction == SortOrder.DESC
        if self.sort == AccountSort.NEWEST:
            return accounts if reverse else accounts[::-1]
        key = keys[self.sort]
        known = [a for a in accounts if key(a) is not None]
        unknown = [a for a in accounts if key(a) is None]
        return sorted(known, key=key, reverse=reverse) + unknown

    def link(self, path: str = "/accounts", **changes: object) -> str:
        """`path` with this query, `changes` applied (page back to 1 unless
        the change is the page); defaults left out."""
        update = {"page": 1, **changes}
        data = self.model_copy(update=update).model_dump(mode="json")
        defaults = AccountQuery().model_dump(mode="json")
        params = {
            k: v for k, v in data.items() if v not in (None, "") and v != defaults[k]
        }
        return path + (f"?{urlencode(params)}" if params else "")


class AccountSummary(BaseModel):
    """The KPI row over the filtered accounts."""

    accounts: int
    followers: int
    views: int | None
    # Private, or the last sync failed: the rows the page marks red.
    unreachable: int
    # Synced from the platform's restricted (logged-out) view: their views
    # miss the posts it hides there.
    limited: int

    @classmethod
    def of(cls, accounts: list[TrackedAccount], days: int) -> Self:
        changes = [c for a in accounts if (c := a.views(days)) is not None]
        bad = (AccountStatus.ERROR, AccountStatus.PRIVATE)
        return cls(
            accounts=len(accounts),
            followers=sum(a.followers or 0 for a in accounts),
            views=sum(c.views for c in changes) if changes else None,
            unreachable=sum(a.status in bad for a in accounts),
            limited=sum(bool(a.metrics and a.metrics.limited) for a in accounts),
        )


class AccountPage(BaseModel):
    """One page of the filtered, sorted list."""

    rows: list[TrackedAccount]
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
    def of(cls, accounts: list[TrackedAccount], page: int, per_page: int) -> Self:
        pages = max((len(accounts) + per_page - 1) // per_page, 1)
        page = min(page, pages)
        start = (page - 1) * per_page
        return cls(
            rows=accounts[start : start + per_page],
            total=len(accounts),
            page=page,
            pages=pages,
            per_page=per_page,
        )


class Snapshot(BaseModel):
    taken_at: float
    followers: int
    metrics: AccountMetrics


class DailyRow(BaseModel):
    """One day's last snapshot, and the follower change from the day
    before it (None for the first day)."""

    day: str
    snapshot: Snapshot
    delta: int | None


# Periods up to this many days chart every snapshot; longer ones one point
# per UTC day (its last snapshot).
CHART_RAW_MAX_DAYS = 2
# Most horizontal gridlines the y-axis draws (3-5 come out).
CHART_MAX_TICKS = 5
# Headroom above the highest and below the lowest value, as a share of the
# range, so neither touches the plot edge.
CHART_PAD = 0.08
# Most date labels under a daily chart; past that every n-th day.
CHART_MAX_X_LABELS = 8
_DAY_S = 86400


def _nice_step(raw: float) -> int:
    """The smallest 1/2/5 x 10^n step >= `raw`, at least 1."""
    if raw <= 1:
        return 1
    magnitude = 10 ** math.floor(math.log10(raw))
    return int(next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw))


def tick_label(value: int, step: int, top: int) -> str:
    """A y-axis label: "9,500" below 10k, "38.85k" below 1M, else "1.2M", with as
    many decimals as `step` needs to tell neighbours apart."""
    if value == 0:
        return "0"
    for unit, suffix, floor in ((1_000_000, "M", 1_000_000), (1_000, "k", 10_000)):
        if top >= floor:
            decimals = 0 if step >= unit else min(3, math.ceil(math.log10(unit / step)))
            return f"{value / unit:,.{decimals}f}{suffix}"
    return f"{value:,}"


class ChartPoint(BaseModel):
    """One sync on the follower chart: where it sits (percent of the plot,
    y from the top) and the change from the point before (None: first)."""

    taken_at: float
    followers: int
    change: int | None
    x: float
    y: float


class YTick(BaseModel):
    label: str
    y: float


class XTick(BaseModel):
    taken_at: float
    x: float


class FollowerChart(BaseModel):
    """Followers over a period, laid out for an inline SVG in a 0..100 box
    (stretched to the plot; labels and dots are HTML at the same percents).

    The first point is the newest sync a full period old when there is one,
    the Follower growth KPI's baseline, so start -> end is that KPI's
    change. x is linear in time: daily charts place each point on its UTC
    day (evenly spaced, the day's label right under it), the others at
    the sync's own time. Missed syncs read as gaps: two points further
    apart than `gap_s` are joined by a dashed connector (`gaps`) instead of
    the line (`lines`). The y-range is the data padded a little and rounded
    to 1/2/2.5/5 steps; a flat line sits mid-plot."""

    daily: bool
    points: list[ChartPoint] = []
    lines: list[str] = []
    gaps: list[str] = []
    dots: list[ChartPoint] = []
    y_ticks: list[YTick] = []
    x_ticks: list[XTick] = []

    @property
    def ready(self) -> bool:
        return len(self.points) >= 2  # noqa: PLR2004 -- a line needs two points

    @property
    def start(self) -> ChartPoint:
        return self.points[0]

    @property
    def end(self) -> ChartPoint:
        return self.points[-1]

    @property
    def span_days(self) -> int:
        """Calendar days (UTC) from the first point to the last."""
        return _day(self.end.taken_at) - _day(self.start.taken_at)

    @property
    def delta(self) -> int:
        return self.end.followers - self.start.followers

    @property
    def pct(self) -> float | None:
        return pct_change(self.end.followers, self.start.followers)

    @classmethod
    def of(cls, snaps: list[Snapshot], daily: bool, gap_s: float) -> Self:
        """`snaps` oldest first."""
        if len(snaps) < 2:  # noqa: PLR2004 -- a line needs two points
            return cls(
                daily=daily,
                points=[_point(s, None, 0, 0) for s in snaps],
            )
        values = [s.followers for s in snaps]
        low, high, step = _y_domain(min(values), max(values))
        # Where each point sits on the time axis: its UTC day's start
        # (daily), else the sync itself.
        slot = [_day(s.taken_at) * _DAY_S if daily else s.taken_at for s in snaps]
        t0 = slot[0]
        span_t = (slot[-1] - t0) or 1

        def x(t: float) -> float:
            return (t - t0) / span_t * 100

        def y(v: float) -> float:
            return (high - v) / (high - low) * 100

        points = [
            _point(
                s,
                s.followers - prev.followers if prev else None,
                x(t),
                y(s.followers),
            )
            for prev, s, t in zip([None, *snaps], snaps, slot, strict=False)
        ]
        runs: list[list[ChartPoint]] = [[points[0]]]
        gaps: list[str] = []
        for i, (prev, p) in enumerate(itertools.pairwise(points)):
            if slot[i + 1] - slot[i] > gap_s:
                gaps.append(f"M{prev.x:.2f} {prev.y:.2f}L{p.x:.2f} {p.y:.2f}")
                runs.append([p])
            else:
                runs[-1].append(p)
        # The ends, and any point a gap leaves alone (it has no line).
        dotted = {0, len(points) - 1} | {
            points.index(r[0]) for r in runs if len(r) == 1
        }
        return cls(
            daily=daily,
            points=points,
            lines=[
                "M" + "L".join(f"{p.x:.2f} {p.y:.2f}" for p in run)
                for run in runs
                if len(run) > 1
            ],
            gaps=gaps,
            dots=[points[i] for i in sorted(dotted)],
            y_ticks=[
                YTick(label=tick_label(v, step, high), y=y(v))
                for v in range(-(-low // step) * step, high + 1, step)
            ],
            x_ticks=(
                _day_ticks(t0, span_t)
                if daily
                else [XTick(taken_at=t0 + span_t * f, x=f * 100) for f in (0, 0.5, 1)]
            ),
        )


def _point(snap: Snapshot, change: int | None, x: float, y: float) -> ChartPoint:
    return ChartPoint(
        taken_at=snap.taken_at, followers=snap.followers, change=change, x=x, y=y
    )


def _day(t: float) -> int:
    return int(t // _DAY_S)


def _day_ticks(t0: float, span_t: float) -> list[XTick]:
    """A label under every day of a daily chart, every n-th past
    CHART_MAX_X_LABELS, counted back from the last day so it is always
    labelled."""
    days = max(1, round(span_t / _DAY_S))
    every = -(-(days + 1) // CHART_MAX_X_LABELS)
    return [
        XTick(taken_at=t0 + d * _DAY_S, x=d / days * 100)
        for d in range(days % every, days + 1, every)
    ]


def _steps() -> Iterator[int]:
    """1, 2, 5, 10, 20, 25, 50, 100, ... -- gridline steps that read well."""
    yield from (1, 2, 5)
    for magnitude in itertools.count(1):
        yield from (int(m * 10**magnitude) for m in (1, 2, 2.5, 5))


def _y_domain(low: int, high: int) -> tuple[int, int, int]:
    """(bottom, top, step): `low..high` padded by CHART_PAD of the range
    (never below 0), with the coarsest-needed step that still draws at most
    CHART_MAX_TICKS gridlines, and the ends rounded to a quarter or half
    step so the data fills the plot. A flat line gets one step either
    side."""
    if low == high:
        step = _nice_step(max(1, high * 0.02))
        bottom = max(0, low - step)
        return bottom, bottom + 2 * step, step
    pad = (high - low) * CHART_PAD
    lo, hi = max(0.0, low - pad) if low >= 0 else low - pad, high + pad
    return next(fit for step in _steps() if (fit := _y_fit(lo, hi, step)))


def _y_fit(lo: float, hi: float, step: int) -> tuple[int, int, int] | None:
    """`lo..hi` rounded out to a quarter (else half, else whole) `step`, or
    None when that draws more than CHART_MAX_TICKS gridlines."""
    unit = next((step // n for n in (4, 2) if step % n == 0), step)
    bottom = math.floor(lo / unit) * unit
    top = math.ceil(hi / unit) * unit
    if top // step - -(-bottom // step) + 1 > CHART_MAX_TICKS:
        return None
    return bottom, top, step


class AccountHistory(BaseModel):
    snapshots: list[Snapshot]

    @property
    def daily(self) -> list[DailyRow]:
        """Oldest day first."""
        by_day: dict[str, Snapshot] = {}
        for snap in self.snapshots:
            by_day[time.strftime("%Y-%m-%d", time.gmtime(snap.taken_at))] = snap
        rows: list[DailyRow] = []
        previous: Snapshot | None = None
        for day, snap in by_day.items():
            delta = snap.followers - previous.followers if previous else None
            rows.append(DailyRow(day=day, snapshot=snap, delta=delta))
            previous = snap
        return rows

    def follower_chart(
        self, days: int, rescan_s: float, now: float | None = None
    ) -> FollowerChart:
        """Followers over the last `days` days, from the newest snapshot a
        period old (the growth baseline) when there is one: every sync for
        short periods, else each day's last. A gap is more than two rescan
        intervals (or a day and a half, daily) without a point."""
        since = (now or time.time()) - days * _DAY_S
        base = [s for s in self.snapshots if s.taken_at <= since][-1:]
        inside = [s for s in self.snapshots if s.taken_at > since]
        daily = days > CHART_RAW_MAX_DAYS
        if daily:
            # One point per UTC day, its last sync; the baseline's day too
            # (a sync later that day replaces it).
            by_day = {_day(s.taken_at): s for s in base + inside}
            base, inside = [], list(by_day.values())
        gap_s = max(2 * rescan_s, 1.5 * _DAY_S if daily else 0)
        return FollowerChart.of(base + inside, daily=daily, gap_s=gap_s)


class AddAccounts(BaseModel):
    """The Add accounts dialog: handles of one platform, one per line ("name",
    "@name", a profile URL; anything after the handle ignored), all going
    to `creator` when one is given."""

    platform: str
    handles: str
    creator: CreatorName = ""

    def parsed(self) -> list[str]:
        """The stored handles, deduped, in order. Raises ValueError naming
        the lines that are not one, so a bad paste adds nothing."""
        if self.platform not in platforms():
            msg = f"Unknown platform: {self.platform}"
            raise ValueError(msg)
        platform = get_platform(self.platform)
        handles: list[str] = []
        bad: list[str] = []
        for line in self.handles.splitlines():
            words = line.split()
            if not words:
                continue
            try:
                handles.append(platform.handle(words[0]))
            except ValueError:
                bad.append(line.strip())
        if bad:
            msg = f"Not a {platform.label} account: " + ", ".join(bad[:5])
            raise ValueError(msg)
        return list(dict.fromkeys(handles))


class AddResult(BaseModel):
    added: list[str]
    existing: int


class AssignCreator(BaseModel):
    ids: list[int] = Field(min_length=1)
    creator: CreatorName = ""


class SelectedAccounts(BaseModel):
    ids: list[int] = Field(min_length=1)


class Notes(BaseModel):
    notes: str = Field(default="", max_length=5000)
