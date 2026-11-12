"""
/spending: what each VA's runs cost today, in the last 7 days and this month,
summed in SQL from `jobs.cost_usd` per `jobs.user_id`. A run is a job that
made a provider request; its cost is what the provider reported, else the
app's clone price (marked "est."), else unknown ("N runs without a price",
never $0). A user sees their own VAs, an admin every VA under its owner, a
VA cannot open the page. Days are the reader's chosen time zone's.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in
from sqlalchemy import update

from ofmhelpers.db.models import JobRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job, get_job, keep_cost
from ofmhelpers.features.jobs.cost import cost_of
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.spending import service
from ofmhelpers.features.spending.models import Amount, Period
from ofmhelpers.integrations.ai_providers import tasks
from ofmhelpers.schemas import Role, User, UserForm
from ofmhelpers.schemas.persistence import GenerationRecord, JobCost, JobMeta
from ofmhelpers.web.main import app

pytestmark = pytest.mark.filterwarnings("ignore")

OWNER = "owner@example.com"
WARSAW = ZoneInfo("Europe/Warsaw")
# A Sunday noon in Warsaw: today is Nov 15, the 7 days start Nov 9, the
# month Nov 1.
NOW = datetime(2026, 11, 15, 12, 0, tzinfo=WARSAW)
APIYI_CLONE = "apiyi:wan3.0-video"


def _va(email: str, owner: User, name: str = "") -> User:
    va = users.add_user(
        UserForm(email=email, role=Role.VA, name=name, owner_id=owner.id)
    )
    assert va is not None
    return va


def _client(email: str, role: Role = Role.USER) -> TestClient:
    client = TestClient(app)
    sign_in(client, email, role)
    return client


def _generation(cost_usd: float | None = None) -> GenerationRecord:
    return GenerationRecord(
        provider="kie", model="m", task_id="t", state="done", cost_usd=cost_usd
    )


def _run(
    user: User,
    at: datetime,
    usd: str | None,
    *,
    estimated: bool = False,
    task: str = "clone",
) -> str:
    """A run of `user`'s started at `at` that cost `usd` (None: no price)."""
    job_id = create_job(task, {}, actor=str(user.id))
    cost = JobCost(usd=None if usd is None else Decimal(usd), estimated=estimated)
    keep_cost(job_id, JobMeta(generations=[_generation()]), cost)
    with session_scope() as s:
        s.execute(
            update(JobRow).where(JobRow.id == job_id).values(created_at=at.timestamp())
        )
    return job_id


def _amounts(view, va: User) -> dict[Period, Amount]:
    (row,) = [row for owner in view.owners for row in owner.vas if row.user_id == va.id]
    return {column.period: column.amount for column in row.columns}


def _view(owner: User, va: User, now: datetime = NOW, zone: str = "Europe/Warsaw"):
    return service.page_view(
        owner.id, {owner.id, va.id}, everyone=False, zone=zone, now=now
    )


def _at(day: int, hour: int, minute: int = 0, second: int = 0, month: int = 11):
    return datetime(2026, month, day, hour, minute, second, tzinfo=WARSAW)


# --- periods ----------------------------------------------------------------------


def test_each_period_starts_at_its_midnight_and_sums_what_is_inside():
    owner = make_user(OWNER)
    va = _va("va@example.com", owner)
    _run(va, _at(15, 0), "1")  # today's first second
    _run(va, _at(14, 23, 59, 59), "2")  # yesterday's last
    _run(va, _at(9, 0), "4")  # the 7 days' first second
    _run(va, _at(8, 23, 59, 59), "8")  # a second before them
    _run(va, _at(1, 0), "16")  # the month's first second
    _run(va, _at(31, 23, 59, 59, month=10), "32")  # last month's last

    amounts = _amounts(_view(owner, va), va)

    assert (amounts[Period.TODAY].runs, amounts[Period.TODAY].usd) == (1, Decimal(1))
    assert (amounts[Period.WEEK].runs, amounts[Period.WEEK].usd) == (3, Decimal(7))
    assert (amounts[Period.MONTH].runs, amounts[Period.MONTH].usd) == (5, Decimal(31))


def test_a_run_later_today_counts_and_tomorrows_does_not():
    owner = make_user(OWNER)
    va = _va("va@example.com", owner)
    _run(va, _at(15, 23, 59, 59), "1")
    _run(va, _at(16, 0), "2")

    today = _amounts(_view(owner, va), va)[Period.TODAY]

    assert (today.runs, today.usd) == (1, Decimal(1))


