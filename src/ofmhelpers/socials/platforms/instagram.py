"""Instagram through `ofmhelpers.instagram`'s client: the profile
(followers, following, avatar; logged out, through the proxies) and the
newest `track_max_reels` reels with their play counts -- through the
uploaded login when it works (every reel), else the logged-out reels tab
(which omits reels Instagram hides from visitors; the scan is then marked
`limited`). A private account still has its profile numbers; it just has no
reels to read."""

import time
from itertools import islice

import requests
from pydantic import TypeAdapter

from ofmhelpers.config import settings
from ofmhelpers.instagram import (
    InstagramClient,
    InstagramError,
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileHandle,
    ProfileNotFoundError,
)
from ofmhelpers.instagram.models import MediaUrl, ReelSummary
from ofmhelpers.log import get_logger
from ofmhelpers.socials.errors import RateLimitedError, RetryableError, SocialError
from ofmhelpers.socials.models import AccountProfile, AccountStats, PostStat
from ofmhelpers.socials.platforms.base import Platform

logger = get_logger(__name__)

# Instagram CDN URLs moved onto its global host (IPv4-reachable).
_MEDIA_URL: TypeAdapter[str] = TypeAdapter(MediaUrl)

# A proxy failing like this is dead for the rest of the job.
_DEAD_PROXY = (
    requests.exceptions.ProxyError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
)


def _reels(
    client: InstagramClient, username: str, user_id: str, cap: int
) -> tuple[list[ReelSummary], bool]:
    """Up to `cap` + 1 newest reels (one past the cap tells a complete read
    from a capped one), and whether they come from the limited logged-out
    view: Instagram hides an account's sensitive/age-restricted reels from
    logged-out visitors, so the logged-in list is used whenever the uploaded
    login works (OFM_IG_TRACK_LOGGED_IN)."""
    if settings.instagram.track_logged_in:
        try:
            full = client.reels_logged_in(username, user_id, cap + 1)
        except (requests.RequestException, InstagramError):
            logger.warning("logged-in reels of %s failed", user_id, exc_info=True)
            full = None
        if full is not None:
            return full, False
    return list(islice(client.reels(user_id), cap + 1)), True


class InstagramPlatform(Platform):
    key = "instagram"
    label = "Instagram"

    def handle(self, text: str) -> str:
        return ProfileHandle(username=text).username

    def profile_url(self, handle: str) -> str:
        return f"https://www.instagram.com/{handle}/"

    def proxies(self) -> list[str]:
        return settings.instagram.proxy_urls

    def fetch(self, handle: str, proxy: str | None) -> AccountStats:
        try:
            return self._fetch(InstagramClient(proxy), handle)
        except InstagramRateLimitedError as exc:
            raise RateLimitedError(str(exc)) from exc
        except (InstagramUnavailableError, ProfileNotFoundError) as exc:
            # "Not found" is also what a flagged proxy IP gets for a real
            # account: worth another connection.
            raise RetryableError(str(exc)) from exc
        except requests.RequestException as exc:
            msg = f"connection failed ({type(exc).__name__})"
            raise RetryableError(
                msg, reached=False, dead_proxy=isinstance(exc, _DEAD_PROXY)
            ) from exc
        except InstagramError as exc:
            raise SocialError(str(exc)) from exc

    @staticmethod
    def _fetch(client: InstagramClient, handle: str) -> AccountStats:
        profile = client.open_profile(handle)
        cap = settings.instagram.track_max_reels
        reels, limited = (
            ([], False)
            if profile.is_private
            else _reels(client, profile.username, profile.user_id, cap)
        )
        return AccountStats(
            profile=AccountProfile(
                handle=profile.username,
                account_id=profile.user_id,
                display_name=profile.full_name,
                followers=profile.followers,
                following=profile.following,
                is_private=profile.is_private,
                is_verified=profile.is_verified,
                avatar_url=profile.avatar_url,
            ),
            posts=[
                PostStat(
                    id=r.code,
                    url=f"https://www.instagram.com/reel/{r.code}/",
                    views=r.play_count or 0,
                    posted_at=r.posted_at,
                    thumbnail_url=_MEDIA_URL.validate_python(r.image_versions2.first),
                )
                for r in reels[:cap]
            ],
            # A private account's posts were not read at all.
            complete=not profile.is_private and len(reels) <= cap,
            limited=limited,
            scanned_at=time.time(),
        )
