"""New subscribers and sales per fan-site model in a window, the same two numbers
whatever the platform tells them by. One class per platform behind `FanSite`; a
new platform = one class and one entry in `fan_sites()`.

- Stacked: its API, for the exact window (`/subscribers/summary` new + returning,
  `/earnings` gross), on the key that reaches the model (`STACKED_API_KEY_*`,
  scope `insights:read`).
- Fanvue: what its webhooks stored: `subscribed` notifications and `chat_sales`
  (every payment, gross). Webhooks reach the production server only.
"""

from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.integrations.stacked import StackedClient, StackedError, reachable
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ChatPlatform, DeliveryKind

logger = get_logger(__name__)


class FanStats(BaseModel):
    subs: int
    cents: int


class FanSite(Protocol):
    platform: ChatPlatform

    def models(self) -> list[str]:
        """Every model it can tell numbers of, by username."""
        ...

    def stats(self, username: str) -> FanStats | None:
        """None: it could not tell (logged)."""
        ...


class StackedSite:
    platform = ChatPlatform.STACKED

    def __init__(self, since: float, until: float) -> None:
        self._start = datetime.fromtimestamp(since, UTC)
        self._end = datetime.fromtimestamp(until, UTC)
        self._reach: dict[str, tuple[StackedClient, str]] = {
            creator.username: (client, creator.user_id)
            for client, creator in reachable(settings.stacked.api_keys.values())
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
        """None on a server Fanvue sends no webhooks to (no `FANVUE_EVENT_SECRET`):
        its zeros would not be real."""
        if not settings.fanvue.event_secret:
            return None
        return FanStats(
            subs=self._subs.get(username, 0), cents=self._cents.get(username, 0)
        )


def fan_sites(since: float, until: float) -> dict[ChatPlatform, FanSite]:
    """Every platform with numbers, for the window [since, until)."""
    sites: list[FanSite] = [StackedSite(since, until), FanvueSite(since, until)]
    return {site.platform: site for site in sites}