def test_days_are_the_readers_time_zones():
    """21:00 on the 14th in Warsaw is 01:30 on the 15th in Ambala."""
    owner = make_user(OWNER)
    va = _va("va@example.com", owner)
    _run(va, _at(14, 21), "3")

    warsaw = _amounts(_view(owner, va), va)[Period.TODAY]
    ambala = _amounts(_view(owner, va, zone="Asia/Kolkata"), va)[Period.TODAY]

    assert (warsaw.runs, ambala.runs, ambala.usd) == (0, 1, Decimal(3))
    assert _view(owner, va, zone="Asia/Kolkata").zone == "Ambala"
    assert _view(owner, va, zone="no/such-zone").zone == "Warsaw"


# --- reported, estimated, no price ----------------------------------------------


def test_reported_estimated_and_unpriced_runs_are_told_apart():
    owner = make_user(OWNER)
    va = _va("va@example.com", owner)
    _run(va, _at(15, 9), "1.25")
    _run(va, _at(15, 10), "0.5", estimated=True)
    _run(va, _at(15, 11), None)
    _run(va, _at(15, 11), None)

    today = _amounts(_view(owner, va), va)[Period.TODAY]

    assert today == Amount(
        runs=4, usd=Decimal("1.75"), estimated_usd=Decimal("0.5"), unpriced=2
    )
    assert today.dollars == "$1.75"
    assert today.estimate_label == "$0.50 est."
    assert today.unpriced_label == "2 runs without a price"


def test_runs_without_any_price_show_no_amount_never_zero():
    owner = make_user(OWNER)
    va = _va("va@example.com", owner)
    _run(va, _at(15, 9), None)

    today = _amounts(_view(owner, va), va)[Period.TODAY]

    assert (today.runs, today.unpriced, today.priced) == (1, 1, False)
    assert today.dollars == "—"
    assert today.unpriced_label == "1 run without a price"


def test_a_job_that_asked_no_provider_is_not_a_run():
    owner = make_user(OWNER)
    va = _va("va@example.com", owner)
    job_id = create_job("download_media", {"urls": ["u"]}, actor=str(va.id))
    run_job(job_id, list, {})

    view = service.page_view(owner.id, {owner.id, va.id}, everyone=False, zone=None)

    assert _amounts(view, va)[Period.MONTH] == Amount()


def test_a_provider_that_reports_a_cost_is_believed():
    params = {"provider": "apiyi", "model": APIYI_CLONE, "resolution": "720P"}
    cost = cost_of({**params, "duration": 10}, [_generation(0.4), _generation(0.25)])

    assert cost == JobCost(usd=Decimal("0.65"), estimated=False)


def test_a_clone_run_without_a_reported_cost_gets_the_clone_price(monkeypatch):
    """APIYI reports nothing: 10 s of source at 720P at the price /clone's
    panel shows ($0.1176 a source second)."""
    monkeypatch.setenv("OFM_CLONE_APIYI_USD_PER_VIDEO_S", '{"720P": 0.1176}')
    params = {"provider": "apiyi", "model": APIYI_CLONE, "resolution": "720P"}

    assert cost_of({**params, "duration": 10}, [_generation()]) == JobCost(
        usd=Decimal("1.1760"), estimated=True
    )
    # /clone-long and /clone-links name the source's length differently.
    assert cost_of({**params, "seconds": 20.0}, [_generation()]).usd == Decimal("2.352")
    assert cost_of({**params, "source_seconds": 5}, [_generation()]).usd == Decimal(
        "0.588"
    )


@pytest.mark.parametrize(
    "params",
    [
        {"provider": "apiyi", "model": "apiyi:gemini-3-pro-image", "resolution": "2K"},
        {"provider": "apiyi", "model": APIYI_CLONE, "resolution": "720P"},
        {"provider": "apiyi", "model": APIYI_CLONE, "resolution": "8K", "duration": 5},
        {"provider": "nobody", "model": "x", "resolution": "720P", "duration": 5},
        {"prompt": "p"},
    ],
    ids=["another model", "no length", "no such resolution", "no provider", "bare"],
)
def test_a_run_the_app_has_no_price_for_has_no_cost(params):
    assert cost_of(params, [_generation()]) == JobCost(usd=None, estimated=False)


def test_a_request_that_was_never_made_is_not_estimated():
    params = {
        "provider": "apiyi",
        "model": APIYI_CLONE,
        "resolution": "720P",
        "duration": 10,
    }
    failed = GenerationRecord(provider="apiyi", model="m", task_id="t", state="failed")

    assert cost_of(params, [failed]) == JobCost()


