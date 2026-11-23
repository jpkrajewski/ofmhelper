import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from notifications_socket import SOCKET, first_sent, next_lines, next_sent
from signin import make_user, sign_in
from starlette.websockets import WebSocketDisconnect

from ofmhelpers.background.recovery import fail_cut_off
from ofmhelpers.cache.redis import get_redis
from ofmhelpers.db.jobs.store import create_job, finish_job, record_failure
from ofmhelpers.db.users import store as users
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.notifications import service as notifications
from ofmhelpers.features.notifications.schema import Notifications, Reader
from ofmhelpers.features.trials.constants import TrialsPhase
from ofmhelpers.features.trials.tasks import Captioned, Made
from ofmhelpers.web.main import app

OWNER = "owner@example.com"
OTHER = "other@example.com"
LOGIN = 999


def _signed_in() -> tuple[TestClient, int]:
    client = TestClient(app)
    return client, sign_in(client, OWNER).id


def _trials(user_id: int, label: str) -> str:
    return create_job("trials", {"label": label, "login": LOGIN}, actor=str(user_id))


def _made_one() -> Made:
    return Made(generated=1, failed=0)


def _lines(client: TestClient) -> list[tuple[str, str, str, str]]:
    return [(n["title"], n["text"], n["url"], n["kind"]) for n in first_sent(client)]


def test_a_signed_out_notifications_is_refused():
    with pytest.raises(WebSocketDisconnect):
        first_sent(TestClient(app))


def test_a_deactivated_persons_notifications_is_refused():
    client, owner = _signed_in()
    users.set_active(owner, active=False)

    with pytest.raises(WebSocketDisconnect):
        first_sent(client)


def test_a_trials_generate_that_ended_says_how_many_it_made():
    client, owner = _signed_in()
    finish_job(_trials(owner, TrialsPhase.GENERATE), Made(generated=4, failed=1))

    assert _lines(client) == [
        ("Trials", "Trials ready: 4 made, 1 failed", "/trials/999", "info")
    ]


def test_a_generate_with_nothing_new_says_so():
    client, owner = _signed_in()
    finish_job(_trials(owner, TrialsPhase.GENERATE), Made(generated=0, failed=0))

    assert _lines(client) == [
        ("Trials", "Trials ready: No new reels in POSTED", "/trials/999", "info")
    ]


def test_a_regenerate_that_failed_is_a_warning():
    client, owner = _signed_in()
    record_failure(_trials(owner, TrialsPhase.REMAKE), "Could not make it")

    assert _lines(client) == [("Trials", "Trials failed", "/trials/999", "warn")]


def test_captions_uploads_and_running_jobs_say_nothing():
    client, owner = _signed_in()
    finish_job(_trials(owner, TrialsPhase.CAPTIONS), Captioned(captions=5))
    finish_job(_trials(owner, TrialsPhase.UPLOAD), {"uploaded": 2})
    _trials(owner, TrialsPhase.GENERATE)

    assert first_sent(client) == []


def test_nobody_sees_anyone_elses_jobs():
    client, _ = _signed_in()
    other = make_user(OTHER).id
    finish_job(_trials(other, TrialsPhase.GENERATE), Made(generated=4, failed=0))
    finish_job(
        _trials(users.agency_id(), TrialsPhase.GENERATE), Made(generated=1, failed=0)
    )

    assert first_sent(client) == []


def test_a_job_that_ends_is_sent_to_its_persons_open_notifications():
    client, owner = _signed_in()
    job_id = _trials(owner, TrialsPhase.REMAKE)
    with client.websocket_connect(SOCKET) as socket:
        assert next_sent(socket) == []

        run_job(job_id, _made_one, {})

        lines = next_lines(socket)
    assert [n["text"] for n in lines] == ["Trials ready: 1 made"]


def test_an_open_socket_of_someone_deactivated_since_is_closed_at_the_next_change():
    client, owner = _signed_in()
    with client.websocket_connect(SOCKET) as socket:
        assert next_sent(socket) == []
        users.set_active(owner, active=False)

        notifications.changed(owner)

        with pytest.raises(WebSocketDisconnect) as closed:
            next_sent(socket)
    assert closed.value.code == 1008


def test_a_job_queued_long_ago_shows_by_when_it_finished(monkeypatch):
    monkeypatch.setenv("OFM_CHATTING_NOTIFICATION_SHOWN_S", "1")
    client, owner = _signed_in()
    job_id = _trials(owner, TrialsPhase.REMAKE)
    time.sleep(1.2)

    finish_job(job_id, Made(generated=1, failed=0))

    assert [line[1] for line in _lines(client)] == ["Trials ready: 1 made"]


def _subscribers(channel: str) -> int:
    ((_, count),) = get_redis().pubsub_numsub(channel)
    return count


def test_open_sockets_share_one_redis_subscription_and_a_burst_is_one_read(monkeypatch):
    reads: list[int] = []

    def counted_read(reader):
        reads.append(reader.user_id)
        return Notifications(items=[])

    monkeypatch.setattr(notifications, "read", counted_read)
    channel = f"notifications-burst-test-{time.time_ns()}"
    monkeypatch.setattr(notifications, "_CHANNEL", channel)
    owner = make_user(OWNER)
    reader = Reader(user_id=owner.id, chats=True)

    async def five_sockets_and_a_burst() -> tuple[int, int, bool, int]:
        sockets = [notifications.updates(lambda: reader) for _ in range(5)]
        for socket in sockets:
            await anext(socket)
        await asyncio.sleep(0.3)
        shared = _subscribers(channel)
        for _ in range(50):
            notifications.changed(owner.id)
        await asyncio.sleep(0.3)
        for socket in sockets:
            await asyncio.wait_for(anext(socket), 5)
        try:
            await asyncio.wait_for(anext(sockets[0]), 0.5)
            burst_left_over = True
        except TimeoutError:
            burst_left_over = False
        for socket in sockets:
            await socket.aclose()
        await asyncio.sleep(0.3)
        return shared, len(reads), burst_left_over, _subscribers(channel)

    shared, read_count, burst_left_over, after_close = asyncio.run(
        five_sockets_and_a_burst()
    )

    assert shared == 1
    assert read_count == 10
    assert burst_left_over is False
    assert after_close == 0


def test_a_job_recovery_fails_after_a_restart_is_sent_to_its_person(monkeypatch):
    monkeypatch.setenv("OFM_RECOVERY_ORPHAN_GRACE_S", "0")
    told: list[int | None] = []
    monkeypatch.setattr(notifications, "changed", told.append)
    owner = make_user(OWNER).id
    job_id = create_job("ai", {}, actor=str(owner))

    outcomes = fail_cut_off(set(), set())

    assert outcomes[job_id] == "interrupted"
    assert told == [owner]
