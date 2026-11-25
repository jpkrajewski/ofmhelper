# Copyright (C) 2026 jpkrajewski

import logging
import secrets
import time

from pydantic import BaseModel, ValidationError
from redis.exceptions import RedisError

from ofmhelpers.acting import Acting, Via, acting
from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.event_targets import store as event_targets
from ofmhelpers.db.users import store as users
from ofmhelpers.features.events.dispatch import deliver_to
from ofmhelpers.features.refusals import UnavailableError
from ofmhelpers.integrations.senders.schema import Event
from ofmhelpers.integrations.senders.telegram.client import TelegramError, bot_name
lazy from ofmhelpers.integrations.senders.telegram.inbox import Start

logger = logging.getLogger(__name__)

SENDER = "telegram"
NOT_SET_UP = "Telegram is not set up on this server"
_LINK_KEY = "telegram:link:{token}"


class PendingLink(BaseModel):
    user_id: int
    issued_at: float
    stamp: str


def enabled() -> bool:
    return (
        settings.telegram.bot_token is not None
        and settings.events.sending(settings.infra.production)
        and SENDER not in settings.events.senders_off_list()
    )


def linked(user_id: int) -> bool:
    return event_targets.of(user_id, SENDER) is not None


def connect_url(user_id: int) -> str:
    token = settings.telegram.bot_token
    if token is None:
        raise UnavailableError(NOT_SET_UP)
    code = secrets.token_urlsafe(16)
    person = users.get_user_uncached(user_id)
    pending = PendingLink(
        user_id=user_id,
        issued_at=time.time(),
        stamp=person.credential_stamp if person else "",
    )
    try:
        name = bot_name(token.get_secret_value())
        get_redis().set(
            _LINK_KEY.format(token=code),
            pending.model_dump_json(),
            ex=settings.telegram.link_ttl_s,
        )
    except TelegramError, RedisError:
        logger.warning("telegram: no link for user %s", user_id, exc_info=True)
        raise UnavailableError(NOT_SET_UP) from None
    return f"https://t.me/{name}?start={code}"


def link(start: Start) -> bool:
    raw = get_redis().getdel(_LINK_KEY.format(token=start.token))
    if raw is None:
        logger.info("telegram: a /start link that is used or too old")
        deliver_to(SENDER, start.chat_id, _old_link())
        return False
    try:
        pending = PendingLink.model_validate_json(raw)
    except ValidationError:
        logger.warning("telegram: a /start link nobody can read", exc_info=True)
        deliver_to(SENDER, start.chat_id, _old_link())
        return False
    user_id = pending.user_id
    person = users.get_user_uncached(user_id)
    if person is None or not person.active:
        logger.info("telegram: a /start link of user %s, not active", user_id)
        deliver_to(SENDER, start.chat_id, _old_link())
        return False
    ended = person.sessions_ended_at
    if person.credential_stamp != pending.stamp or (
        ended is not None and ended >= pending.issued_at
    ):
        logger.info(
            "telegram: a /start link of user %s from before a sign-out",
            user_id,
        )
        deliver_to(SENDER, start.chat_id, _old_link())
        return False
    with acting(Acting(via=Via.TELEGRAM, actor_id=user_id, ref="telegram /start")):
        saved = event_targets.save(user_id, SENDER, start.chat_id)
    if not saved:
        logger.info("telegram: a /start link of user %s, deleted since", user_id)
        deliver_to(SENDER, start.chat_id, _old_link())
        return False
    logger.info("telegram: user %s linked their chat", user_id)
    deliver_to(SENDER, start.chat_id, _connected())
    return True


def unlink(user_id: int) -> bool:
    return event_targets.remove(user_id, SENDER)


def _connected() -> Event:
    return Event(
        name="account.telegram",
        title="Connected",
        text="Your notices come to this chat now. Disconnect on your Account page.",
    )


def _old_link() -> Event:
    return Event(
        name="account.telegram",
        title="This link is used or too old",
        text="Press Connect Telegram on your Account page again.",
    )
