"""The "Chats unanswered" message to Telegram every 10 minutes
(features/chatting/wait_events.py): one message per look naming, per platform,
each model with fans waiting and how long each has waited, by the pop-up's own
5-minute rule -- nothing when nobody waits, and only after those chats were read
from the platform again. Fanvue is left out for now
(`OFM_CHATTING_EVENTS_PLATFORMS_OFF`).

And 5 minutes after each fan message, by webhook or sync (rechecks.py): the list
at once if that fan still waits and nobody was told yet (prod 2026-11-06: three
fans who wrote at 18:50-18:55 were told at 19:07, by the look)."""

import os
import threading
import time
from http import HTTPStatus

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from chat_platforms import StackedDriver
from fastapi.testclient import TestClient

import ofmhelpers.features.events.dispatch as dispatch_module
from ofmhelpers.db.models import ChatFanMessageRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.features.chatting import rechecks, wait_events
from ofmhelpers.features.chatting.sync import sync
from ofmhelpers.features.chatting.wait_events import emit_waiting, recheck_due, waited
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram
from ofmhelpers.schemas.chatting import ChatEvent, ChatPlatform, ChatRead
from ofmhelpers.web.main import app

NOW = time.time()
LOOK = 600
# The pop-up's rule: a fan waiting longer is unanswered.
LIMIT = 300


@pytest.fixture(autouse=True)
def _telegram(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("OFM_TELEGRAM_CHAT_IDS", "111")
    monkeypatch.setattr(dispatch_module, "_sleep", lambda _s: None)


@pytest.fixture
def fanvue_on(monkeypatch):
    monkeypatch.setenv("OFM_CHATTING_EVENTS_PLATFORMS_OFF", "")


@pytest.fixture
def every_platform_read(monkeypatch) -> list[set[str]]:
    """Every platform read here, its fresh read changing nothing; who was read."""
    read: list[set[str]] = []

    def fresh(_platform, usernames):
        read.append(usernames)
        return usernames

    monkeypatch.setattr(wait_events, "_fresh", fresh)
    return read


def _fan_wrote(
    fan: str,
    ago_s: float,
    *,
    platform: str = "fanvue",
    model: str = "walker_dane",
) -> None:
    with session_scope() as s:
        s.add(
            ChatFanMessageRow(
                platform=platform,
                message_id=f"{fan}-{ago_s}",
                channel_id=f"chat-{fan}",
                model_username=model,
                fan_name=fan,
                text="hi",
                sent_at=NOW - ago_s,
            )
        )


def _answered(fan: str) -> None:
    with session_scope() as s:
        s.query(ChatFanMessageRow).filter_by(channel_id=f"chat-{fan}").update(
            {"replied_at": NOW}
        )


def _sent() -> list[str]:
    return [m.text for m in fake_telegram().sent]


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(-5, "0m"), (59, "0m"), (600, "10m"), (8100, "2h 15m"), (273_600, "3d 4h")],
)
def test_how_long_a_fan_waited_reads_short(seconds, text):
    assert waited(seconds) == text


@pytest.mark.usefixtures("every_platform_read", "fanvue_on")
def test_waiting_fans_are_listed_by_platform_and_model_in_one_message():
    _fan_wrote("Anna", ago_s=8100)
    _fan_wrote("Bob", ago_s=400)
    _fan_wrote("Carl", ago_s=900, platform="stacked", model="danewalkerjohnson")

    assert emit_waiting(NOW) == 2

    assert _sent() == [
        (
            "Chatting:\n\n"
            "Fanvue:\n\n"
            "Chats unanswered (2): @walker_dane\n"
            "- Anna (unanswered 2h 15m)\n"
            "- Bob (unanswered 6m)\n\n"
            "Stacked:\n\n"
            "Chats unanswered (1): @danewalkerjohnson\n"
            "- Carl (unanswered 15m)"
        )
    ]


