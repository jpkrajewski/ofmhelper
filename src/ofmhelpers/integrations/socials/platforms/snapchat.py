# Copyright (C) 2026 jpkrajewski

import time

from ofmhelpers.config import settings
from ofmhelpers.db.tracked_accounts.models import SnapchatNames
from ofmhelpers.integrations.snapchat import (
    SnapchatClient,
    SnapchatError,
    SnapchatNotFoundError,
    SnapchatProfile,
    SnapchatRateLimitedError,
    SnapchatUnavailableError,
)
from ofmhelpers.integrations.socials.errors import ClientErrors
from ofmhelpers.integrations.socials.models import (
    AccountProfile,
    AccountStats,
    PostStat,
)
from ofmhelpers.integrations.socials.platforms.base import Platform

_ERRORS = ClientErrors(
    rate_limited=SnapchatRateLimitedError,
    retryable=(SnapchatUnavailableError, SnapchatNotFoundError),
    lasting=SnapchatError,
)


def _stats(found: SnapchatProfile) -> AccountStats:
    return AccountStats(
        profile=AccountProfile(
            handle=found.username,
            account_id=found.user_id,
            display_name=found.display_name,
            followers=found.subscribers or 0,
            followers_hidden=found.subscribers is None,
            avatar_url=found.avatar_url,
        ),
        posts=[PostStat.model_validate(p, from_attributes=True) for p in found.posts],
        hidden_posts=found.hidden_posts,
        complete=found.complete,
        scanned_at=time.time(),
    )


class SnapchatPlatform(SnapchatNames, Platform):
    reads_logged_in = False

    def proxies(self) -> list[str]:
        return settings.snapchat.proxy_urls

    def fetch(
        self,
        handle: str,
        proxy: str | None,
        cookies: str | None = None,  # noqa: ARG002
    ) -> AccountStats:
        with _ERRORS.mapped():
            return _stats(SnapchatClient(proxy).profile(handle))
