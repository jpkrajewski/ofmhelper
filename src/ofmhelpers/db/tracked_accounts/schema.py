import itertools
import math
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator, Mapping, Sequence
from enum import StrEnum
from functools import lru_cache
from types import MappingProxyType
from typing import Annotated, Any, ClassVar, Self
from urllib.parse import urlencode, urlsplit

from pydantic import (
    AfterValidator,
    BaseModel,
    Field,
    computed_field,
    model_validator,
)

from ofmhelpers.db.watched_profiles.schema import ProfileHandle


class AccountProfile(BaseModel):
    handle: str
    account_id: str = ""
    display_name: str = ""
    followers: int
    followers_hidden: bool = False
    following: int | None = None
    is_private: bool = False
    is_verified: bool = False
    avatar_url: str = ""


class PostViews(BaseModel):
    views: int
    posted_at: float


class PostStat(PostViews):
    id: str
    url: str
    thumbnail_url: str = ""


class ViewWindow(BaseModel):
    days: int
    views: int
    posts: int
    covered: bool = True

    @classmethod
    def of(
        cls,
        posts: Sequence[PostViews],
        days: int,
        as_of: float,
        covered_since: float | None,
    ) -> Self:
        since = as_of - days * 86400
        recent = [p for p in posts if p.posted_at >= since]
        return cls(
            days=days,
            views=sum(p.views for p in recent),
            posts=len(recent),
            covered=covered_since is not None and covered_since <= since,
        )


class AccountMetrics(BaseModel):
    followers: int
    following: int | None = None
    posts_scanned: int = 0
    views_scanned: int = 0
    hidden_posts: int = 0
    limited: bool = False
    source: str = ""


class AccountStats(BaseModel):
    profile: AccountProfile
    posts: list[PostStat] = []
    hidden_posts: int = 0
    complete: bool = True
    since: float | None = None
    limited: bool = False
    source: str = ""
    scanned_at: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def views_scanned(self) -> int:
        return sum(p.views for p in self.posts)

    @property
    def reach(self) -> float:
        if self.complete:
            return 0.0
        if self.since is not None:
            return self.since
        return min((p.posted_at for p in self.posts), default=self.scanned_at)

    def metrics(self) -> AccountMetrics:
        return AccountMetrics(
            followers=self.profile.followers,
            following=self.profile.following,
            posts_scanned=len(self.posts),
            views_scanned=self.views_scanned,
            hidden_posts=self.hidden_posts,
            limited=self.limited,
            source=self.source,
        )


class PlatformNames(ABC):
    key: ClassVar[str]
    label: ClassVar[str]

    @abstractmethod
    def handle(self, text: str) -> str: ...

    @abstractmethod
    def profile_url(self, handle: str) -> str: ...


class InstagramNames(PlatformNames):
    key = "instagram"
    label = "Instagram"

    def handle(self, text: str) -> str:
        return ProfileHandle(username=text).username

    def profile_url(self, handle: str) -> str:
        return f"https://www.instagram.com/{handle}/"


class _HandleRule(BaseModel, frozen=True):
    label: str
    hosts: tuple[str, ...]
    pattern: re.Pattern[str]

    def bare(self, text: str) -> str:
        value = text.strip()
        if any(host in value.lower() for host in self.hosts):
            path = urlsplit(value if "//" in value else f"https://{value}").path
            parts = [p for p in path.split("/") if p and p.lower() != "add"]
            value = parts[0] if parts else ""
        value = value.lstrip("@").strip("/")
        if not self.pattern.fullmatch(value):
            msg = f"Not a valid {self.label} username: {text!r}"
            raise ValueError(msg)
        return value.lower()


_SNAPCHAT = _HandleRule(
    label="Snapchat",
    hosts=("snapchat.com",),
    pattern=re.compile(r"[A-Za-z][A-Za-z0-9._-]{2,14}"),
)
_X = _HandleRule(
    label="X",
    hosts=("x.com", "twitter.com"),
    pattern=re.compile(r"[A-Za-z0-9_]{1,15}"),
)


class SnapchatNames(PlatformNames):
    key = "snapchat"
    label = "Snapchat"

    def handle(self, text: str) -> str:
        return _SNAPCHAT.bare(text)

    def profile_url(self, handle: str) -> str:
        return f"https://www.snapchat.com/@{handle}"


class XNames(PlatformNames):
    key = "x"
    label = "X"

    def handle(self, text: str) -> str:
        return _X.bare(text)

    def profile_url(self, handle: str) -> str:
        return f"https://x.com/{handle}"


_NAMES: tuple[type[PlatformNames], ...] = (InstagramNames, SnapchatNames, XNames)


@lru_cache(maxsize=1)
def platform_names() -> MappingProxyType[str, PlatformNames]:
    return MappingProxyType({cls.key: cls() for cls in _NAMES})


def names_of(key: str) -> PlatformNames:
    return platform_names()[key]


NO_CREATOR = "(none)"


def _creator_name(value: str) -> str:
    name = " ".join(value.split())[:64]
    if name.startswith("("):
        msg = "A creator name cannot start with '('"
        raise ValueError(msg)
    return name


