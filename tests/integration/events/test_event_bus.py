"""An event from any producer to every sender that is on (features/events): `emit`
and `@emits`, through the events queue and the aggregator, to Telegram (its fake
at the wire). Inline first (the suite's synchronous queue), then on the real RQ
queue with its retries."""

import asyncio
import json
import logging
import time
from http import HTTPStatus

import pytest
from logfire.testing import CaptureLogfire
from rq import SimpleWorker
from rq.registry import FailedJobRegistry, ScheduledJobRegistry

import ofmhelpers.features.events.dispatch as dispatch_module
from ofmhelpers.cache import QueueName, get_queue, get_redis
from ofmhelpers.features.events import Event, emit, emits
from ofmhelpers.integrations.senders.telegram import TelegramClient
from ofmhelpers.integrations.senders.telegram.fake import (
    TOKEN,
    SentMessage,
    error,
    fake_telegram,
    flood,
    moved,
)
from ofmhelpers.log import _HideJobArguments

_BASE = "https://ascend.test/"
_CHAT, _GROUP = "111", "-1002222"
_CHATS = (_CHAT, _GROUP)
_CLICKS_TEXT = "3,000 clicks\n\nBio link of @model\n\nhttps://ascend.test/links"


@pytest.fixture(autouse=True)
def _telegram(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", ",".join(_CHATS))
    monkeypatch.setenv("APP_BASE_URL", _BASE)


@pytest.fixture(autouse=True)
def waits(monkeypatch) -> list[float]:
    """Every wait a delivery makes, without waiting."""
    found: list[float] = []
    monkeypatch.setattr(dispatch_module, "_sleep", found.append)
    return found


def _clicks(**update) -> Event:
    values = {
        "name": "links.clicks",
        "title": "3,000 clicks",
        "text": "Bio link of @model",
        "url": "/links",
    }
    return Event.model_validate(values | update)


def _sent_to(*chats: str, text: str = _CLICKS_TEXT) -> list[SentMessage]:
    return [SentMessage(chat_id=chat, text=text) for chat in chats]


def _outcomes(capfire: CaptureLogfire) -> list[str]:
    spans = capfire.exporter.exported_spans_as_dict()
    return [
        s["attributes"]["outcome"]
        for s in spans
        if s["name"] == "event {event} to {sender}"
    ]


def test_an_event_reaches_every_chat_with_a_link_to_the_page():
    emit(_clicks())

    assert fake_telegram().sent == _sent_to(*_CHATS)


def test_an_event_with_a_key_reaches_each_chat_once():
    emit(_clicks(key="links.clicks:3000:42"))
    emit(_clicks(key="links.clicks:3000:42"))
    emit(_clicks(key="links.clicks:3000:43"))

    assert fake_telegram().sent == _sent_to(*_CHATS) * 2


def test_a_key_whose_send_failed_can_be_sent_again():
    fake_telegram().fail_next.append(
        error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal Server Error")
    )
    emit(_clicks(key="links.clicks:3000:42"))

    emit(_clicks(key="links.clicks:3000:42"))

    assert fake_telegram().sent == _sent_to(_GROUP, _CHAT)


def test_a_sender_switched_off_gets_nothing(monkeypatch):
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "telegram")

    emit(_clicks())

    assert fake_telegram().sent == []


def test_a_misspelt_sender_name_is_logged(monkeypatch, caplog):
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "telegarm")

    emit(_clicks())

    assert "OFM_EVENTS_SENDERS_OFF names no sender: ['telegarm']" in caplog.text
    assert len(fake_telegram().sent) == len(_CHATS)


def test_without_a_bot_token_nothing_is_sent(monkeypatch):
    monkeypatch.delenv("OFM_TELEGRAM_BOT_TOKEN")

    emit(_clicks())

    assert fake_telegram().sent == []


def test_a_failed_send_fails_neither_the_producer_nor_the_other_chats():
    fake_telegram().fail_next.append(
        error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal Server Error")
    )

    emit(_clicks())

    assert fake_telegram().sent == _sent_to(_GROUP)


def test_sends_to_one_group_are_paced_to_what_it_takes(waits):
    emit(_clicks())
    emit(_clicks())

    assert len(fake_telegram().sent) == 2 * len(_CHATS)
    assert len(waits) == len(_CHATS)
    chat_wait, group_wait = waits
    assert 0 < chat_wait <= 1.0
    assert 2.0 < group_wait <= 3.0


def test_a_flood_answer_is_waited_out_and_sent_in_place(waits, capfire):
    fake_telegram().fail_next.append(flood(retry_after=7))

    emit(_clicks())

    assert waits == [7]
    assert fake_telegram().sent == _sent_to(*_CHATS)
    assert _outcomes(capfire) == ["sent", "sent"]


