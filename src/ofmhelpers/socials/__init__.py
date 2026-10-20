"""Tracked social media accounts, platform-agnostic: each platform
(`platforms/`) turns a handle into `AccountStats`; `web/account_tracker.py`
scans them on a schedule and `/accounts` shows them."""

from ofmhelpers.socials.errors import RateLimitedError, RetryableError, SocialError
from ofmhelpers.socials.models import (
    AccountMetrics,
    AccountProfile,
    AccountStats,
    PostStat,
    ViewWindow,
)
from ofmhelpers.socials.platforms import Platform, get_platform, platforms

__all__ = [
    "AccountMetrics",
    "AccountProfile",
    "AccountStats",
    "Platform",
    "PostStat",
    "RateLimitedError",
    "RetryableError",
    "SocialError",
    "ViewWindow",
    "get_platform",
    "platforms",
]
