"""The "Chats unanswered" message to Telegram every 15 minutes
(features/chatting/wait_events.py): one message per look naming, per platform,
each model with fans waiting and how long each has waited, by the pop-up's own
5-minute rule -- nothing when nobody waits, and only after those chats were read
from the platform again. Fanvue is left out for now
(`OFM_CHATTING_EVENTS_PLATFORMS_OFF`)."""

import time

import pytest
from chat_platforms import StackedDriver

import ofmhelpers.features.events.dispatch as dispatch_module
from ofmhelpers.db.models import ChatFanMessageRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.features.chatting import wait_events
from ofmhelpers.features.chatting.sync import sync
from ofmhelpers.features.chatting.wait_events import emit_waiting, waited
from ofmhelpers.integrations.senders.telegram.fake import TOKEN, fake_telegram

NOW = time.time()
LOOK = 900


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