def test_a_finished_job_keeps_its_estimate_in_its_row(monkeypatch):
    """Through `run_job`: the remembered request, settled without a cost,
    becomes the job's estimated `cost_usd`."""
    monkeypatch.setenv("OFM_CLONE_APIYI_USD_PER_VIDEO_S", '{"720P": 0.1176}')
    va = make_user()
    params = {
        "provider": "apiyi",
        "model": APIYI_CLONE,
        "resolution": "720P",
        "duration": 10,
    }
    job_id = create_job("clone", params, actor=str(va.id))
    tasks.remember(
        tasks.ProviderTask(
            job_id=job_id,
            provider="apiyi",
            model="wan3.0-video",
            task_id="t1",
            api_key="k",
        )
    )
    tasks.settle(job_id, 0, "done", None)

    run_job(job_id, list, {})

    job = get_job(job_id)
    assert (job.cost_usd, job.cost_estimated) == (Decimal("1.1760"), True)
    assert [g.cost_usd for g in job.meta.generations] == [None]


# --- who sees whom -----------------------------------------------------------------


def _rows(html: str) -> list[str]:
    return re.findall(r'<tr data-va="(\d+)">', html)


def _cell(html: str, va: User, period: Period) -> str:
    row = re.search(rf'<tr data-va="{va.id}">.*?</tr>', html, re.DOTALL)
    assert row, f"no row of VA {va.id}"
    cell = re.search(
        rf'<td data-period="{period.value}">(.*?)</td>', row.group(0), re.DOTALL
    )
    assert cell
    return " ".join(re.sub(r"<[^>]+>", " ", cell.group(1)).split())


def _today() -> datetime:
    return datetime.now(WARSAW).replace(hour=0, minute=0, second=0, microsecond=0)


def test_a_user_sees_only_their_own_vas_with_money_and_marks():
    owner, other = make_user(OWNER), make_user("other@example.com")
    mine = _va("va@example.com", owner, "Vera")
    theirs = _va("their-va@example.com", other, "Theo")
    _run(mine, _today(), "2.5")
    _run(mine, _today(), "1", estimated=True)
    _run(mine, _today(), None)
    _run(theirs, _today(), "99")

    r = _client(OWNER).get("/spending")

    assert r.status_code == 200
    assert _rows(r.text) == [str(mine.id)]
    assert "Vera" in r.text
    assert "Theo" not in r.text
    assert "$99" not in r.text
    assert (
        _cell(r.text, mine, Period.TODAY)
        == "$3.50 $1.00 est. 3 runs · 1 run without a price"
    )
    assert _cell(r.text, mine, Period.MONTH).startswith("$3.50")
    assert "Days in Warsaw time" in r.text


def test_an_all_estimated_amount_is_marked_est():
    owner = make_user(OWNER)
    va = _va("va@example.com", owner)
    _run(va, _today(), "4", estimated=True)

    r = _client(OWNER).get("/spending")

    assert _cell(r.text, va, Period.TODAY) == "$4.00 est. 1 run"


def test_the_time_zone_cookie_names_the_days():
    owner = make_user(OWNER)
    _va("va@example.com", owner)
    client = _client(OWNER)
    client.cookies.set("ofm_timezone", "Asia/Kolkata")

    assert "Days in Ambala time" in client.get("/spending").text


def test_an_admin_sees_every_va_under_its_owner():
    ann, bob = make_user("ann@example.com"), make_user("bob@example.com")
    a = _va("a@example.com", ann, "Va A")
    b = _va("b@example.com", bob, "Va B")
    _run(a, _today(), "1")
    _run(b, _today(), "2")

    r = _client("boss@example.com", Role.ADMIN).get("/spending")

    assert r.status_code == 200
    assert _rows(r.text) == [str(a.id), str(b.id)]
    assert re.findall(r"<h2 data-owner>([^<]+)</h2>", r.text) == [
        "ann@example.com",
        "bob@example.com",
    ]
    assert _cell(r.text, a, Period.TODAY) == "$1.00 1 run"
    assert _cell(r.text, b, Period.TODAY) == "$2.00 1 run"


def test_a_va_cannot_open_spending():
    owner = make_user(OWNER)
    _va("va@example.com", owner)

    r = _client("va@example.com", Role.VA).get("/spending")

    assert r.status_code == 403


def test_a_user_with_no_va_sees_the_empty_state():
    make_user(OWNER)

    r = _client(OWNER).get("/spending")

    assert r.status_code == 200
    assert re.search(r'<p class="empty-state">No VAs yet</p>', r.text)
    assert _rows(r.text) == []


def test_spending_is_in_the_more_menu_of_users_not_of_vas():
    owner = make_user(OWNER)
    _va("va@example.com", owner)

    assert 'href="/spending"' in _client(OWNER).get("/home").text
    assert (
        'href="/spending"' not in _client("va@example.com", Role.VA).get("/home").text
    )
