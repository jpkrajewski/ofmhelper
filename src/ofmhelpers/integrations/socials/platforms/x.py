import time

from ofmhelpers.config import settings
from ofmhelpers.db.tracked_accounts.schema import XNames
from ofmhelpers.integrations.socials.errors import ClientErrors
from ofmhelpers.integrations.socials.models import (
    AccountProfile,
    AccountStats,
    PostStat,
)
from ofmhelpers.integrations.socials.platforms.base import Platform
from ofmhelpers.integrations.socials.platforms.x_apify import ApifyXSource
from ofmhelpers.integrations.x import (
    XClient,
    XError,
    XNotFoundError,
    XProfile,
    XRateLimitedError,
    XUnavailableError,
)
lazy from ofmhelpers.integrations.socials.sources import AccountSource

_ERRORS = ClientErrors(
    rate_limited=XRateLimitedError,
    retryable=(XUnavailableError, XNotFoundError),
    lasting=XError,
)


def _stats(found: XProfile) -> AccountStats:
    return AccountStats(
        profile=AccountProfile(
            handle=found.username,
            account_id=found.user_id,
            display_name=found.name,
            followers=found.followers,
            following=found.following,
            is_private=found.is_protected,
            is_verified=found.is_verified,
            avatar_url=found.avatar_url,
        ),
        posts=[PostStat.model_validate(p, from_attributes=True) for p in found.posts],
        complete=found.complete and not found.is_protected,
        scanned_at=time.time(),
    )


class XPlatform(XNames, Platform):
    reads_logged_in = False

    def proxies(self) -> list[str]:
        return settings.x.proxy_urls

    def api_source(self) -> AccountSource:
        return ApifyXSource()

    def fetch(
        self,
        handle: str,
        proxy: str | None,
        cookies: str | None = None,  # noqa: ARG002
    ) -> AccountStats:
        with _ERRORS.mapped():
            return _stats(XClient(proxy).profile(handle))
