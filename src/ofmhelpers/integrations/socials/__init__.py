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
from ofmhelpers.integrations.socials.sources import (
    AccountSource,
    Answers,
    Refusal,
    ScanTarget,
    scan_through,
)

__all__ = [
    "AccountMetrics",
    "AccountProfile",
    "AccountSource",
    "AccountStats",
    "Answers",
    "Platform",
    "PostStat",
    "PostViews",
    "RateLimitedError",
    "Refusal",
    "RetryableError",
    "ScanTarget",
    "SocialError",
    "ViewWindow",
    "get_platform",
    "platforms",
    "scan_through",
]
