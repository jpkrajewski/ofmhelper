"""
A VA's day leaves a record (owner 2026-11-14): every action keeps
`users.last_seen_at` (to the minute), a VA is out after 2 hours without one
while everyone else keeps 5, and a VA who just stops working gets an
`idle_logout` event dated at their last action -- from the sweep, or from
their next sign-in -- once per idle stretch.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import time

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in
from sqlalchemy import select

from ofmhelpers.config import settings
from ofmhelpers.db.models import JobRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import users
from ofmhelpers.features.jobs.sweeps import sweeps
from ofmhelpers.features.signin.outcome import signed_in, signed_out
from ofmhelpers.features.users import idle, presence
from ofmhelpers.schemas import Role, User
from ofmhelpers.web.main import app

pytestmark = pytest.mark.filterwarnings("ignore")

OWNER = "owner@example.com"
VA = "va@example.com"
_HOUR = 3600
PAGE = {"sec-fetch-mode": "navigate"}
POLL = {"sec-fetch-mode": "cors"}


def _va() -> User:
    owner = make_user(OWNER)
    va = users.set_owner(make_user(VA, Role.VA).id, owner.id)
    assert va is not None
    return va


def _fresh(user: User) -> User:
    found = users.get_user(user.id)
    assert found is not None
    return found


def _events(user: User, task: str) -> list[float]:
    """When `user`'s events of `task` happened, oldest first."""
    with session_scope() as s:
        return list(
            s.scalars(
                select(JobRow.created_at)
                .where(JobRow.user_id == user.id, JobRow.task == task)
                .order_by(JobRow.created_at)
            )
        )


@pytest.fixture
def clock(monkeypatch) -> list[float]:
    now = [time.time()]
    monkeypatch.setattr(time, "time", lambda: now[0])
    return now


# --- last seen ------------------------------------------------------------------


def test_an_action_is_kept_on_the_person_and_a_poll_is_not():
    va = _va()
    client = TestClient(app)
    sign_in(client, VA, Role.VA)

    client.get("/clone/gallery/latest", headers=POLL)
    after_poll = _fresh(va).last_seen_at
    before = time.time()
    client.get("/clone", headers=PAGE)

    assert after_poll is None
    assert _fresh(va).last_seen_at == pytest.approx(before, abs=5)


def test_last_seen_is_written_at_most_once_a_minute():
    va = _va()
    start = 1_800_000_000.0

    presence.seen(_fresh(va), start)
    presence.seen(_fresh(va), start + 30)
    half_minute = _fresh(va).last_seen_at
    presence.seen(_fresh(va), start + settings.web.seen_write_every_s)

    assert half_minute == start
    assert _fresh(va).last_seen_at == start + settings.web.seen_write_every_s


# --- the idle limit -----------------------------------------------------------


def test_a_va_is_out_after_two_idle_hours_and_a_user_is_not(clock):
    _va()
    va, owner = TestClient(app), TestClient(app)
    sign_in(va, VA, Role.VA)
    sign_in(owner, OWNER)

    clock[0] += 2 * _HOUR - 60
    still_in = va.put("/session", headers=POLL)  # an action: the two hours restart
    clock[0] += 2 * _HOUR - 60
    kept = va.get("/clone/gallery/latest", headers=POLL)
    clock[0] += 120
    out = va.get("/clone/gallery/latest", headers=POLL)

    assert (still_in.status_code, kept.status_code, out.status_code) == (204, 200, 401)
    assert owner.get("/clone/gallery/latest", headers=POLL).status_code == 200


def test_the_page_tells_the_tab_the_limit_of_who_is_signed_in():
    _va()
    va, owner = TestClient(app), TestClient(app)
    sign_in(va, VA, Role.VA)
    sign_in(owner, OWNER)

    assert 'data-session-max-age="7200"' in va.get("/clone").text
    assert 'data-session-max-age="18000"' in owner.get("/clone").text
    assert presence.idle_limit_s(Role.VA) == settings.web.va_session_max_age_s
    assert presence.idle_limit_s(None) == settings.session.session_max_age_s


# --- the record ---------------------------------------------------------------


def test_the_sweep_logs_an_idle_va_out_at_their_last_action_once():
    va = _va()
    client = TestClient(app)
    sign_in(client, VA, Role.VA)
    stopped = time.time() - 3 * _HOUR
    users.seen(va.id, stopped)

    idle.end_idle()
    idle.end_idle()

    assert _events(va, idle.IDLE_LOGOUT) == [pytest.approx(stopped, abs=0.001)]
    ended = _fresh(va).sessions_ended_at
    assert ended is not None
    assert ended > stopped
    # Every session of theirs is out, the cookie's own lifetime regardless.
    assert client.get("/clone/gallery/latest", headers=POLL).status_code == 401


def test_a_va_still_working_or_logged_out_and_an_idle_user_get_no_idle_logout():
    va = _va()
    owner = make_user(OWNER)
    users.seen(va.id, time.time() - _HOUR)  # within the two hours
    users.seen(owner.id, time.time() - 9 * _HOUR)  # not a VA

    idle.end_idle()
    working = _events(va, idle.IDLE_LOGOUT)
    users.seen(va.id, time.time() - 3 * _HOUR)
    signed_out(va.id)  # pressed Logout after that last action
    idle.end_idle()

    assert working == []
    assert _events(va, idle.IDLE_LOGOUT) == []
    assert _events(owner, idle.IDLE_LOGOUT) == []
    assert len(_events(va, "logout")) == 1


def test_signing_in_again_first_records_the_idle_stretch_before_it():
    """Back before the sweep's next round: the break's end is not lost, and
    the new session is not ended by it."""
    va = _va()
    stopped = time.time() - 5 * _HOUR
    users.seen(va.id, stopped)

    outcome = signed_in(_fresh(va), "/", "va")
    idle.end_idle()

    (idle_at,) = _events(va, idle.IDLE_LOGOUT)
    (login_at,) = _events(va, "login")
    assert idle_at == pytest.approx(stopped, abs=0.001)
    assert idle_at < login_at
    assert outcome.user is not None
    assert _fresh(va).last_seen_at == pytest.approx(time.time(), abs=5)


def test_the_sweep_and_a_sign_in_at_once_record_one_idle_logout():
    """Both read the VA as idle before either wrote: one event, not two."""
    va = _va()
    stopped = time.time() - 3 * _HOUR
    users.seen(va.id, stopped)
    as_the_sweep_read_them = _fresh(va)
    as_the_sign_in_read_them = _fresh(va)

    first = idle.end_if_idle(as_the_sweep_read_them, time.time())
    second = idle.end_if_idle(as_the_sign_in_read_them, time.time())

    assert (first, second) == (True, False)
    assert _events(va, idle.IDLE_LOGOUT) == [pytest.approx(stopped, abs=0.001)]


def test_the_idle_sweep_is_registered():
    (entry,) = [s for s in sweeps() if s.name == "va idle"]

    assert entry.run is idle.end_idle
    assert entry.interval_s is not None
    assert entry.interval_s() == settings.web.va_idle_sweep_s
    assert entry.queue is None
