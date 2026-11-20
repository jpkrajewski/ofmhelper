from abc import ABC, abstractmethod
from collections.abc import Sequence
from functools import lru_cache
from typing import ClassVar, Self

from pydantic import BaseModel, computed_field

from ofmhelpers.schemas.instagram import ProfileHandle
from ofmhelpers.utils.registry import Registry


class AccountProfile(BaseModel):
    handle: str
    account_id: str = ""
    display_name: str = ""
    followers: int
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
    limited: bool = False
    source: str = ""


class AccountStats(BaseModel):
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


_NAMES: tuple[type[PlatformNames], ...] = (InstagramNames,)


@lru_cache(maxsize=1)
def platform_names() -> Registry[PlatformNames]:
    return Registry[PlatformNames](entries={cls.key: cls() for cls in _NAMES})


def names_of(key: str) -> PlatformNames:
    return platform_names()[key]
