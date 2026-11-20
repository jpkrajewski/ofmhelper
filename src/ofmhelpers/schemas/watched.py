from typing import Annotated

from pydantic import AfterValidator, BaseModel, ValidationError

from ofmhelpers.schemas.instagram import ProfileHandle, ProfileReels, Reel


class WatchedProfile(BaseModel):
    user_id: int
    username: str
    group: str | None = None
    added_at: float
    scanned_at: float | None = None
    error: str | None = None
    result: ProfileReels | None = None

    @property
    def pending(self) -> bool:
        return self.scanned_at is None

    @property
    def views_total(self) -> int:
        return self.result.views_total if self.result else 0

    @classmethod
    def most_viewed(cls, profiles: list[WatchedProfile]) -> str | None:
        top = max(profiles, key=lambda p: p.views_total, default=None)
        return top.username if top and top.views_total else None

    @classmethod
    def most_reels(
        cls, profiles: list[WatchedProfile], excluding: str | None
    ) -> str | None:
        rest = [p for p in profiles if p.username != excluding]
        top = max(rest, key=lambda p: (p.reel_count, p.views_total), default=None)
        return top.username if top and top.reel_count else None

    @property
    def reel_count(self) -> int:
        return len(self.result.reels) if self.result else 0

    @classmethod
    def by_reels(cls, profiles: list[WatchedProfile]) -> list[WatchedProfile]:

        def key(p: WatchedProfile) -> tuple[bool, int, int]:
            views = sum(r.views for r in p.result.reels) if p.result else 0
            return (p.pending, p.reel_count, views)

        return sorted(profiles, key=key, reverse=True)


class Leaderboard(BaseModel):
    rows: list[WatchedProfile]
    top_views: str | None
    top_reels: str | None

    @classmethod
    def of(cls, profiles: list[WatchedProfile]) -> Leaderboard:
        top_views = WatchedProfile.most_viewed(profiles)
        top_reels = WatchedProfile.most_reels(profiles, top_views)
        leaders = [u for u in (top_views, top_reels) if u]
        by_name = {p.username: p for p in profiles}
        rest = [p for p in profiles if p.username not in leaders]
        return cls(
            rows=[by_name[u] for u in leaders] + WatchedProfile.by_reels(rest),
            top_views=top_views,
            top_reels=top_reels,
        )


class FeedItem(BaseModel):
    username: str
    reel: Reel

    @classmethod
    def of(cls, profiles: list[WatchedProfile]) -> list[FeedItem]:
        items = [
            cls(username=p.username, reel=reel)
            for p in profiles
            if p.result
            for reel in p.result.reels
        ]
        items.sort(key=lambda i: i.reel.views, reverse=True)
        return items


def _group_name(value: str) -> str:
    return " ".join(value.split()).lower()[:64]


GroupName = Annotated[str, AfterValidator(_group_name)]


class GroupForm(BaseModel):
    group: GroupName = ""


class WatchForm(BaseModel):
    profiles: str
    group: GroupName = ""

    @property
    def usernames(self) -> list[str]:
        handles: list[str] = []
        bad: list[str] = []
        for line in self.profiles.splitlines():
            first = line.split()[0] if line.split() else ""
            if not first:
                continue
            try:
                handles.append(ProfileHandle(username=first).username)
            except ValidationError:  # silent: a bad line is listed back
                bad.append(line.strip())
        if bad:
            msg = "Not an Instagram profile: " + ", ".join(bad[:5])
            raise ValueError(msg)
        return list(dict.fromkeys(handles))
