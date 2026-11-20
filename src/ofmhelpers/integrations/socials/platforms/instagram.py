import time
from itertools import islice

import requests
from pydantic import BaseModel, TypeAdapter

from ofmhelpers.config import settings
from ofmhelpers.db.tracked_accounts.schema import InstagramNames
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
lazy from ofmhelpers.integrations.socials.sources import AccountSource

logger = get_logger(__name__)

_MEDIA_URL: TypeAdapter[str] = TypeAdapter(MediaUrl)

_DEAD_PROXY = (
    requests.exceptions.ProxyError,
    requests.exceptions.SSLError,
    requests.exceptions.ConnectTimeout,
)


class _Reels(BaseModel):
    summaries: list[ReelSummary]
    limited: bool


def _reels(
    client: InstagramClient, username: str, user_id: str, cap: int, logged_in: bool
) -> _Reels:
    if logged_in:
        try:
            full = client.reels_logged_in(username, user_id, cap + 1)
        except requests.RequestException, InstagramError:
            logger.warning("logged-in reels of %s failed", user_id, exc_info=True)
            full = None
        if full is not None:
            return _Reels(summaries=full, limited=False)
    return _Reels(summaries=list(islice(client.reels(user_id), cap + 1)), limited=True)


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
        found = (
            _Reels(summaries=[], limited=False)
            if profile.is_private
            else _reels(client, profile.username, profile.user_id, cap, logged_in)
        )
        reels = found.summaries
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
            complete=not profile.is_private and len(reels) <= cap,
            limited=found.limited,
            scanned_at=time.time(),
        )