@pytest.mark.usefixtures("every_platform_read")
def test_every_look_lists_who_still_waits_and_a_restart_in_one_look_sends_once():
    _fan_wrote("Carl", ago_s=600, platform="stacked", model="danewalkerjohnson")
    # The next look's start: Carl has waited past 5 minutes by then, wherever NOW
    # falls in its own look.
    look = NOW - NOW % LOOK + LOOK

    emit_waiting(look + 1)
    emit_waiting(look + 2)
    emit_waiting(look + LOOK + 1)

    assert len(_sent()) == 2


@pytest.mark.usefixtures("every_platform_read")
def test_nobody_waiting_over_five_minutes_sends_nothing():
    _fan_wrote("Carl", ago_s=120, platform="stacked", model="danewalkerjohnson")

    assert emit_waiting(NOW) == 0

    assert _sent() == []


@pytest.mark.usefixtures("every_platform_read", "fanvue_on")
def test_an_answered_fan_is_no_longer_listed():
    _fan_wrote("Anna", ago_s=600)
    _fan_wrote("Bob", ago_s=360)
    _answered("Anna")

    emit_waiting(NOW)

    assert _sent() == [
        (
            "Chatting:\n\nFanvue:\n\nChats unanswered (1): @walker_dane\n"
            "- Bob (unanswered 6m)"
        )
    ]


@pytest.mark.usefixtures("every_platform_read")
def test_fanvue_is_left_out_for_now():
    _fan_wrote("Anna", ago_s=600)

    assert emit_waiting(NOW) == 0

    assert _sent() == []


@pytest.mark.usefixtures("every_platform_read", "fanvue_on")
def test_a_fan_without_a_name_still_counts():
    _fan_wrote("", ago_s=600)

    emit_waiting(NOW)

    assert _sent()[0].endswith("- (no name) (unanswered 10m)")


@pytest.mark.usefixtures("fanvue_on")
def test_a_platform_not_read_on_this_server_is_not_listed():
    """What a server without the login stored can be days old (Fanvue on dev)."""
    _fan_wrote("Anna", ago_s=600)

    assert emit_waiting(NOW) == 0

    assert _sent() == []


@pytest.fixture
def stacked(monkeypatch) -> StackedDriver:
    driver = StackedDriver(monkeypatch)
    driver.connect()
    return driver


def test_a_chat_answered_since_the_last_read_is_not_listed(stacked):
    now = time.time()
    stacked.fan_writes("ann", now - 600)
    sync(stacked.source)
    stacked.model_replies("ann", now - 30)

    assert emit_waiting(now) == 0

    assert _sent() == []


def test_a_chat_still_waiting_after_a_fresh_read_is_listed(stacked):
    now = time.time()
    stacked.fan_writes("ann", now - 600)
    sync(stacked.source)

    assert emit_waiting(now) == 1

    assert _sent() == [
        (
            "Chatting:\n\nStacked:\n\nChats unanswered (1): @fake_creator\n"
            "- ann (unanswered 10m)"
        )
    ]


@pytest.mark.usefixtures("every_platform_read")
def test_a_new_list_deletes_the_one_before_and_the_last_stays_when_nobody_waits():
    _fan_wrote("Carl", ago_s=600, platform="stacked", model="danewalkerjohnson")
    _fan_wrote("Dina", ago_s=700, platform="stacked", model="danewalkerjohnson")
    look = NOW - NOW % LOOK + LOOK
    emit_waiting(look + 1)
    first = list(fake_telegram().sent)
    _answered("Dina")

    emit_waiting(look + LOOK + 1)
    _answered("Carl")
    emit_waiting(look + 2 * LOOK + 1)

    assert fake_telegram().deleted == first
    assert len(fake_telegram().sent) == 2
    assert "Chats unanswered (1)" in fake_telegram().sent[-1].text