def test_a_chat_that_is_gone_is_given_up_at_once(capfire, caplog):
    fake_telegram().fail_next.append(
        error(HTTPStatus.FORBIDDEN, "Forbidden: bot was kicked from the group chat")
    )

    emit(_clicks())

    assert fake_telegram().sent == _sent_to(_GROUP)
    assert _outcomes(capfire) == ["gave_up", "sent"]
    assert "telegram gave up: Telegram sendMessage refused (403)" in caplog.text


def test_a_group_that_became_a_supergroup_still_gets_it(caplog):
    fake_telegram().fail_next.append(moved(-1009999))

    emit(_clicks())

    assert fake_telegram().sent == _sent_to("-1009999", _GROUP)
    assert "became a supergroup: put -1009999 there in its place" in caplog.text
    assert _GROUP not in caplog.text.split("refused")[0]


def test_emits_sends_what_build_makes_and_keeps_the_result():
    @emits(lambda clicks, link: Event(name="links.clicks", title=f"{clicks} on {link}"))
    def count(link: str) -> int:
        return 3000

    assert count("bio") == 3000
    assert fake_telegram().sent == _sent_to(*_CHATS, text="3000 on bio")


def test_emits_on_an_async_function():
    @emits(lambda views, **_: Event(name="accounts.views", title=f"{views} views"))
    async def views(*, day: str) -> int:
        return 5000

    assert asyncio.run(views(day="2026-11-06")) == 5000
    assert fake_telegram().sent == _sent_to(*_CHATS, text="5000 views")


def test_emits_on_a_method():
    class Links:
        @emits(lambda clicks, links: Event(name="n", title=f"{clicks} {links.name}"))
        def clicks(self) -> int:
            return 7

        name = "bio"

    assert Links().clicks() == 7
    assert fake_telegram().sent == _sent_to(*_CHATS, text="7 bio")


@pytest.mark.parametrize(
    "build",
    [
        lambda clicks: None if clicks < 3000 else Event(name="n", title="t"),
        lambda result: result["missing"],
        lambda result: {"name": "n", "title": "t"},
        lambda: Event(name="n", title="t"),
    ],
    ids=["none", "raises", "not an event", "wrong signature"],
)
def test_a_build_that_gives_no_event_sends_nothing_and_keeps_the_result(build):
    @emits(build)
    def few() -> int:
        return 12

    assert few() == 12
    assert fake_telegram().sent == []


def test_a_build_that_raises_says_which_function(caplog):
    @emits(lambda result: result["missing"])
    def refresh_links() -> dict:
        return {}

    refresh_links()

    assert "event of test_a_build_that_raises_says_which_function." in caplog.text
    assert "refresh_links not built" in caplog.text


def test_a_function_that_raises_emits_nothing():
    @emits(lambda *_: Event(name="n", title="t"))
    def fails() -> None:
        msg = "boom"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="boom"):
        fails()
    assert fake_telegram().sent == []


@pytest.mark.parametrize("thing", [None, {"name": "n", "title": "t"}, "3k clicks"])
def test_emit_of_anything_but_an_event_is_logged_not_raised(thing, caplog):
    emit(thing)

    assert "emit takes an Event" in caplog.text
    assert fake_telegram().sent == []


def test_what_emits_cannot_wrap_is_refused_when_decorating():
    def gen():
        yield 1

    with pytest.raises(TypeError, match="generator"):
        emits(lambda *_: None)(gen)
    with pytest.raises(TypeError, match="under @classmethod"):
        emits(lambda *_: None)(classmethod(lambda cls: 1))


def test_the_chats_that_pressed_start_or_added_the_bot_are_listed_once():
    fake_telegram().wrote(555, text="/start")
    fake_telegram().wrote(555, text="hi")
    fake_telegram().wrote(777)

    chats = TelegramClient(TOKEN).chats()

    assert [(c.id, c.label) for c in chats] == [(555, "Owner"), (777, "Owner")]


def test_each_send_is_a_span_with_its_outcome_and_without_its_target(capfire):
    emit(_clicks())

    spans = capfire.exporter.exported_spans_as_dict()
    sends = [s for s in spans if s["name"] == "event {event} to {sender}"]
    assert [
        (
            s["attributes"]["event"],
            s["attributes"]["sender"],
            s["attributes"]["outcome"],
        )
        for s in sends
    ] == [("links.clicks", "telegram", "sent")] * 2
    assert not any(chat in json.dumps(sends) for chat in _CHATS)


