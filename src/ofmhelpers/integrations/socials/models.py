"""What every platform's scan of one account comes back as, whatever the
platform: the profile, the newest posts it could read with their views, and
the numbers the /accounts list and history are made of."""

from ofmhelpers.schemas.socials import (
    AccountMetrics,
    AccountProfile,
    AccountStats,
    PostStat,
    PostViews,
    ViewWindow,
)

__all__ = [
    "AccountMetrics",
    "AccountProfile",
    "AccountStats",
    "PostStat",
    "PostViews",
    "ViewWindow",
]