def test_a_model_that_cannot_be_read_does_not_drop_the_others(monkeypatch):
    """Two models on one platform, one read fails: the other is still listed."""
    from types import SimpleNamespace

    from ofmhelpers.features.chatting import sync as chat_sync
    from ofmhelpers.integrations.chat_source import ChatSourceError
    from ofmhelpers.schemas.chatting import ChatModel, ChatModels, ChatPlatform

    def new_reads(model, _scope):
        if model.username == "broken":
            msg = "Stacked is not answering"
            raise ChatSourceError(msg)
        return []

    source = SimpleNamespace(
        platform=ChatPlatform.STACKED,
        configured=lambda: True,
        models=lambda: ChatModels(
            found=[ChatModel(model_id=n, username=n) for n in ("broken", "fine")]
        ),
        new_reads=new_reads,
    )
    monkeypatch.setattr(wait_events, "source_for", lambda _platform: source)
    _fan_wrote("Ann", ago_s=600, platform="stacked", model="broken")
    _fan_wrote("Bob", ago_s=600, platform="stacked", model="fine")

    assert chat_sync.read_now(source, {"broken", "fine"}) == {"fine"}
    assert emit_waiting(NOW) == 1
    assert "@fine" in _sent()[0]
    assert "@broken" not in _sent()[0]


@pytest.mark.usefixtures("every_platform_read")
def test_a_model_with_many_fans_waiting_names_the_longest_ten(monkeypatch):
    for n in range(12):
        _fan_wrote(f"fan{n:02d}", ago_s=600 + n, platform="stacked", model="dane")

    emit_waiting(NOW)

    lines = _sent()[0].split("\n")
    assert lines[4] == "Chats unanswered (12): @dane"
    assert lines[5].startswith("- fan11 ")
    assert lines[-1] == "- and 2 more"
    assert len([line for line in lines if line.startswith("- fan")]) == 10


@pytest.mark.usefixtures("every_platform_read")
def test_nobody_listening_reads_nothing(monkeypatch, every_platform_read):
    monkeypatch.delenv("OFM_TELEGRAM_BOT_TOKEN")
    _fan_wrote("Carl", ago_s=600, platform="stacked", model="danewalkerjohnson")

    assert emit_waiting(NOW) == 0

    assert every_platform_read == []


# --- 5 minutes after each fan message (rechecks.py) -----------------------------


def _one_list(*fans_waited: tuple[str, str], model: str = "fake_creator") -> str:
    lines = [f"- {fan} (unanswered {w})" for fan, w in fans_waited]
    head = f"Chats unanswered ({len(lines)}): @{model}"
    return "\n".join(["Chatting:", "", "Stacked:", "", head, *lines])


def test_a_fan_by_webhook_is_told_five_minutes_after_writing(stacked):
    now = time.time()
    message_id = stacked.fan_writes("ann", now)
    raw, headers = stacked.signed(
        stacked.message("ann", message_id, from_fan=True, at=now)
    )
    reply = TestClient(app).post("/events/stacked", content=raw, headers=headers)
    assert reply.status_code == HTTPStatus.OK

    assert recheck_due(now + LIMIT - 10) == 0
    assert _sent() == []
    assert recheck_due(now + LIMIT + 2) == 1
    assert _sent() == [_one_list(("ann", "5m"))]


def test_a_fan_read_by_the_sync_is_told_once_however_often_they_write(stacked):
    now = time.time()
    for ago in (60, 40, 20):
        stacked.fan_writes("ann", now - ago)
    sync(stacked.source)

    told = [recheck_due(now - ago + LIMIT + 2) for ago in (60, 40, 20)]

    assert told == [1, 0, 0]
    assert _sent() == [_one_list(("ann", "5m"))]


def test_a_fan_answered_within_five_minutes_is_not_told(stacked):
    now = time.time()
    stacked.fan_writes("ann", now - 60)
    sync(stacked.source)
    stacked.model_replies("ann", now - 30)  # only the fresh read sees it
    reads = stacked.chat_reads()

    assert recheck_due(now + LIMIT) == 0

    assert stacked.chat_reads() > reads  # the recheck did read the chat again
    assert _sent() == []