CreatorName = Annotated[str, AfterValidator(_creator_name)]


def ago(ts: float, now: float | None = None) -> str:
    seconds = max((now or time.time()) - ts, 0)
    for unit, size in (("d", 86400), ("h", 3600), ("min", 60)):
        if seconds >= size:
            return f"{int(seconds // size)} {unit} ago"
    return "just now"


def pct_change(now: int, before: int) -> float | None:
    return (now - before) / before * 100 if before else None


class Period(BaseModel):
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
    days: int
    followers: int
    taken_at: float


class Growth(BaseModel):
    delta: int
    pct: float | None
    since: float
    full: bool


class ViewsChange(BaseModel):
    views: int
    previous: int | None

    @property
    def pct(self) -> float | None:
        return None if self.previous is None else pct_change(self.views, self.previous)


class TopPosts(BaseModel):
    posts: list[PostStat]
    in_period: bool

    @property
    def max_views(self) -> int:
        return max((p.views for p in self.posts), default=0)


class TrackedAccount(BaseModel):
    id: int
    user_id: int
    platform: str
    handle: str
    creator: str | None = None
    notes: str = ""
    added_at: float
    scanned_at: float | None = None
    claimed_at: float | None = None
    syncing: bool = False
    untracked_at: float | None = None
    error: str | None = None
    profile: AccountProfile | None = None
    metrics: AccountMetrics | None = None
    baselines: list[Baseline] = []
    first: Baseline | None = None
    last_scan_at: float | None
    post_views: list[PostViews] = []
    covered_since: float | None = None
    posts: list[PostStat] = []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def status(self) -> AccountStatus:
        if self.error:
            return AccountStatus.ERROR
        if self.profile is None:
            return AccountStatus.SCANNING
        if self.profile.is_private:
            return AccountStatus.PRIVATE
        return AccountStatus.ACTIVE

    @property
    def profile_url(self) -> str:
        return names_of(self.platform).profile_url(self.handle)

    @property
    def name(self) -> str:
        return (self.profile.display_name if self.profile else "") or self.handle

    @property
    def platform_label(self) -> str:
        return names_of(self.platform).label

    @property
    def followers_hidden(self) -> bool:
        return bool(self.profile and self.profile.followers_hidden)

    @property
    def hidden_posts(self) -> int:
        return self.metrics.hidden_posts if self.metrics else 0

    @property
    def followers(self) -> int | None:
        if self.metrics is None or self.followers_hidden:
            return None
        return self.metrics.followers

    def growth(self, days: int) -> Growth | None:
        if self.metrics is None or self.followers_hidden:
            return None
        now = self.metrics.followers
        base = next((b for b in self.baselines if b.days == days), None)
        if base is not None:
            full = True
        elif (
            self.first and self.last_scan_at and self.first.taken_at < self.last_scan_at
        ):
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
        if self.metrics is None or self.last_scan_at is None:
            return None
        found = ViewWindow.of(
            self.post_views, days, self.last_scan_at, self.covered_since
        )
        return found if found.covered else None

    def views(self, days: int) -> ViewsChange | None:
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
    q: str = Field(default="", max_length=64)
    platform: str = Field(default=InstagramNames.key, max_length=32)
    creator: str = Field(default="", max_length=64)
    status: AccountStatus | None = None
    sort: AccountSort = AccountSort.FOLLOWERS
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
        if self.order is not None:
            return self.order
        return SortOrder.ASC if self.sort == AccountSort.HANDLE else SortOrder.DESC

    def sort_link(self, sort: AccountSort) -> str:
        if sort != self.sort:
            return self.link(sort=sort, order=None)
        flipped = SortOrder.ASC if self.direction == SortOrder.DESC else SortOrder.DESC
        return self.link(order=flipped)

    def ordered(
        self, accounts: list[TrackedAccount], days: int
    ) -> list[TrackedAccount]:

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
        update = {"page": 1, **changes}
        data = self.model_copy(update=update).model_dump(mode="json")
        defaults = AccountQuery().model_dump(mode="json")
        params = {
            k: v for k, v in data.items() if v not in (None, "") and v != defaults[k]
        }
        return path + (f"?{urlencode(params)}" if params else "")


class AccountSummary(BaseModel):
    accounts: int
    followers: int
    followers_hidden: int
    views: int | None
    hidden_posts: int
    unreachable: int
    limited: int

    @classmethod
    def of(cls, accounts: list[TrackedAccount], days: int) -> Self:
        changes = [c for a in accounts if (c := a.views(days)) is not None]
        bad = (AccountStatus.ERROR, AccountStatus.PRIVATE)
        return cls(
            accounts=len(accounts),
            followers=sum(a.followers or 0 for a in accounts),
            followers_hidden=sum(a.followers_hidden for a in accounts),
            views=sum(c.views for c in changes) if changes else None,
            hidden_posts=sum(a.hidden_posts for a in accounts),
            unreachable=sum(a.status in bad for a in accounts),
            limited=sum(bool(a.metrics and a.metrics.limited) for a in accounts),
        )