class TestOnTheRealQueue:
    """The events worker's path: emit -> events queue -> dispatch -> one
    deliver job per chat, each with its own retries."""

    @pytest.fixture(autouse=True)
    def _async_broker(self, monkeypatch):
        monkeypatch.setenv("OFM_RQ_ASYNC", "true")
        monkeypatch.setenv("OFM_EVENTS_RETRY_WAITS_S", "[60, 300]")
        get_redis().flushdb()
        yield
        get_redis().flushdb()

    @staticmethod
    def _queue():
        return get_queue(QueueName.EVENTS)

    def _drain(self) -> None:
        SimpleWorker([self._queue()], connection=get_redis()).work(burst=True)

    def test_emit_returns_before_anything_is_sent(self):
        emit(_clicks())

        assert self._queue().count == 1
        assert fake_telegram().sent == []

    def test_the_events_worker_sends_to_every_chat(self):
        emit(_clicks())

        self._drain()

        assert fake_telegram().sent == _sent_to(*_CHATS)

    def test_a_send_has_a_short_time_limit_and_a_short_stay_when_failed(self):
        emit(_clicks())
        SimpleWorker([self._queue()], connection=get_redis()).work(
            burst=True, max_jobs=1
        )

        jobs = [self._queue().fetch_job(i) for i in self._queue().get_job_ids()]
        assert [(j.timeout, j.failure_ttl) for j in jobs if j] == [
            (180, 14 * 24 * 3600)
        ] * 2

    def test_a_failed_send_is_tried_again_later_on_its_own(self):
        fake_telegram().fail_next.append(
            error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal Server Error")
        )
        emit(_clicks())

        self._drain()

        assert fake_telegram().sent == _sent_to(_GROUP)
        [job_id] = ScheduledJobRegistry(queue=self._queue()).get_job_ids()
        job = self._queue().fetch_job(job_id)
        assert job is not None
        assert job.args[:2] == ("telegram", _CHAT)
        assert job.retries_left == 1

    def test_a_send_failing_every_try_ends_in_the_failed_registry(
        self, monkeypatch, capfire
    ):
        monkeypatch.setenv("OFM_EVENTS_RETRY_WAITS_S", "[]")
        fake_telegram().fail_next.append(error(HTTPStatus.BAD_GATEWAY, "Bad Gateway"))
        emit(_clicks())

        self._drain()

        assert len(FailedJobRegistry(queue=self._queue())) == 1
        assert _outcomes(capfire) == ["gave_up", "sent"]

    def test_switching_a_sender_off_stops_what_is_already_queued(self, monkeypatch):
        emit(_clicks())
        SimpleWorker([self._queue()], connection=get_redis()).work(
            burst=True, max_jobs=1
        )
        monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "telegram")

        self._drain()

        assert fake_telegram().sent == []

    def test_rq_failure_lines_hold_no_target(self, caplog):
        """The filter configure_logging puts on `rq.worker`, on RQ's real line."""
        hide = _HideJobArguments()
        logging.getLogger("rq.worker").addFilter(hide)
        fake_telegram().fail_next.append(
            error(HTTPStatus.INTERNAL_SERVER_ERROR, "Internal Server Error")
        )
        emit(_clicks())

        try:
            with caplog.at_level(logging.ERROR, logger="rq.worker"):
                self._drain()
        finally:
            logging.getLogger("rq.worker").removeFilter(hide)

        failures = [r for r in caplog.records if r.name == "rq.worker"]
        assert failures
        assert {getattr(r, "arguments", None) for r in failures} == {"[hidden]"}


def test_a_message_that_replaces_deletes_the_one_before_in_each_chat():
    emit(_clicks(replaces="clicks.today"))
    first = list(fake_telegram().sent)

    emit(_clicks(title="4,000 clicks", replaces="clicks.today"))

    assert fake_telegram().deleted == first
    assert [m.text.split("\n")[0] for m in fake_telegram().sent[2:]] == [
        "4,000 clicks"
    ] * 2


def test_without_replaces_nothing_is_deleted():
    emit(_clicks())
    emit(_clicks())

    assert fake_telegram().deleted == []


def test_one_before_that_cannot_be_deleted_stays_and_the_new_one_still_goes(caplog):
    emit(_clicks(replaces="clicks.today"))
    fake_telegram().deleted.extend(fake_telegram().sent)
    fake_telegram()._by_id.clear()  # deleted by hand in the chat

    emit(_clicks(title="4,000 clicks", replaces="clicks.today"))

    assert len(fake_telegram().sent) == 2 * len(_CHATS)
    assert "could not delete the one before" in caplog.text


def test_an_older_one_arriving_late_never_replaces_the_newer():
    """A requeued or retried list made before the one in the chat: it goes, the
    newer stays."""
    made = time.time()
    emit(_clicks(title="newer", replaces="clicks.today", at=made))
    newer = list(fake_telegram().sent)

    emit(_clicks(title="older", replaces="clicks.today", at=made - 60))

    assert [m.text.split("\n")[0] for m in fake_telegram().deleted] == ["older"] * 2
    assert not any(m in fake_telegram().deleted for m in newer)


def test_nobody_listening_is_told_apart(monkeypatch):
    from ofmhelpers.features.events import listening

    assert listening()
    monkeypatch.setenv("OFM_EVENTS_SENDERS_OFF", "telegram")
    assert not listening()
