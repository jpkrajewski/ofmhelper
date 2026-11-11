"""Instagram. Sources in turn (`Platform.sources`): Apify
(`instagram_apify.py`), then our own client (`fetch`) logged out through the
proxies, then the same client as the account's owner (cookies: last resort).

`fetch` = `ofmhelpers.integrations.instagram`'s client: the profile
(followers, following, avatar; through the proxy) and the newest
`track_max_reels` reels with their play counts -- logged in when `cookies`
are given and the login works (every reel), else the logged-out reels tab
(which omits reels Instagram hides from visitors; the scan is then marked
`limited`). A private account still has its profile numbers; it just has no
reels to read."""

import time
from itertools import islice

import requests
from pydantic import TypeAdapter

from ofmhelpers.config import settings
from ofmhelpers.integrations.instagram import (
    InstagramClient,
    InstagramError,
    InstagramRateLimitedError,
    InstagramUnavailableError,
    ProfileNotFoundError,
)
from ofmhelpers.integrations.instagram.models import MediaUrl, ReelSummary
from ofmhelpers.integrations.socials.errors import (
    RateLimitedError,
    RetryableError,
    SocialError,
)
from ofmhelpers.integrations.socials.models import (
    AccountProfile,
    AccountStats,
    PostStat,
)
from ofmhelpers.integrations.socials.platforms.base import Platform
from ofmhelpers.integrations.socials.platforms.instagram_apify import (
    ApifyInstagramSource,
)
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.socials import InstagramNames
lazy from ofmhelpers.integrations.socials.sources import AccountSource

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
    client: InstagramClient, username: str, user_id: str, cap: int, logged_in: bool
) -> tuple[list[ReelSummary], bool]:
    """Up to `cap` + 1 newest reels (one past the cap tells a complete read
    from a capped one), and whether they come from the limited logged-out
    view: Instagram hides an account's sensitive/age-restricted reels from
    logged-out visitors, so the logged-in list is used when `logged_in` and
    the owner's login works."""
    if logged_in:
        try:
            full = client.reels_logged_in(username, user_id, cap + 1)
        except requests.RequestException, InstagramError:
            logger.warning("logged-in reels of %s failed", user_id, exc_info=True)
            full = None
        if full is not None:
            return full, False
    return list(islice(client.reels(user_id), cap + 1)), True


class InstagramPlatform(InstagramNames, Platform):
    def proxies(self) -> list[str]:
        return settings.instagram.proxy_urls

    def api_source(self) -> AccountSource:
        return ApifyInstagramSource()

    def fetch(
        self, handle: str, proxy: str | None, cookies: str | None = None
    ) -> AccountStats:
        try:
            return self._fetch(
                InstagramClient(proxy, cookies), handle, logged_in=bool(cookies)
            )
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
    def _fetch(
        client: InstagramClient, handle: str, *, logged_in: bool
    ) -> AccountStats:
        profile = client.open_profile(handle)
        cap = settings.instagram.track_max_reels
        reels, limited = (
            ([], False)
            if profile.is_private
            else _reels(client, profile.username, profile.user_id, cap, logged_in)
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