def test_a_fan_the_look_named_is_not_named_again_by_their_recheck(stacked):
    now = time.time()
    stacked.fan_writes("ann", now - 400)
    sync(stacked.source)

    assert emit_waiting(now) == 1
    assert recheck_due(now) == 0

    assert len(_sent()) == 1


def test_a_new_fan_is_told_at_once_with_the_one_already_told(stacked):
    now = time.time()
    stacked.fan_writes("ann", now - 400)
    sync(stacked.source)
    emit_waiting(now)
    stacked.fan_writes("bob", now - 10)
    sync(stacked.source)

    assert recheck_due(now + LIMIT) == 1

    assert _sent()[-1] == _one_list(("ann", "11m"), ("bob", "5m"))
    assert len(_sent()) == 2


def test_a_chats_history_plans_no_recheck_the_look_covers_it(stacked):
    now = time.time()
    stacked.fan_writes("ann", now - 3600)
    stacked.fan_writes("bob", now - 60)
    sync(stacked.source)

    due = rechecks.take_due(now + LIMIT)

    assert [(r.platform, r.channel_id) for r in due] == [
        ("stacked", stacked.channel("bob"))
    ]


def test_a_recheck_is_taken_by_one_caller_only(stacked):
    now = time.time()
    stacked.fan_writes("ann", now - 60)
    sync(stacked.source)

    first = rechecks.take_due(now + LIMIT)
    second = rechecks.take_due(now + LIMIT)

    assert [(r.platform, r.channel_id) for r in first] == [
        ("stacked", stacked.channel("ann"))
    ]
    assert second == []


def test_fanvue_messages_plan_no_recheck_while_it_is_off():
    read = ChatRead(
        channel_id="c1",
        model_username="m",
        fan_name="f",
        last_message_id="1",
        events=[ChatEvent(message_id="1", from_fan=True, at=NOW)],
    )

    rechecks.plan(ChatPlatform.FANVUE, read, NOW)
    assert rechecks.take_due(NOW + LIMIT + 2) == []
    rechecks.plan(ChatPlatform.STACKED, read, NOW)
    assert [(r.platform, r.channel_id) for r in rechecks.take_due(NOW + LIMIT + 2)] == [
        ("stacked", "c1")
    ]


@pytest.mark.usefixtures("every_platform_read")
def test_prod_2026_11_06_each_fan_is_told_within_five_minutes_and_a_poll():
    """Three fans of one Stacked model wrote at 18:50:45, 18:51:19 and 18:55:08;
    the 15-minute look alone told them at 19:07. Each is named by the first
    15 s poll after their 5 minutes."""
    base = NOW - 3600  # 18:50:00
    wrote = {"fan_a": base + 45, "fan_b": base + 79, "fan_c": base + 308}
    for fan, at in wrote.items():
        _fan_wrote(fan, NOW - at, platform="stacked", model="danewalkerjohnson")
        read = ChatRead(
            channel_id=f"chat-{fan}",
            model_username="danewalkerjohnson",
            fan_name=fan,
            last_message_id=fan,
            events=[ChatEvent(message_id=fan, from_fan=True, at=at)],
        )
        rechecks.plan(ChatPlatform.STACKED, read, at)

    named_at: dict[str, float] = {}
    for poll in range(int(17 * 60 / 15)):  # 18:50:00 .. 19:07:00
        at = base + poll * 15
        if recheck_due(at):
            for fan in wrote:
                if fan in _sent()[-1]:
                    named_at.setdefault(fan, at)

    assert named_at.keys() == wrote.keys()
    assert all(named_at[f] - wrote[f] <= LIMIT + 1 + 15 for f in wrote)
    assert len(_sent()) == 3  # a new list per fan, each replacing the last


