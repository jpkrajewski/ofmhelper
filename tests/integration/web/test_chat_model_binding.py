# Copyright (C) 2026 jpkrajewski

import time

from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import ChatEvent, ChatPlatform, ChatRead

PLATFORM = ChatPlatform.STACKED


def _read(model: str, channel: str, *events: ChatEvent) -> ChatRead:
    return ChatRead(
        channel_id=channel,
        model_username=model,
        fan_name="fan",
        last_message_id=events[-1].message_id,
        events=list(events),
    )


def _bobs_fan_waits() -> float:
    asked = time.time() - 600
    kept = chats.record_events(
        PLATFORM,
        _read("bob", "bob-chat", ChatEvent(message_id="f1", from_fan=True, at=asked)),
    )
    assert kept
    return asked


def _waiting() -> list[tuple[str, str]]:
    return [(w.model_username, w.channel_id) for w in chats.open_waits(time.time())]


def test_a_reply_signed_for_another_model_does_not_answer_its_fan():
    _bobs_fan_waits()

    reply = ChatEvent(message_id="m1", from_fan=False, at=time.time())
    kept = chats.record_events(PLATFORM, _read("alice", "bob-chat", reply))

    assert kept is False
    assert _waiting() == [("bob", "bob-chat")]
    assert chats.record_events(PLATFORM, _read("bob", "bob-chat", reply))
    assert _waiting() == []


def test_a_deletion_signed_for_another_model_deletes_nothing():
    _bobs_fan_waits()

    chats.forget_message(PLATFORM, "f1", "alice")

    assert _waiting() == [("bob", "bob-chat")]
    chats.forget_message(PLATFORM, "f1", "bob")
    assert _waiting() == []
