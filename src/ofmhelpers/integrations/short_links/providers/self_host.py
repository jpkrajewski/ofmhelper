# Copyright (C) 2026 jpkrajewski

import time
lazy from datetime import date

from ofmhelpers.config import settings
from ofmhelpers.db.self_links import store
from ofmhelpers.integrations.short_links import RemoteLink, Usage
from ofmhelpers.integrations.short_links.providers.base import (
    LinkProvider,
    RequestHeaders,
)
lazy from ofmhelpers.integrations.short_links import LinkDays, LinkHours, NewLink


class SelfProvider(LinkProvider):
    key = "self"

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self.user_id = int(api_key)

    @classmethod
    def local_key(cls, user_id: int) -> str | None:
        return str(user_id)

    def _headers(self) -> RequestHeaders:
        return RequestHeaders()

    def create(self, link: NewLink) -> RemoteLink:
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

    def daily(self, remote_ids: list[str], start: date, end: date) -> list[LinkDays]:
        return store.daily(self.user_id, remote_ids, start, end)

    def hourly(self, remote_ids: list[str], day: date) -> list[LinkHours]:
        return store.hourly(self.user_id, remote_ids, day)

    def enable_adult(self, remote_id: str) -> None: ...

    def delete(self, remote_id: str) -> None: ...

    def usage(self) -> Usage:
        return Usage(
            used=store.live_count(self.user_id),
            limit=settings.links.self_link_limit,
        )
