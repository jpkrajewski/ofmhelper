"""What every platform's scan of one account comes back as, whatever the
platform: the profile, the newest posts it could read with their views, and
the numbers the /accounts list and history are made of."""

from collections.abc import Sequence
from typing import Self

from pydantic import BaseModel, computed_field


class AccountProfile(BaseModel):
    """The account itself, as of the scan."""

    handle: str
    # The platform's own id for the account, stable across handle renames
    # ("" where the platform has none): a renamed account is matched to
    # the one already tracked by it.
    account_id: str = ""
    display_name: str = ""
    followers: int
    # None where the platform does not show it.
    following: int | None = None
    is_private: bool = False
    is_verified: bool = False
    # A platform CDN URL; may be signed and expire, so only the latest
    # scan's is shown.
    avatar_url: str = ""


class PostViews(BaseModel):
    """A post's views and when it was published: all a view window needs."""

    views: int
    posted_at: float


class PostStat(PostViews):
    """One post (reel, video, ...) of the scan, with its views then."""

    id: str
    url: str
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

    @classmethod
    def of(
        cls,
        posts: Sequence[PostViews],
        days: int,
        as_of: float,
        covered_since: float | None,
    ) -> Self:
        """The window ending `as_of` over an account's kept `posts`, which
        are complete from `covered_since` on (None: from nowhere yet)."""
        since = as_of - days * 86400
        recent = [p for p in posts if p.posted_at >= since]
        return cls(
            days=days,
            views=sum(p.views for p in recent),
            posts=len(recent),
            covered=covered_since is not None and covered_since <= since,
        )


class AccountMetrics(BaseModel):
    """One scan as numbers: stored with every snapshot (the history) and on
    the account (the list). View windows are not stored: any period is
    derived on read from the account's kept posts (`ViewWindow.of`), so a
    new reporting period needs no rescan."""

    followers: int
    following: int | None = None
    posts_scanned: int = 0
    views_scanned: int = 0
    limited: bool = False


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

    @property
    def reach(self) -> float:
        """The publish time this read has every post back to: 0 when
        complete, else its oldest post (none read: only from now on)."""
        if self.complete:
            return 0.0
        return min((p.posted_at for p in self.posts), default=self.scanned_at)

    def metrics(self) -> AccountMetrics:
        return AccountMetrics(
            followers=self.profile.followers,
            following=self.profile.following,
            posts_scanned=len(self.posts),
            views_scanned=self.views_scanned,
            limited=self.limited,
        )
