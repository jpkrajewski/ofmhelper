# Copyright (C) 2026 jpkrajewski

import logging
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import ChatPlatform, DeliveryKind
from ofmhelpers.features.account.stacked_keys import StackedKeys
from ofmhelpers.integrations.stacked import StackedClient, StackedError, reachable

logger = logging.getLogger(__name__)


class FanStats(BaseModel):
    subs: int
    cents: int


class FanSite(Protocol):
    platform: ChatPlatform

    def models(self) -> list[str]: ...

    def stats(self, username: str) -> FanStats | None: ...


class StackedSite:
    platform = ChatPlatform.STACKED

    def __init__(self, since: float, until: float) -> None:
        self._start = datetime.fromtimestamp(since, UTC)
        self._end = datetime.fromtimestamp(until, UTC)
        self._reach: dict[str, tuple[StackedClient, str]] = {
            r.creator.username: (r.client, r.creator.user_id)
            for r in reachable(StackedKeys().keys())
        }

    def models(self) -> list[str]:
        return sorted(self._reach)

    def stats(self, username: str) -> FanStats | None:
        found = self._reach.get(username)
        if found is None:
            return None
        client, user_id = found
        try:
            subs = client.subscriber_summary(user_id, self._start, self._end)
            sold = client.earnings(user_id, self._start, self._end)
        except StackedError:
            logger.warning("stacked numbers of %s unavailable", username, exc_info=True)
            return None
        return FanStats(
            subs=(subs.new + subs.returning) if subs else 0,
            cents=sold.gross_cents if sold else 0,
        )


class FanvueSite:
    platform = ChatPlatform.FANVUE

    def __init__(self, since: float, until: float) -> None:
        self._subs = {
            t.model_username: t.count
            for t in chats.counts_between(DeliveryKind.SUBSCRIBED, since, until)
            if t.platform == self.platform
        }
        self._cents = {
            t.model_username: t.cents
            for t in chats.sales_between(since, until)
            if t.platform == self.platform
        }

    def models(self) -> list[str]:
        return chats.models(self.platform)

    def stats(self, username: str) -> FanStats | None:
        if not settings.fanvue.event_secret:
            return None
        return FanStats(
            subs=self._subs.get(username, 0),
            cents=self._cents.get(username, 0),
        )


def fan_sites(since: float, until: float) -> list[FanSite]:
    return [StackedSite(since, until), FanvueSite(since, until)]
