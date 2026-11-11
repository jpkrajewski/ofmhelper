"""Words typed to the Telegram bot, each answered in the chat that typed it.

`money`: Clicks / Rev of the last 24 hours as of the ask (clicks per link asked
of the providers by the hour, subs, CTR, conversion, sales per model:
`daily.money_now`).
A new word is one entry in `ASKS`.

The sweep (`answer_asks`, features/jobs/sweeps.py) waits for messages
(`telegram/inbox.py`) and queues one `answer` per ask on the worker, where the
numbers are counted and each message goes to that chat through the event bus's
own delivery (`dispatch.deliver_to`: paced with every other message to the chat,
retried, in Logfire). Only on a server that talks to Telegram: not with
`telegram` in `OFM_EVENTS_SENDERS_OFF` (dev, whose bot token is prod's: two
servers reading one bot take each other's messages)."""

from functools import lru_cache
lazy from collections.abc import Callable

from redis.exceptions import RedisError

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.events.dispatch import deliver_to
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.reports import daily
from ofmhelpers.integrations.senders.telegram import inbox
from ofmhelpers.integrations.senders.telegram.client import (
    TelegramClient,
    TelegramError,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.schemas.events import Event

logger = get_logger(__name__)

ASKS: dict[str, Callable[[], list[Event]]] = {
    "money": daily.money_now,
}
_SENDER = "telegram"
# An ask's job row.
TASK = "report_ask"
# Between reads: at once (each read waits for a message itself), or this long
# after one failed (Telegram or Redis down: no read and no warning every second).
_NEXT_READ_S = 1.0
_AFTER_FAILURE_S = 20.0


class _Reading:
    """Whether the last read failed."""

    def __init__(self) -> None:
        self.failed = False


@lru_cache(maxsize=1)
def _reading() -> _Reading:
    return _Reading()


@lru_cache(maxsize=2)
def _bot_name(token: str) -> str:
    """The bot's username (`/money@<it>`), asked once per token."""
    return TelegramClient(token).me().username


def enabled() -> bool:
    """A bot to read, on the server that talks to Telegram."""
    return (
        settings.telegram.bot_token is not None
        and _SENDER not in settings.events.senders_off_list()
    )


def pause_s() -> float:
    """The sweep's wait before the next read."""
    return _AFTER_FAILURE_S if _reading().failed else _NEXT_READ_S


def answer_asks() -> None:
    """One read: every ask queued, then the read confirmed. Telegram or Redis
    down: a warning, and the next read waits `_AFTER_FAILURE_S`."""
    token = settings.telegram.bot_token
    if token is None:
        return
    state = _reading()
    try:
        secret = token.get_secret_value()
        found = inbox.read(TelegramClient(secret), set(ASKS), _bot_name(secret))
        for ask in found.asks:
            _start(ask.chat_id, ask.word)
            # No chat id: the app never logs where a message goes (senders/CLAUDE.md).
            logger.info("telegram: %r asked", ask.word)
        inbox.confirm(found)
    except TelegramError, RedisError:
        state.failed = True
        logger.warning("telegram asks not read", exc_info=True)
        return
    except Exception:
        state.failed = True
        raise
    state.failed = False


def _start(chat_id: str, word: str) -> None:
    """The answer as a job (the agency's): its row keeps the word, never the
    chat (the app does not store where a message goes)."""
    job_id = create_job(TASK, {"word": word})
    enqueue(run_job, job_id, answer, {"chat_id": chat_id, "word": word})


def answer(chat_id: str, word: str) -> None:
    """On the worker: `word`'s answer, a message per part, to that chat only."""
    for event in ASKS[word]():
        deliver_to(_SENDER, chat_id, event)
