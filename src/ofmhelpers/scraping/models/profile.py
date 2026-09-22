"""An Instagram profile as the Apify profile actor returns it.

Same rule as post.py: the actor is third-party and its key names drift
between versions (`followersCount` vs `followers`, a single `externalUrl`
vs an `externalUrls` list), so the mapping lives on the model rather than
in the pipeline that called the actor.
"""

from pydantic import BaseModel, ConfigDict

MAX_BIO_CHARS = 1000


class InstagramProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    username: str
    full_name: str | None = None
    followers: int | None = None
    biography: str | None = None
    # Every off-Instagram link on the profile. Newer actor versions return
    # several (Instagram allows up to five); older ones return one string.
    # Normalised to a list here so callers never branch on which they got.
    external_urls: list[str] = []
    is_private: bool = False
    is_verified: bool = False

    @property
    def profile_url(self) -> str:
        return f"https://www.instagram.com/{self.username}/"

    @property
    def searchable_text(self) -> str:
        """Bio and links as one blob -- an OnlyFans link is as likely to be
        typed into the bio text as it is to sit in the link field."""
        return "\n".join([self.biography or "", *self.external_urls])

    @staticmethod
    def _external_urls(item: dict) -> list[str]:
        """`externalUrls` is a list of `{"url": ...}` on newer actor versions;
        `externalUrl` is a bare string on older ones. Both may be present."""
        urls: list[str] = []
        raw = item.get("externalUrls")
        if isinstance(raw, list):
            for entry in raw:
                url = entry.get("url") if isinstance(entry, dict) else entry
                if url:
                    urls.append(str(url))
        single = item.get("externalUrl")
        if single and str(single) not in urls:
            urls.append(str(single))
        return urls

    @classmethod
    def from_apify(cls, item: dict) -> "InstagramProfile":
        return cls.model_validate(
            {
                "username": item.get("username") or "",
                "full_name": item.get("fullName") or item.get("full_name"),
                "followers": item.get("followersCount") or item.get("followers"),
                "biography": (item.get("biography") or "")[:MAX_BIO_CHARS] or None,
                "external_urls": cls._external_urls(item),
                "is_private": bool(item.get("private") or item.get("isPrivate")),
                "is_verified": bool(item.get("verified") or item.get("isVerified")),
            }
        )

    def is_valid(self) -> bool:
        return bool(self.username and self.followers is not None)