class AccountPage(BaseModel):
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
    day: str
    snapshot: Snapshot
    delta: int | None


CHART_RAW_MAX_DAYS = 2
CHART_MAX_TICKS = 5
CHART_PAD = 0.08
CHART_MAX_X_LABELS = 8
_DAY_S = 86400


def _nice_step(raw: float) -> int:
    if raw <= 1:
        return 1
    magnitude = 10 ** math.floor(math.log10(raw))
    return int(next(m * magnitude for m in (1, 2, 5, 10) if m * magnitude >= raw))


def tick_label(value: int, step: int, top: int) -> str:
    if value == 0:
        return "0"
    for unit, suffix, floor in ((1_000_000, "M", 1_000_000), (1_000, "k", 10_000)):
        if top >= floor:
            decimals = 0 if step >= unit else min(3, math.ceil(math.log10(unit / step)))
            return f"{value / unit:,.{decimals}f}{suffix}"
    return f"{value:,}"


class ChartPoint(BaseModel):
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
    daily: bool
    points: list[ChartPoint] = []
    lines: list[str] = []
    gaps: list[str] = []
    dots: list[ChartPoint] = []
    y_ticks: list[YTick] = []
    x_ticks: list[XTick] = []

    @property
    def ready(self) -> bool:
        return len(self.points) >= 2  # noqa: PLR2004

    @property
    def start(self) -> ChartPoint:
        return self.points[0]

    @property
    def end(self) -> ChartPoint:
        return self.points[-1]

    @property
    def span_days(self) -> int:
        return _day(self.end.taken_at) - _day(self.start.taken_at)

    @property
    def delta(self) -> int:
        return self.end.followers - self.start.followers

    @property
    def pct(self) -> float | None:
        return pct_change(self.end.followers, self.start.followers)

    @classmethod
    def of(cls, snaps: list[Snapshot], daily: bool, gap_s: float) -> Self:
        if len(snaps) < 2:  # noqa: PLR2004
            return cls(
                daily=daily,
                points=[_point(s, None, 0, 0) for s in snaps],
            )
        values = [s.followers for s in snaps]
        axis = _y_domain(min(values), max(values))
        low, high, step = axis.low, axis.high, axis.step
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
    days = max(1, round(span_t / _DAY_S))
    every = -(-(days + 1) // CHART_MAX_X_LABELS)
    return [
        XTick(taken_at=t0 + d * _DAY_S, x=d / days * 100)
        for d in range(days % every, days + 1, every)
    ]


def _steps() -> Iterator[int]:
    yield from (1, 2, 5)
    for magnitude in itertools.count(1):
        yield from (int(m * 10**magnitude) for m in (1, 2, 2.5, 5))


class _YAxis(BaseModel, frozen=True):
    low: int
    high: int
    step: int


def _y_domain(low: int, high: int) -> _YAxis:
    if low == high:
        step = _nice_step(max(1, high * 0.02))
        bottom = max(0, low - step)
        return _YAxis(low=bottom, high=bottom + 2 * step, step=step)
    pad = (high - low) * CHART_PAD
    lo, hi = max(0.0, low - pad) if low >= 0 else low - pad, high + pad
    return next(fit for step in _steps() if (fit := _y_fit(lo, hi, step)))


def _y_fit(lo: float, hi: float, step: int) -> _YAxis | None:
    unit = next((step // n for n in (4, 2) if step % n == 0), step)
    bottom = math.floor(lo / unit) * unit
    top = math.ceil(hi / unit) * unit
    if top // step - -(-bottom // step) + 1 > CHART_MAX_TICKS:
        return None
    return _YAxis(low=bottom, high=top, step=step)


class AccountHistory(BaseModel):
    snapshots: list[Snapshot]

    @property
    def daily(self) -> list[DailyRow]:
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
        since = (now or time.time()) - days * _DAY_S
        base = [s for s in self.snapshots if s.taken_at <= since][-1:]
        inside = [s for s in self.snapshots if s.taken_at > since]
        daily = days > CHART_RAW_MAX_DAYS
        if daily:
            by_day = {_day(s.taken_at): s for s in base + inside}
            base, inside = [], list(by_day.values())
        gap_s = max(2 * rescan_s, 1.5 * _DAY_S if daily else 0)
        return FollowerChart.of(base + inside, daily=daily, gap_s=gap_s)


class AddAccounts(BaseModel):
    platform: str
    handles: str
    creator: CreatorName = ""

    def parsed(self) -> list[str]:
        if self.platform not in platform_names():
            msg = f"Unknown platform: {self.platform}"
            raise ValueError(msg)
        platform = names_of(self.platform)
        handles: list[str] = []
        bad: list[str] = []
        for line in self.handles.splitlines():
            words = line.split()
            if not words:
                continue
            try:
                handles.append(platform.handle(words[0]))
            except ValueError:  # silent: a bad line is listed back
                bad.append(line.strip())
        if bad:
            msg = f"Not a valid {platform.label} account: " + ", ".join(bad[:5])
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
