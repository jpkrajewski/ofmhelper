from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class PostBase:
    username: str
    url: str
    timestamp: datetime
    views: Optional[int]
    likes: Optional[int]
    comments: Optional[int]
    caption: Optional[str]
    duration_seconds: Optional[float]
    hashtags: list[str] = field(default_factory=list)

    @classmethod
    def from_apify(cls, item: dict) -> "PostBase":
        raise NotImplementedError()

    def is_valid(self) -> bool:
        return self.username and self.url and self.views


@dataclass
class Reel(PostBase):
    username: str
    url: str
    timestamp: datetime
    views: Optional[int]
    likes: Optional[int]
    comments: Optional[int]
    caption: Optional[str]
    duration_seconds: Optional[float]
    hashtags: list[str] = field(default_factory=list)

    @classmethod
    def from_apify(cls, item: dict) -> "Reel":
        raw_ts = (
            item.get("timestamp")
            or item.get("takenAt")
            or item.get("taken_at")
            or item.get("date")
            or ""
        )
        try:
            if isinstance(raw_ts, (int, float)):
                ts = datetime.fromtimestamp(raw_ts, tz=timezone.utc)
            elif raw_ts:
                ts = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            else:
                ts = datetime.now(timezone.utc)
        except Exception:
            ts = datetime.now(timezone.utc)

        hashtags = item.get("hashtags", [])
        if isinstance(hashtags, list):
            hashtags = [
                h.get("name", h) if isinstance(h, dict) else str(h) for h in hashtags
            ]

        return cls(
            username=item.get("ownerUsername"),
            url=item.get("url") or item.get("shortCode") or "",
            timestamp=ts,
            views=item.get("videoPlayCount") or item.get("playCount"),
            likes=item.get("likesCount") or item.get("likes"),
            comments=item.get("commentsCount") or item.get("comments"),
            caption=(item.get("caption") or "")[:200],
            duration_seconds=item.get("videoDuration"),
            hashtags=hashtags[:10],
        )


@dataclass
class TikTokAuthor:
    id: str
    username: str
    nickname: str
    profile_url: str
    verified: bool
    followers: int
    following: int
    total_likes: int
    total_videos: int
    bio: str
    avatar_url: str

    @classmethod
    def from_raw(cls, a: dict) -> "TikTokAuthor":
        return cls(
            id=a.get("id", ""),
            username=a.get("name", ""),
            nickname=a.get("nickName", ""),
            profile_url=a.get("profileUrl", ""),
            verified=a.get("verified", False),
            followers=a.get("fans", 0),
            following=a.get("following", 0),
            total_likes=a.get("heart", 0),
            total_videos=a.get("video", 0),
            bio=a.get("signature", ""),
            avatar_url=a.get("originalAvatarUrl", "") or a.get("avatar", ""),
        )


@dataclass
class TikTokVideo(PostBase):
    # ── Top-level: the stuff you actually care about ──────────────────────────
    id: str

    username: str
    url: str
    timestamp: datetime
    views: int
    likes: int
    comments: int
    caption: str
    duration_seconds: int
    hashtags: list[str]

    shares: int
    bookmarks: int
    reposts: int
    # ── Metadata ──────────────────────────────────────────────────────────────
    author: TikTokAuthor = field(repr=False)
    music_name: str = ""
    music_author: str = ""
    music_original: bool = False
    music_url: str = ""
    cover_url: str = ""
    resolution: str = ""
    format: str = ""
    is_ad: bool = False
    is_pinned: bool = False
    is_slideshow: bool = False
    is_sponsored: bool = False
    language: str = ""

    @classmethod
    def from_apify(cls, item: dict) -> "TikTokVideo":
        author = TikTokAuthor.from_raw(item.get("authorMeta", {}))
        music: dict = item.get("musicMeta", {})
        video: dict = item.get("videoMeta", {})
        ts_iso = item.get("createTimeISO")
        ts_unix = item.get("createTime")

        if ts_iso:
            posted_at = datetime.fromisoformat(ts_iso.replace("Z", "+00:00"))
        elif ts_unix:
            posted_at = datetime.fromtimestamp(ts_unix, tz=timezone.utc)
        else:
            posted_at = datetime.now(timezone.utc)

        hashtags = [h["name"] for h in item.get("hashtags", []) if h.get("name")]

        return cls(
            # top-level
            id=str(item.get("id", "")),
            username=item.get("input", author.username),
            caption=item.get("text", ""),
            url=item.get("webVideoUrl", ""),
            timestamp=posted_at,
            views=item.get("playCount", 0),
            likes=item.get("diggCount", 0),
            comments=item.get("commentCount", 0),
            shares=item.get("shareCount", 0),
            bookmarks=item.get("collectCount", 0),
            reposts=item.get("repostCount", 0),
            duration_seconds=video.get("duration", 0),
            hashtags=hashtags,
            # metadata
            author=author,
            music_name=music.get("musicName", ""),
            music_author=music.get("musicAuthor", ""),
            music_original=music.get("musicOriginal", False),
            music_url=music.get("playUrl", ""),
            cover_url=video.get("originalCoverUrl", "") or video.get("coverUrl", ""),
            resolution=video.get("definition", ""),
            format=video.get("format", ""),
            is_ad=item.get("isAd", False),
            is_pinned=item.get("isPinned", False),
            is_slideshow=item.get("isSlideshow", False),
            is_sponsored=item.get("isSponsored", False),
            language=item.get("textLanguage", ""),
        )
