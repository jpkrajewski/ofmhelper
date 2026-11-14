"""
What the owner is told about a VA (features/vas/notices.py, owner
2026-11-14): the VA signed in; every 10 reels the VA finished that day;
every $10 the VA's day cost. Each step goes out under its own key (the bus
sends a key once), only a VA's work counts, and a notice that cannot be
made never fails the job.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from decimal import Decimal

import pytest
from signin import make_user
from sqlalchemy import update

from ofmhelpers.db.models import JobRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job, get_job, keep_cost
from ofmhelpers.features.events import Event
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.signin.outcome import signed_in
from ofmhelpers.features.vas import notices
from ofmhelpers.schemas import Role, User
from ofmhelpers.schemas.persistence import GenerationRecord, JobCost, JobMeta

pytestmark = pytest.mark.filterwarnings("ignore")

OWNER = "owner@example.com"


@pytest.fixture
def sent(monkeypatch) -> list[Event]:
    """Every event handed to the bus."""
    out: list[Event] = []
    monkeypatch.setattr(notices, "emit", out.append)
    return out


def _va() -> User:
    owner = make_user(OWNER)
    va = users.set_owner(make_user("va@example.com", Role.VA).id, owner.id)
    assert va is not None
    return va


def _finish(user: User, task: str = "clone") -> str:
    """A job of `user`'s run to its end by the real runner."""
    job_id = create_job(task, {}, actor=str(user.id))
    run_job(job_id, list, {})
    return job_id


def _fail(user: User) -> str:
    def refused() -> None:
        msg = "Flagged content"
        raise RuntimeError(msg)

    job_id = create_job("clone", {}, actor=str(user.id))
    run_job(job_id, refused, {})
    return job_id


def _costing(user: User, usd: str, task: str = "ai") -> str:
    """A finished job of `user`'s that cost `usd`."""
    job_id = create_job(task, {}, actor=str(user.id))
    generation = GenerationRecord(provider="kie", model="m", task_id="t")
    keep_cost(job_id, JobMeta(generations=[generation]), JobCost(usd=Decimal(usd)))
    with session_scope() as s:
        s.execute(update(JobRow).where(JobRow.id == job_id).values(status="done"))
    return job_id


def _named(sent: list[Event], name: str) -> list[Event]:
    return [event for event in sent if event.name == name]


def test_a_va_signing_in_is_told_and_anyone_else_is_not(sent):
    va = _va()
    owner = users.by_email(OWNER)
    assert owner is not None

    signed_in(va, "/", "va")
    signed_in(owner, "/", "password")

    (event,) = sent
    assert (event.name, event.title) == ("vas.login", f"{va.label} logged in")
    assert (event.text, event.url, event.key) == (f"VA of {owner.label}", "/vas", None)


def test_the_tenth_reel_of_the_day_is_told_and_the_ninth_is_not(sent):
    va = _va()
    for _ in range(9):
        _finish(va)
    before = list(sent)
    _fail(va)  # no reel
    _finish(va, task="download_media")  # no reel either
    still = list(sent)

    _finish(va)

    assert (before, still) == ([], [])
    (event,) = sent
    day = notices._today(va)
    assert day is not None
    assert (event.name, event.title) == ("vas.reels", f"{va.label}: 10 reels today")
    assert event.key == f"vas.reels:{va.id}:{day.day}:10"
    assert event.url == "/vas"


def test_the_same_step_keeps_its_key_and_the_next_step_gets_a_new_one(sent):
    va = _va()
    for _ in range(21):
        _finish(va)

    keys = [event.key or "" for event in _named(sent, "vas.reels")]

    assert len(set(keys)) == 2
    assert keys[0].endswith(":10")
    assert keys[9].endswith(":10")
    assert keys[-1].endswith(":20")
    assert keys[-2].endswith(":20")


def test_every_ten_dollars_of_the_day_is_told(sent):
    va = _va()

    notices.after_job(_costing(va, "6"))
    under = list(sent)
    notices.after_job(_costing(va, "5.5"))
    notices.after_job(_costing(va, "9"))

    assert under == []
    first, second = _named(sent, "vas.spend")
    assert first.title == f"{va.label}: over $10 spent today"
    assert first.text.endswith("$11.50 so far")
    assert first.key is not None
    assert first.key.endswith(":10")
    assert second.title == f"{va.label}: over $20 spent today"
    assert second.key is not None
    assert second.key.endswith(":20")
    assert _named(sent, "vas.reels") == []


def test_an_unpriced_run_and_an_owners_work_tell_nothing(sent):
    va = _va()
    owner = users.by_email(OWNER)
    assert owner is not None
    for _ in range(10):
        _finish(owner)
    notices.after_job(_costing(owner, "50"))
    notices.after_job(_finish(va, task="ai"))  # no price, no reel

    assert sent == []


def test_a_notice_that_cannot_be_made_leaves_the_job_done(monkeypatch, caplog):
    va = _va()

    def broken(_event: Event) -> None:
        msg = "bus down"
        raise RuntimeError(msg)

    monkeypatch.setattr(notices, "emit", broken)
    monkeypatch.setenv("OFM_VA_REELS_NOTICE_EVERY", "1")

    with caplog.at_level("WARNING", logger="ofmhelpers.features.vas.notices"):
        job_id = _finish(va)

    job = get_job(job_id)
    assert job is not None
    assert job.status == "done"
    assert f"va notices of job {job_id} not made" in caplog.text