def _carl_planned(at: float) -> None:
    """Carl wrote at `at`, his recheck planned."""
    _fan_wrote("Carl", NOW - at, platform="stacked", model="danewalkerjohnson")
    read = ChatRead(
        channel_id="chat-Carl",
        model_username="danewalkerjohnson",
        fan_name="Carl",
        last_message_id="c1",
        events=[ChatEvent(message_id="c1", from_fan=True, at=at)],
    )
    rechecks.plan(ChatPlatform.STACKED, read, at)


def test_a_recheck_whose_chat_could_not_be_read_is_tried_at_the_next_poll(
    monkeypatch,
):
    reads: list[set[str]] = []

    def down_once(_platform, usernames):
        reads.append(usernames)
        return set() if len(reads) == 1 else usernames

    monkeypatch.setattr(wait_events, "_fresh", down_once)
    _carl_planned(NOW)
    due = NOW + LIMIT + 2

    assert recheck_due(due) == 0
    assert recheck_due(due + 15) == 1

    assert len(reads) == 2
    assert len(_sent()) == 1


@pytest.mark.usefixtures("every_platform_read")
def test_a_recheck_whose_list_failed_is_tried_at_the_next_poll(monkeypatch):
    real = wait_events._waiting
    calls: list[float] = []

    def db_down_once(now):
        calls.append(now)
        if len(calls) == 1:
            msg = "the database blinked"
            raise RuntimeError(msg)
        return real(now)

    monkeypatch.setattr(wait_events, "_waiting", db_down_once)
    _carl_planned(NOW)
    due = NOW + LIMIT + 2

    with pytest.raises(RuntimeError, match="blinked"):
        recheck_due(due)
    assert recheck_due(due + 15) == 1

    assert len(_sent()) == 1


def test_a_recheck_during_a_look_waits_for_it_and_sends_nothing_more(monkeypatch):
    """The look and the recheck are two sweeps: at once, both would find Carl untold
    and both send."""
    reading, go = threading.Event(), threading.Event()

    def slow_read(_platform, usernames):
        reading.set()
        go.wait(10)
        return usernames

    monkeypatch.setattr(wait_events, "_fresh", slow_read)
    _carl_planned(NOW - 400)
    look = threading.Thread(target=emit_waiting, args=(NOW,))
    look.start()
    assert reading.wait(10)
    recheck = threading.Thread(target=recheck_due, args=(NOW,))
    recheck.start()
    recheck.join(1)  # it waits on the lock the look holds
    assert recheck.is_alive()

    go.set()
    look.join(10)
    recheck.join(10)

    assert len(_sent()) == 1


def test_a_recheck_that_cannot_wait_out_a_slow_list_skips_and_comes_back(
    monkeypatch,
):
    """Prod 2026-11-07 18:07: Stacked's 429s kept a list over the lock's 120 s,
    the recheck waiting on it made its own unlocked and two lists went out a
    second apart. Now the lock is renewed while the list is made, and a caller
    that cannot take it in time makes none: its recheck is due again at the
    next poll."""
    monkeypatch.setattr(rechecks, "_TELL_LOCK_S", 0.6)  # renewed every 0.2 s
    monkeypatch.setattr(rechecks, "_TELL_WAIT_S", 0.2)
    reading, go = threading.Event(), threading.Event()

    def slow_read(_platform, usernames):
        reading.set()
        go.wait(10)
        return usernames

    monkeypatch.setattr(wait_events, "_fresh", slow_read)
    _carl_planned(NOW - 400)
    look = threading.Thread(target=emit_waiting, args=(NOW,))
    look.start()
    assert reading.wait(10)
    time.sleep(1.5)  # the list outlives the lock's own life

    assert recheck_due(NOW) == 0
    assert len(rechecks.take_due(NOW + 15)) == 1  # due again at the next poll

    go.set()
    look.join(10)
    assert len(_sent()) == 1
