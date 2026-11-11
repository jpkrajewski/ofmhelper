"""Tracked social accounts, the plain side every layer shares: what a scan
of one account says (`AccountStats` and its parts), and each platform's
naming rules (`PlatformNames`: its stored key, its label, how a pasted handle
is reduced and where its profile page is). The scanning side (proxies,
clients, sources) is `integrations/socials`, whose `Platform` classes extend
these names."""

from abc import ABC, abstractmethod
from collections.abc import Sequence
from functools import lru_cache
from typing import ClassVar, Self

from pydantic import BaseModel, computed_field

from ofmhelpers.schemas.instagram import ProfileHandle
from ofmhelpers.utils.registry import Registry


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
    # The source that answered (`sources.AccountSource.key`); "" before sources.
    source: str = ""


class AccountStats(BaseModel):
    """A platform's whole answer for one account: newest posts first.
    `complete`: the read went past the oldest post it could see (no cap
    reached). `since`: an incomplete read that still has every post from
    then on (a source reading only the last N days); None: from its oldest
    post. `limited`: the platform showed a restricted view (logged out), so
    posts it hides from it are missing from the numbers. `source`: who
    answered."""

    profile: AccountProfile
    posts: list[PostStat] = []
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
        """The publish time this read has every post back to: 0 when
        complete, else its oldest post (none read: only from now on)."""
        if self.complete:
            return 0.0
        if self.since is not None:
            # Only that far was asked for: an older post it brought along (a
            # pinned one) says nothing about the posts between.
            return self.since
        return min((p.posted_at for p in self.posts), default=self.scanned_at)

    def metrics(self) -> AccountMetrics:
        return AccountMetrics(
            followers=self.profile.followers,
            following=self.profile.following,
            posts_scanned=len(self.posts),
            views_scanned=self.views_scanned,
            limited=self.limited,
            source=self.source,
        )


# --- naming rules -------------------------------------------------------------


class PlatformNames(ABC):
    """A platform as text: nothing here talks to it."""

    # Stored on every tracked account; never rename one in use.
    key: ClassVar[str]
    label: ClassVar[str]

    @abstractmethod
    def handle(self, text: str) -> str:
        """`text` as pasted ("name", "@name", a profile URL) reduced to the
        stored handle. Raises ValueError when it is none."""

    @abstractmethod
    def profile_url(self, handle: str) -> str: ...


class InstagramNames(PlatformNames):
    key = "instagram"
    label = "Instagram"

    def handle(self, text: str) -> str:
        return ProfileHandle(username=text).username

    def profile_url(self, handle: str) -> str:
        return f"https://www.instagram.com/{handle}/"


# Display order of the platform pickers; `integrations/socials/platforms`
# has the scanning class of each (`tests/unit/socials` keeps the two in step).
_NAMES: tuple[type[PlatformNames], ...] = (InstagramNames,)


@lru_cache(maxsize=1)
def platform_names() -> Registry[PlatformNames]:
    """Every platform's naming rules by its stored key."""
    return Registry[PlatformNames](entries={cls.key: cls() for cls in _NAMES})


def names_of(key: str) -> PlatformNames:
    """Raises KeyError for a platform not (or no longer) registered."""
    return platform_names()[key]
