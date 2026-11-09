"""The "self" provider: bridge pages we serve from our own server on our own
domains, counted from our own click log instead of a third party's API. It is
a LinkProvider like Oopsie/Bouncy so /links, the sync and the reports treat it
the same, but every method reads or writes our database -- there is no HTTP,
no API key, and no `_call`.

Identity rides in on the key: `local_key` hands the owner's id in as the
"api_key", so the client always knows whose links it lists and counts (a self
link is always 18+ gated, so `create` returns it gated and `enable_adult` is a
no-op)."""

import time
lazy from datetime import date

from ofmhelpers.config import settings
from ofmhelpers.db.stores import self_links as store
from ofmhelpers.integrations.short_links import RemoteLink, Usage
from ofmhelpers.integrations.short_links.providers.base import LinkProvider
lazy from ofmhelpers.integrations.short_links import DayStat, HourStat, NewLink


class SelfProvider(LinkProvider):
    key = "self"

    def __init__(self, api_key: str) -> None:
        # No HTTP session: every call is a database read or write. The key is
        # the owner's id (see local_key).
        self.api_key = api_key
        self.user_id = int(api_key)

    @classmethod
    def local_key(cls, user_id: int) -> str | None:
        return str(user_id)

    def _headers(self) -> dict[str, str]:
        return {}

    def create(self, link: NewLink) -> RemoteLink:
        """Mints the bridge link: it is live and 18+ gated at once (the row is
        written by the caller from what is returned). No network, so it never
        fails for a provider reason."""
        return RemoteLink(
            remote_id=f"{link.domain}/{link.slug}",
            slug=link.slug,
            domain=link.domain,
            url=f"https://{link.domain}/{link.slug}",
            destination=link.destination,
            active=True,
            adult=True,
            created_at=time.time(),
        )

    def list_links(self) -> list[RemoteLink]:
        return store.live_links(self.user_id)

    def daily(
        self, remote_ids: list[str], start: date, end: date
    ) -> dict[str, list[DayStat]]:
        return store.daily(self.user_id, remote_ids, start, end)

    def hourly(self, remote_ids: list[str], day: date) -> dict[str, list[HourStat]]:
        return store.hourly(self.user_id, remote_ids, day)

    def enable_adult(self, remote_id: str) -> None:
        """A self link is always gated; nothing to turn on."""

    def delete(self, remote_id: str) -> None:
        """The row is already marked "deleting" by the caller; the next sync
        drops it (and its clicks cascade), so there is nothing to call out to."""

    def usage(self) -> Usage:
        return Usage(
            used=store.live_count(self.user_id), limit=settings.links.self_link_limit
        )
