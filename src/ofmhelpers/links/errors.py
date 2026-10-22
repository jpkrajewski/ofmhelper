"""The only exceptions a link provider client raises. Each message is
written for the person on /links: it is shown as is."""


class LinkError(Exception):
    """The provider refused or failed the call."""


class AuthError(LinkError):
    """The key is wrong, revoked, or its plan has no API access."""


class SlugTakenError(LinkError):
    """The slug (on that domain) is someone's already."""


class QuotaError(LinkError):
    """The plan's link limit is reached: nothing was made."""


class RateLimitedError(LinkError):
    """Too many calls: stop, the rest goes at the next try."""
