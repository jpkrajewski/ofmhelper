"""A person's own Telegram chat for their events (owner 2026-11-15: "is it
easy for new users to connect their telegram bot?"; before, one bot sent
every owner's VA notices to the agency's chats, set by hand in the .env).

/account's Connect Telegram hands out a one-time link to the bot,
`t.me/<bot>?start=<token>` (`OFM_TELEGRAM_LINK_TTL_S`, the token in Redis
holding who asked). Opening it and pressing Start sends the bot
`/start <token>`; the asks sweep reads it (`features/reports/asks.py`,
`telegram/inbox.py` `Start`) and `link` makes that chat the person's
(`user_event_targets`), saying so in the chat. Their events (`Event.owner_id`)
go there from then on (`features/events/dispatch.py`). Disconnect takes it
back. Only where the bot is read (`asks.enabled()`: production; dev shares
prod's bot and never reads it)."""

import secrets

from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.acting import Acting, Via, acting
from ofmhelpers.db.stores import event_targets
from ofmhelpers.features.events.dispatch import deliver_to
from ofmhelpers.features.refusals import UnavailableError
from ofmhelpers.integrations.senders.telegram.client import TelegramError, bot_name
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.events import Event
lazy from ofmhelpers.integrations.senders.telegram.inbox import Start

logger = get_logger(__name__)

SENDER = "telegram"
NOT_SET_UP = "Telegram is not set up on this server"
_LINK_KEY = "telegram:link:{token}"


def linked(user_id: int) -> bool:
    return event_targets.of(user_id, SENDER) is not None


def connect_url(user_id: int) -> str:
    """The bot's one-time link for `user_id`. Raises `UnavailableError`
    when the bot or Redis cannot be reached (logged)."""
    token = settings.telegram.bot_token
    if token is None:
        raise UnavailableError(NOT_SET_UP)
    code = secrets.token_urlsafe(16)  # Telegram takes [A-Za-z0-9_-], up to 64
    try:
        name = bot_name(token.get_secret_value())
        get_redis().set(
            _LINK_KEY.format(token=code), str(user_id), ex=settings.telegram.link_ttl_s
        )
    except TelegramError, RedisError:
        logger.warning("telegram: no link for user %s", user_id, exc_info=True)
        raise UnavailableError(NOT_SET_UP) from None
    return f"https://t.me/{name}?start={code}"


def link(start: Start) -> bool:
    """The chat that opened a link becomes its person's, told so there;
    False (told to open a new one) for a link used, too old or not ours."""
    raw = get_redis().getdel(_LINK_KEY.format(token=start.token))
    if raw is None:
        logger.info("telegram: a /start link that is used or too old")
        deliver_to(SENDER, start.chat_id, _old_link())
        return False
    user_id = int(raw)
    with acting(Acting(via=Via.TELEGRAM, actor_id=user_id, ref="telegram /start")):
        saved = event_targets.save(user_id, SENDER, start.chat_id)
    if not saved:
        logger.info("telegram: a /start link of user %s, deleted since", user_id)
        deliver_to(SENDER, start.chat_id, _old_link())
        return False
    # Never the chat id: the app does not log where messages go.
    logger.info("telegram: user %s linked their chat", user_id)
    deliver_to(SENDER, start.chat_id, _connected())
    return True


def unlink(user_id: int) -> bool:
    """False when nothing was linked."""
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
