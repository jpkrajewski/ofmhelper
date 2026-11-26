# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
lazy from collections.abc import Callable

from redis.exceptions import RedisError

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import create_job
from ofmhelpers.features.account import telegram as telegram_link
from ofmhelpers.features.events.dispatch import deliver_to
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.reports import daily
from ofmhelpers.integrations.senders.telegram import inbox
from ofmhelpers.integrations.senders.telegram.client import (
    TelegramError,
    bot_name,
    telegram_client,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.integrations.senders.schema import Event

logger = get_logger(__name__)

ASKS: dict[str, Callable[[], list[Event]]] = {
    "money": daily.money_now,
}
_SENDER = "telegram"
TASK = "report_ask"
_NEXT_READ_S = 1.0
_AFTER_FAILURE_S = 20.0


class _Reading:
    def __init__(self) -> None:
        self.failed = False


@lru_cache(maxsize=1)
def _reading() -> _Reading:
    return _Reading()


def pause_s() -> float:
    return _AFTER_FAILURE_S if _reading().failed else _NEXT_READ_S


def answer_asks() -> None:
    token = settings.telegram.bot_token
    if token is None:
        return
    state = _reading()
    try:
        secret = token.get_secret_value()
        found = inbox.read(telegram_client(secret), set(ASKS), bot_name(secret))
        for ask in found.asks:
            _start(ask.chat_id, ask.word)
            logger.info("telegram: %r asked", ask.word)
        for start in found.starts:
            telegram_link.link(start)
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
    job_id = create_job(TASK, {"word": word})
    enqueue(run_job, (job_id, answer, {"chat_id": chat_id, "word": word}))


def answer(chat_id: str, word: str) -> None:
    for event in ASKS[word]():
        deliver_to(_SENDER, chat_id, event)
