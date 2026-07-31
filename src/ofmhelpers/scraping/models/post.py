"""The shared post shape, and Instagram's flavour of it.

Every field arrives from a third-party Apify actor whose key names drift
between runs and between actors, so the mapping from a raw dataset item lives
on the model (`from_apify`) rather than in whatever code happened to call the
actor.
"""

from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

MAX_CAPTION_CHARS = 200
MAX_HASHTAGS = 10


class PostBase(BaseModel):
    """What post_exporter can write and post_scorer can rank. Subclasses add
    platform-specific extras but never rename these."""

    model_config = ConfigDict(extra="forbid")

    username: str
    url: str
    timestamp: datetime
    views: int | None
    likes: int | None
    comments: int | None
    caption: str | None
    duration_seconds: float | None
    hashtags: list[str] = []

    @classmethod
    def from_apify(cls, item: dict) -> "PostBase":
        raise NotImplementedError

    @staticmethod
    def as_utc_datetime(raw: object) -> datetime:
        """Apify sends a timestamp as ISO text, as unix seconds, or not at all
        -- and occasionally as something unparseable. A bad date must not lose
        the whole row: the sheet is a review artifact, so `now` beats dropping
        a post."""
        try:
            if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                return datetime.fromtimestamp(raw, tz=UTC)
            if raw:
                return datetime.fromisoformat(str(raw))
        except Exception:
            return datetime.now(UTC)
        return datetime.now(UTC)

    def is_valid(self) -> bool:
        return bool(self.username and self.url and self.views)


class Reel(PostBase):
    @staticmethod
    def _hashtag_names(raw: object) -> list[str]:
        """Some actors return `["tag"]`, others `[{"name": "tag"}]`."""
        if not isinstance(raw, list):
            return []
        return [h.get("name", h) if isinstance(h, dict) else str(h) for h in raw]

    @classmethod
    def from_apify(cls, item: dict) -> "Reel":
        return cls.model_validate(
            {
                "username": item.get("ownerUsername") or "",
                # shortCode, not a URL, is what some actor versions return --
                # it still identifies the reel, which is what the sheet needs.
                "url": item.get("url") or item.get("shortCode") or "",
                "timestamp": cls.as_utc_datetime(
                    item.get("timestamp")
                    or item.get("takenAt")
                    or item.get("taken_at")
                    or item.get("date")
                    or ""
                ),
                "views": item.get("videoPlayCount") or item.get("playCount"),
                "likes": item.get("likesCount") or item.get("likes"),
                "comments": item.get("commentsCount") or item.get("comments"),
                "caption": (item.get("caption") or "")[:MAX_CAPTION_CHARS],
                "duration_seconds": item.get("videoDuration"),
                "hashtags": cls._hashtag_names(item.get("hashtags", []))[:MAX_HASHTAGS],
            }
        )
