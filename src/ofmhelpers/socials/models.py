"""What every platform's scan of one account comes back as, whatever the
platform: the profile, the newest posts it could read with their views, and
the numbers the /accounts list and history are made of."""

from pydantic import BaseModel, computed_field


class AccountProfile(BaseModel):
    """The account itself, as of the scan."""

    handle: str
    display_name: str = ""
    followers: int
    # None where the platform does not show it.
    following: int | None = None
    is_private: bool = False
    is_verified: bool = False
    # A platform CDN URL; may be signed and expire, so only the latest
    # scan's is shown.
    avatar_url: str = ""


class PostStat(BaseModel):
    """One post (reel, video, ...) of the scan, with its views then."""

    id: str
    url: str
    views: int
    posted_at: float
    # Expiring CDN URL like the avatar; "" where the platform has none.
    thumbnail_url: str = ""


class ViewWindow(BaseModel):
    """Views of the posts published in the last `days` days, as of a scan.
    `covered`: the scan read back past the window's start (or every post
    there is) -- otherwise the numbers are a floor, not the total."""

    days: int
    views: int
    posts: int
    covered: bool = True


class AccountMetrics(BaseModel):
    """One scan as numbers: stored with every snapshot (the history) and on
    the account (the list). Posts themselves are not kept per snapshot.
    Windows are kept for each reporting period and its double, so a period
    compares against the one before it."""

    followers: int
    following: int | None = None
    posts_scanned: int = 0
    views_scanned: int = 0
    limited: bool = False
    windows: list[ViewWindow] = []

    def window(self, days: int) -> ViewWindow | None:
        """None when not computed or not fully covered by the scan."""
        found = next((w for w in self.windows if w.days == days), None)
        return found if found and found.covered else None

    def views(self, days: int) -> int | None:
        found = self.window(days)
        return found.views if found else None

    def posts(self, days: int) -> int | None:
        found = self.window(days)
        return found.posts if found else None

    def previous_views(self, days: int) -> int | None:
        """Views of the posts published in the `days` before the last `days`."""
        both = self.window(2 * days)
        last = self.window(days)
        return both.views - last.views if both and last else None


class AccountStats(BaseModel):
    """A platform's whole answer for one account: newest posts first.
    `complete`: the read went past the oldest post it could see (no cap
    reached). `limited`: the platform showed a restricted view (logged
    out), so posts it hides from it are missing from the numbers."""

    profile: AccountProfile
    posts: list[PostStat] = []
    complete: bool = True
    limited: bool = False
    scanned_at: float

    @computed_field  # type: ignore[prop-decorator]
    @property
    def views_scanned(self) -> int:
        return sum(p.views for p in self.posts)

    def metrics(self, periods_days: list[int]) -> AccountMetrics:
        oldest = min((p.posted_at for p in self.posts), default=self.scanned_at)
        windows = []
        for days in sorted({*periods_days, *(2 * d for d in periods_days)}):
            since = self.scanned_at - days * 86400
            recent = [p for p in self.posts if p.posted_at >= since]
            windows.append(
                ViewWindow(
                    days=days,
                    views=sum(p.views for p in recent),
                    posts=len(recent),
                    covered=self.complete or oldest <= since,
                )
            )
        return AccountMetrics(
            followers=self.profile.followers,
            following=self.profile.following,
            posts_scanned=len(self.posts),
            views_scanned=self.views_scanned,
            limited=self.limited,
            windows=windows,
        )
