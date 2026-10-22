"""An Instagram profile /reels keeps rescanning, with its latest scan; the
feed /reels shows (every watched profile's reels as one list); and what the
page posts to add profiles."""

from typing import Annotated

from pydantic import AfterValidator, BaseModel, ValidationError

from ofmhelpers.instagram import ProfileHandle, ProfileReels, Reel


class WatchedProfile(BaseModel):
    # Whose watch list it is on.
    user_id: int
    username: str
    group: str | None = None
    added_at: float
    scanned_at: float | None = None
    error: str | None = None
    # None until the first scan succeeds.
    result: ProfileReels | None = None

    @property
    def pending(self) -> bool:
        """Added, first scan not finished yet."""
        return self.scanned_at is None

    @property
    def views_total(self) -> int:
        """Views of all its reels of the last scan's window (two weeks)."""
        return self.result.views_total if self.result else 0

    @classmethod
    def most_viewed(cls, profiles: list["WatchedProfile"]) -> str | None:
        """The username with the most window views -- the legendary row --
        or None while no profile has any."""
        top = max(profiles, key=lambda p: p.views_total, default=None)
        return top.username if top and top.views_total else None

    @classmethod
    def most_reels(
        cls, profiles: list["WatchedProfile"], excluding: str | None
    ) -> str | None:
        """The username with the most feed reels besides `excluding` (the
        most viewed, which already leads), window views breaking a tie; None
        while no other profile has any."""
        rest = [p for p in profiles if p.username != excluding]
        top = max(rest, key=lambda p: (p.reel_count, p.views_total), default=None)
        return top.username if top and top.reel_count else None

    @property
    def reel_count(self) -> int:
        """Reels in the feed from the last scan (past every filter); 0 before
        the first."""
        return len(self.result.reels) if self.result else 0

    @classmethod
    def by_reels(cls, profiles: list["WatchedProfile"]) -> list["WatchedProfile"]:
        """The watch list's order: ones still on their first scan on top (so
        an add shows up), then the most feed reels first, total views of
        those breaking a tie."""

        def key(p: "WatchedProfile") -> tuple[bool, int, int]:
            views = sum(r.views for r in p.result.reels) if p.result else 0
            return (p.pending, p.reel_count, views)

        return sorted(profiles, key=key, reverse=True)


class Leaderboard(BaseModel):
    """The watch list as /reels shows it, for whichever group is filtered:
    the most viewed profile first, then the one with the most feed reels,
    both outlined and labelled; the rest in `WatchedProfile.by_reels` order."""

    rows: list[WatchedProfile]
    top_views: str | None
    top_reels: str | None

    @classmethod
    def of(cls, profiles: list[WatchedProfile]) -> "Leaderboard":
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
    """One reel card on /reels: the reel and whose it is."""

    username: str
    reel: Reel

    @classmethod
    def of(cls, profiles: list[WatchedProfile]) -> list["FeedItem"]:
        """Every profile's reels, most viewed first."""
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


# A group label as stored: trimmed, single-spaced, lower case; "" = no group.
GroupName = Annotated[str, AfterValidator(_group_name)]


class GroupForm(BaseModel):
    """A watch-list row's group dropdown."""

    group: GroupName = ""


class WatchForm(BaseModel):
    """The /reels add box: profiles one per line ("name", "@name", a profile
    URL, anything after the handle on a line ignored, e.g. "@name — Real
    Name"), and the group they all go into."""

    profiles: str
    group: GroupName = ""

    @property
    def usernames(self) -> list[str]:
        """The handles, deduped, in order. Raises ValueError naming the
        lines that are not a handle, so one bad paste adds nothing."""
        handles: list[str] = []
        bad: list[str] = []
        for line in self.profiles.splitlines():
            first = line.split()[0] if line.split() else ""
            if not first:
                continue
            try:
                handles.append(ProfileHandle(username=first).username)
            except ValidationError:
                bad.append(line.strip())
        if bad:
            msg = "Not an Instagram profile: " + ", ".join(bad[:5])
            raise ValueError(msg)
        return list(dict.fromkeys(handles))
