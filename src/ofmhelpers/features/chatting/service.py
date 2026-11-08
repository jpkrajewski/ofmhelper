"""What a model's /chatting page shows, from the stored history."""

import time
from datetime import datetime
from statistics import mean
from zoneinfo import ZoneInfo

from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.features.chatting.recent import remember
from ofmhelpers.schemas.chatting import (
    ChatPlatform,
    FanMessage,
    LateWait,
    ModelReport,
)

_MINUTE_S = 60
_HOUR_MIN = 60
_HOUR_S = _MINUTE_S * _HOUR_MIN


def _duration(seconds: float) -> str:
    total = round(seconds)
    if total < _MINUTE_S:
        return f"{total} s"
    minutes, rest_s = divmod(total, _MINUTE_S)
    if minutes < _HOUR_MIN:
        return f"{minutes} min {rest_s} s"
    hours, rest_min = divmod(minutes, _HOUR_MIN)
    return f"{hours} h {rest_min} min"


def _when(epoch: float, zone: str) -> str:
    return datetime.fromtimestamp(epoch, ZoneInfo(zone)).strftime("%d %b %H:%M:%S")


def _waits(messages: list[FanMessage]) -> list[FanMessage]:
    """One entry per wait: a fan's messages until the model's next one share that
    reply, and the wait runs from the first of them. `messages` come newest
    first, so the last one kept per wait is its first."""
    return list({(m.channel_id, m.replied_at): m for m in messages}.values())


def _urgent_first(wait: FanMessage) -> tuple[bool, float]:
    """Fans still waiting on top, longest wait first; then answered, newest first."""
    if wait.replied_at is None:
        return (False, wait.sent_at)
    return (True, -wait.sent_at)


def _late(wait: FanMessage, now: float, zone: str) -> LateWait:
    return LateWait(
        sent=_when(wait.sent_at, zone),
        replied=_when(wait.replied_at, zone) if wait.replied_at else "No reply yet",
        waited=_duration((wait.replied_at or now) - wait.sent_at),
        fan_name=wait.fan_name,
        text=wait.text,
    )


def model_report(platform: ChatPlatform, username: str, zone: str) -> ModelReport:
    """The look-back's average response time, and each wait past the limit,
    answered or not, its times in `zone`. A fan still waiting stays, however long
    ago they wrote; one answered counts while the answer is recent."""
    now = time.time()
    limit_s = settings.chatting.unanswered_after_s
    lookback_s = settings.chatting.lookback_s
    waits = _waits(chats.fan_messages(platform, username, now - lookback_s))
    answered = [w.replied_at - w.sent_at for w in waits if w.replied_at is not None]
    late = [w for w in waits if (w.replied_at or now) - w.sent_at > limit_s]
    return ModelReport(
        username=username,
        average_response_time=_duration(mean(answered)) if answered else None,
        period_h=lookback_s // _HOUR_S,
        late_after_min=limit_s // _MINUTE_S,
        late=[_late(w, now, zone) for w in sorted(late, key=_urgent_first)],
    )


def open_model(
    user_id: int, platform: ChatPlatform, username: str, zone: str
) -> ModelReport:
    """A model's page. Opening it makes them one of this person's recent ones,
    unless that name has no chats: a typo is not offered back."""
    if username in chats.models(platform):
        remember(user_id, platform, username)
    return model_report(platform, username, zone)


def platform_models(platform: ChatPlatform) -> list[str]:
    """Every model with chats read, and anyone just connected before their first read."""
    connected = {a.username for a in chats.accounts(platform) if not a.is_agency}
    return sorted(set(chats.models(platform)) | connected)


def accounts_to_reconnect(platform: ChatPlatform) -> list[str]:
    return [a.username for a in chats.accounts(platform) if a.needs_reconnect]
