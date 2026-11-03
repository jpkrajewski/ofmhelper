"""Tracked social media accounts, platform-agnostic: each platform
(`platforms/`) turns a handle into `AccountStats`; `features/accounts/tracker.py`
scans them on a schedule and `/accounts` shows them."""

from ofmhelpers.integrations.socials.errors import (
    RateLimitedError,
    RetryableError,
    SocialError,
)
from ofmhelpers.integrations.socials.models import (
    AccountMetrics,
    AccountProfile,
    AccountStats,
    PostStat,
    PostViews,
    ViewWindow,
)
from ofmhelpers.integrations.socials.platforms import Platform, get_platform, platforms

__all__ = [
    "AccountMetrics",
    "AccountProfile",
    "AccountStats",
    "Platform",
    "PostStat",
    "PostViews",
    "RateLimitedError",
    "RetryableError",
    "SocialError",
    "ViewWindow",
    "get_platform",
    "platforms",
]
