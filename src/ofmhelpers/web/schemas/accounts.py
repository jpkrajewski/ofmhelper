"""/accounts: a tracked social account with its numbers for a reporting
period, the list's query (search, filters, sort, period, paging), a page of
it and its totals, the analytics page's history and top posts, and what the
page posts.

Metric rules, everywhere: followers are a point-in-time total; views are
per period (posts published in it, compared with the period before). A
number is shown only from data that covers it: 0 is a confirmed zero, None
(a dash) is "no data for that yet", a failed sync is its own warning."""

import time
from collections.abc import Callable, Mapping
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
        return f"Last {self.days} days"

    @property
    def short(self) -> str:
        return f"{self.days}d"


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
    platform: str
    handle: str
    creator: str | None = None
    notes: str = ""
    added_at: float
    scanned_at: float | None = None
    error: str | None = None
    # None until the first scan succeeds.
    profile: AccountProfile | None = None
    metrics: AccountMetrics | None = None
    # Per period, the newest snapshot at least that old.
    baselines: list[Baseline] = []
    first: Baseline | None = None
    latest: Baseline | None = None
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

    def views(self, days: int) -> ViewsChange | None:
        if self.metrics is None or (views := self.metrics.views(days)) is None:
            return None
        return ViewsChange(views=views, previous=self.metrics.previous_views(days))

    def posts_in(self, days: int) -> int | None:
        return self.metrics.posts(days) if self.metrics else None

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
    """The list's query string. `period` None: the first configured one."""

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


class Sparkline(BaseModel):
    """An inline SVG polyline over `values` in a `width` x `height` box,
    with the range it spans."""

    width: int = 600
    height: int = 120
    points: str = ""
    low: int = 0
    high: int = 0

    @classmethod
    def of(cls, values: list[int], width: int = 600, height: int = 120) -> Self:
        if len(values) < 2:  # noqa: PLR2004 -- a line needs two points
            return cls(width=width, height=height)
        low, high = min(values), max(values)
        span = (high - low) or 1
        step = width / (len(values) - 1)
        points = " ".join(
            f"{i * step:.1f},{height - (v - low) / span * height:.1f}"
            for i, v in enumerate(values)
        )
        return cls(width=width, height=height, points=points, low=low, high=high)


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

    def last_days(self, days: int, now: float | None = None) -> list[DailyRow]:
        """The daily rows of the last `days` days, oldest first."""
        since = (now or time.time()) - days * 86400
        return [r for r in self.daily if r.snapshot.taken_at >= since]

    def followers_line(self, days: int) -> Sparkline:
        """Daily follower totals over the last `days` days."""
        return Sparkline.of([r.snapshot.followers for r in self.last_days(days)])


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
