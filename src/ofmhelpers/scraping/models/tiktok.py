"""TikTok's flavour of a post, plus the author block Apify nests inside it.

Same contract as `post.py`: the raw-item mapping is a model classmethod, and
every counter defaults to 0 rather than None -- TikTok always reports them, so
a missing one means the actor returned a partial item, not "unknown".
"""

from pydantic import BaseModel, ConfigDict

from ofmhelpers.scraping.models.post import PostBase


class TikTokAuthor(BaseModel):
    """Renamed out of Apify's `authorMeta` shape (`fans`, `heart`, `video`) into
    names that say what they hold."""

    model_config = ConfigDict(extra="forbid")

    id: str = ""
    username: str = ""
    nickname: str = ""
    profile_url: str = ""
    verified: bool = False
    followers: int = 0
    following: int = 0
    total_likes: int = 0
    total_videos: int = 0
    bio: str = ""
    avatar_url: str = ""

    @classmethod
    def from_raw(cls, a: dict) -> "TikTokAuthor":
        return cls.model_validate(
            {
                "id": a.get("id", ""),
                "username": a.get("name", ""),
                "nickname": a.get("nickName", ""),
                "profile_url": a.get("profileUrl", ""),
                "verified": a.get("verified", False),
                "followers": a.get("fans", 0),
                "following": a.get("following", 0),
                "total_likes": a.get("heart", 0),
                "total_videos": a.get("video", 0),
                "bio": a.get("signature", ""),
                # The original is the full-size one; `avatar` is the thumbnail
                # some actor versions return instead.
                "avatar_url": a.get("originalAvatarUrl", "") or a.get("avatar", ""),
            }
        )


class TikTokVideo(PostBase):
    # ── Top-level: the stuff you actually care about ──────────────────────────
    id: str = ""
    shares: int = 0
    bookmarks: int = 0
    reposts: int = 0
    # ── Metadata ──────────────────────────────────────────────────────────────
    author: TikTokAuthor = TikTokAuthor()
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

        return cls.model_validate(
            {
                # top-level
                "id": str(item.get("id", "")),
                # `input` is the profile the scrape was asked for; it only
                # differs from the author on a reposted video.
                "username": item.get("input", author.username),
                "caption": item.get("text", ""),
                "url": item.get("webVideoUrl", ""),
                "timestamp": cls.as_utc_datetime(
                    item.get("createTimeISO") or item.get("createTime") or ""
                ),
                "views": item.get("playCount", 0),
                "likes": item.get("diggCount", 0),
                "comments": item.get("commentCount", 0),
                "shares": item.get("shareCount", 0),
                "bookmarks": item.get("collectCount", 0),
                "reposts": item.get("repostCount", 0),
                "duration_seconds": video.get("duration", 0),
                "hashtags": [
                    h["name"] for h in item.get("hashtags", []) if h.get("name")
                ],
                # metadata
                "author": author,
                "music_name": music.get("musicName", ""),
                "music_author": music.get("musicAuthor", ""),
                "music_original": music.get("musicOriginal", False),
                "music_url": music.get("playUrl", ""),
                "cover_url": video.get("originalCoverUrl", "")
                or video.get("coverUrl", ""),
                "resolution": video.get("definition", ""),
                "format": video.get("format", ""),
                "is_ad": item.get("isAd", False),
                "is_pinned": item.get("isPinned", False),
                "is_slideshow": item.get("isSlideshow", False),
                "is_sponsored": item.get("isSponsored", False),
                "language": item.get("textLanguage", ""),
            }
        )
