"""What every admin page's pop-ups name (static/js/chats-alert.js), across every
platform: one per model with fans left waiting, and one per mass message finished, new
subscriber and big sale. Read from the store, so no page and no extra round waits on a platform."""

import time

from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.schemas.chatting import (
    ChatAlerts,
    ChatPlatform,
    ChatPopup,
    DeliveryKind,
    StoredNotification,
)

_CENTS = 100
_POPUP_WORDS = {
    DeliveryKind.MASS: "Mass message sent",
    DeliveryKind.SUBSCRIBED: "New subscriber",
}


def money(cents: int | None, currency: str | None) -> str:
    """$75, $75.50; another currency says its code."""
    amount = (cents or 0) / _CENTS
    text = f"{amount:.0f}" if amount.is_integer() else f"{amount:.2f}"
    return f"${text}" if currency in (None, "USD") else f"{currency} {text}"


def _popup_text(row: StoredNotification) -> str:
    if row.kind == DeliveryKind.SALE:
        return f"Sold {money(row.amount_cents, row.currency)} {row.detail}".strip()
    return _POPUP_WORDS[row.kind]


def _wait_popups(now: float) -> list[ChatPopup]:
    """One per model, the longest wait first, counting its fans waiting. A chat counts
    from the fan's first unanswered message, so one who keeps writing does not restart
    the clock, and it counts until it is answered, however long that takes."""
    waits = chats.open_waits(before=now - settings.chatting.unanswered_after_s)
    per_model: dict[tuple[ChatPlatform, str], list[str]] = {}
    for wait in sorted(waits, key=lambda wait: wait.started_at):
        per_model.setdefault((wait.platform, wait.model_username), []).append(wait.id)
    return [
        ChatPopup(
            id=f"waits:{platform.value}:{username}",
            event_ids=wait_ids,
            kind="warn",
            text=f"Chats unanswered ({len(wait_ids)})",
            username=username,
            url=platform.model_page(username),
        )
        for (platform, username), wait_ids in per_model.items()
    ]


def _reconnect_popups() -> list[ChatPopup]:
    """A login that stopped working stops its models being read: nothing else shows."""
    popups = []
    for platform in ChatPlatform:
        for account in chats.accounts(platform):
            if account.needs_reconnect:
                popup_id = f"reconnect:{platform.value}:{account.account_id}"
                popups.append(
                    ChatPopup(
                        id=popup_id,
                        event_ids=[popup_id],
                        kind="warn",
                        text=f"Connect {platform.label} again",
                        username=account.username,
                        url=f"/chatting/{platform.value}",
                    )
                )
    return popups


def _event_popup(row: StoredNotification) -> ChatPopup:
    event_id = f"{row.platform.value}:{row.event_id}"
    return ChatPopup(
        id=event_id,
        event_ids=[event_id],
        text=_popup_text(row),
        username=row.model_username,
        url=row.platform.model_page(row.model_username),
    )


def _event_popups(now: float) -> list[ChatPopup]:
    """Newest first."""
    return [
        _event_popup(row)
        for row in chats.notifications(
            since=now - settings.chatting.notification_shown_s
        )
    ]


def chat_alerts() -> ChatAlerts:
    now = time.time()
    return ChatAlerts(
        popups=_reconnect_popups() + _wait_popups(now) + _event_popups(now),
        check_every_s=settings.chatting.alerts_check_s,
    )
