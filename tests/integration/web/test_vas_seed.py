"""
`python -m ofmhelpers.features.vas.seed`: a VA's made-up month for
development (owner 2026-11-14: fake runs "accorting toi real prices" to see
how /vas and the galleries look). Rows only, priced by the app's own prices,
copied from the team's real finished jobs; `clear` takes back exactly what
`seed` wrote; production refuses both.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient
from signin import make_user, sign_in
from sqlalchemy import func, select

from ofmhelpers.db.models import JobRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job, get_job, keep_cost
from ofmhelpers.features.clone.pricing import clone_pricing
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.vas import seed as made_up
from ofmhelpers.features.vas import service
from ofmhelpers.features.vas.models import Period
from ofmhelpers.schemas import Role, User
from ofmhelpers.schemas.persistence import GenerationRecord, JobCost, JobMeta
from ofmhelpers.web.main import app

pytestmark = pytest.mark.filterwarnings("ignore")

OWNER = "owner@example.com"
WARSAW = ZoneInfo("Europe/Warsaw")
# A Sunday, late: every made-up day is whole.
NOW = datetime(2026, 11, 15, 23, 30, tzinfo=WARSAW)


@pytest.fixture(autouse=True)
def uploads(tmp_path, monkeypatch) -> Path:
    monkeypatch.setenv("OFM_UPLOADS_ROOT", str(tmp_path / "uploads"))
    return tmp_path


def _va() -> User:
    owner = make_user(OWNER)
    va = users.set_owner(make_user("va@example.com", Role.VA).id, owner.id)
    assert va is not None
    return va


def _real(task: str, user: User, folder: Path, params: dict, usd: str | None = None):
    """A real finished job of `user` with one result file."""
    file = folder / f"{task}-{user.id}.mp4"
    file.write_bytes(b"\x00\x00\x00\x18ftypmp42 fake")
    job_id = create_job(task, params, actor=str(user.id))
    run_job(job_id, lambda: [{"name": file.name, "path": str(file)}], {})
    if usd is not None:
        generation = GenerationRecord(
            provider="kie", model="m", task_id="t", cost_usd=float(usd)
        )
        keep_cost(job_id, JobMeta(generations=[generation]), JobCost(usd=Decimal(usd)))
    return job_id


def _team_with_work(folder: Path) -> User:
    va = _va()
    owner = users.by_email(OWNER)
    assert owner is not None
    clone = {"prompt": "p", "reference_images": [], "reference_videos": ["v.mp4"]}
    _real("clone", owner, folder, clone)
    _real("ai", owner, folder, {"provider": "kie", "model": "kie:x"}, usd="0.0325")
    _real("ai", va, folder, {"provider": "apiyi", "model": "apiyi:gemini-3-pro-image"})
    return va


def _jobs() -> list[JobRow]:
    with session_scope() as s:
        rows = list(s.scalars(select(JobRow).order_by(JobRow.created_at)))
        s.expunge_all()
        return rows


def _made_up() -> list[JobRow]:
    return [row for row in _jobs() if row.id.startswith(made_up.PREFIX)]


def test_production_refuses_to_make_or_clear_anything(monkeypatch):
    va = _va()
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("OFM_FAKE_EXTERNALS", "0")

    with pytest.raises(RefusedError, match="development only"):
        made_up.seed(va.email, NOW)
    with pytest.raises(RefusedError, match="development only"):
        made_up.clear()
    assert _made_up() == []


def test_only_a_va_gets_a_made_up_month():
    make_user(OWNER)

    with pytest.raises(NotFoundError, match="No VA named"):
        made_up.seed(OWNER, NOW)


def test_a_made_up_month_is_the_vas_rows_priced_by_the_apps_own_prices(uploads):
    va = _team_with_work(uploads)

    found = made_up.seed(va.email, NOW)

    rows = _made_up()
    assert {row.user_id for row in rows} == {va.id}
    assert len(rows) == (
        found.clones
        + found.failed_clones
        + found.generations
        + found.downloads
        + found.events
    )
    assert (found.days, found.downloads) == (len(made_up.DAYS_BACK), 0)
    assert min(found.clones, found.failed_clones, found.generations) > 0
    assert all(row.created_at <= NOW.timestamp() for row in rows)
    # Every clone: its provider's /clone price for its length, kie.ai's as
    # reported, the others as the app's estimate. No row without a price.
    prices = clone_pricing().entries
    clones = [r for r in rows if r.task == "clone" and r.status == "done"]
    assert {r.params["provider"] for r in clones} == set(prices)
    for row in clones:
        price = prices[row.params["provider"]]
        per_second = price.per_second("720P") * price.usd_per_credit
        expected = Decimal(str(per_second * row.params["duration"])).quantize(
            Decimal("0.0001")
        )
        assert row.cost_usd == expected, row.params["provider"]
        assert row.cost_estimated is (row.params["provider"] != "kie")
        assert row.result, "a card needs its video"
    # /generate: a model with a known price each, the Higgsfield video by
    # the /clone table (an estimate), the others as a price per run.
    generated = [r for r in rows if r.task == "ai"]
    by_model = {r.params["model"]: r for r in generated}
    assert set(by_model) == {model.model for model in made_up.GENERATED}
    assert by_model["kie:seedream/4.5-edit"].cost_usd == Decimal("0.0325")
    assert by_model["apiyi:gemini-3-pro-image"].cost_usd == Decimal("0.0900")
    assert by_model["apiyi:gemini-3.1-flash-image"].cost_usd == Decimal("0.0550")
    video = by_model["higgsfield:alibaba/wan-3.0/reference-to-video"]
    assert video.cost_estimated is True
    assert video.cost_usd is not None
    assert video.cost_usd > 0
    assert all(r.cost_usd is not None and r.result for r in generated)
    failed = [r for r in rows if r.status == "failed"]
    assert {(r.error, r.cost_usd) for r in failed} == {(made_up.FLAGGED, None)}


def test_the_owner_sees_the_made_up_days_and_money_on_the_page(uploads):
    va = _team_with_work(uploads)
    owner = users.by_email(OWNER)
    assert owner is not None
    made_up.seed(va.email, NOW)
    client = TestClient(app)
    sign_in(client, OWNER)

    view = service.page_view(
        owner.id, {owner.id, va.id}, everyone=False, zone="Europe/Warsaw", now=NOW
    )

    # Nov 15 back to Nov 3: the days of this month among the made-up ones.
    assert [day.day.day for day in view.days] == [15, 14, 13, 12, 10, 9, 6, 3]
    today, yesterday = view.days[0], view.days[1]
    assert today.logged_in.startswith("09:")
    assert not today.logged_out.endswith("idle")
    assert yesterday.logged_out.endswith("· idle")
    assert all(day.reels >= 4 and day.spent.startswith("$") for day in view.days)
    month = next(c.amount for c in view.totals if c.period == Period.MONTH)
    assert (month.priced, month.unpriced) == (True, 0)
    # Their cards are in the owner's /clone gallery, named as the VA's.
    assert "Done by VA: " in client.get("/clone").text
    assert client.get("/vas").status_code == 200


def test_clear_takes_back_exactly_what_was_made_and_seeding_twice_adds_nothing(
    uploads,
):
    va = _team_with_work(uploads)
    real = {row.id for row in _jobs()}

    first = made_up.seed(va.email, NOW)
    again = made_up.seed(va.email, NOW)
    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(JobRow))
    cleared = made_up.clear()

    assert again == first
    assert total == len(real) + cleared.jobs
    assert {row.id for row in _jobs()} == real
    assert all(get_job(job_id) is not None for job_id in real)
    assert made_up.clear().jobs == 0


def test_a_team_with_no_finished_work_still_gets_its_logins_and_logouts():
    va = _va()

    found = made_up.seed(va.email, NOW)

    assert (found.clones, found.generations, found.downloads) == (0, 0, 0)
    assert found.events == 2 * len(made_up.DAYS_BACK)
    assert {row.task for row in _made_up()} == {"login", "logout", "idle_logout"}
