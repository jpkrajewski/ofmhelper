"""The only exceptions a link provider client raises. Each message is
written for the person on /links: it is shown as is."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ofmhelpers.links.models import RemoteLink


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


class UngatedError(LinkError):
    """The link was made, its 18+ gate could not be set and deleting it
    failed too: it is live without the gate. `remote` is it, so the app
    records it (flagged, gated again at the next sync)."""

    def __init__(self, message: str, remote: RemoteLink) -> None:
        super().__init__(message)
        self.remote = remote
