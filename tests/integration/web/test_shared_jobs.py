"""
A VA's jobs are their owner's to see (`jobs.user_id`, `Viewer.actors`): the
owner's galleries show them with the VA's name, and their status page and
files open. Reading only: hiding a card stays its maker's own choice, so the
owner's card of a VA's job offers no Hide and the route answers 404. The VA
sees nothing of the owner's, a third person nothing of either, and a job
with no owner is nobody's.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from signin import make_user, sign_in

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job, get_job, log_event
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.schemas import Role, User, UserForm
from ofmhelpers.web.main import app

pytestmark = pytest.mark.filterwarnings("ignore")

OWNER = "owner@example.com"
VA = "va@example.com"
OTHER = "other@example.com"
VA_NAME = "Vera the VA"


class Team(BaseModel):
    owner: User
    va: User


@pytest.fixture
def team() -> Team:
    owner = make_user(OWNER)
    return Team(owner=owner, va=_va(VA, VA_NAME, owner))


def _va(email: str, name: str, owner: User) -> User:
    va = users.add_user(
        UserForm(email=email, role=Role.VA, name=name, owner_id=owner.id)
    )
    assert va is not None
    return va


def _client(email: str, role: Role = Role.USER) -> TestClient:
    client = TestClient(app)
    sign_in(client, email, role)
    return client


def _done(task: str, user: User, out: Path) -> str:
    out.write_bytes(b"png")
    job_id = create_job(task, {"prompt": out.stem}, actor=str(user.id))
    run_job(job_id, lambda: [{"name": out.name, "path": str(out)}], {})
    return job_id


def _card(html: str, job_id: str) -> str:
    """The markup of `job_id`'s card."""
    found = re.search(
        rf'<div class="result-item" data-job-id="{job_id}".*?(?=<div class="result-item"|\Z)',
        html,
        re.DOTALL,
    )
    assert found, f"no card of job {job_id}"
    return found.group(0)


def _ids(html: str) -> list[str]:
    return re.findall(r'class="result-item" data-job-id="([^"]+)"', html)


PAGES = [
    ("/clone", "/clone/gallery", "/clone/gallery/latest", "clone"),
    ("/generate", "/generate/gallery", "/generate/gallery/latest", "fake_ai"),
]


@pytest.mark.parametrize(("page", "fragment", "latest", "task"), PAGES)
def test_an_owner_sees_their_vas_card_with_the_vas_name(
    team, tmp_path, page, fragment, latest, task
):
    theirs = _done(task, team.va, tmp_path / "va.png")
    mine = _done(task, team.owner, tmp_path / "owner.png")
    owner = _client(OWNER)

    for url in (page, fragment, latest):
        html = owner.get(url).text
        assert set(_ids(html)) == {theirs, mine}, url
        assert f'<p class="source" data-made-by>Done by VA: {VA_NAME}</p>' in _card(
            html, theirs
        ), url
        assert VA_NAME not in _card(html, mine), url


def test_an_owner_opens_their_vas_status_and_file(team, tmp_path):
    job = _done("clone", team.va, tmp_path / "va.png")
    owner = _client(OWNER)

    assert owner.get(f"/clone/jobs/{job}").status_code == 200
    status = owner.get(f"/clone/jobs/{job}/status")
    assert (status.status_code, status.json()["status"]) == (200, "done")
    file = owner.get(f"/clone/files/{job}/0")
    assert (file.status_code, file.content) == (200, b"png")


def test_a_va_sees_their_own_card_unsigned_and_nothing_of_the_owners(team, tmp_path):
    theirs = _done("clone", team.va, tmp_path / "va.png")
    owners = _done("clone", team.owner, tmp_path / "owner.png")
    va = _client(VA, Role.VA)

    html = va.get("/clone").text
    assert _ids(html) == [theirs]
    assert VA_NAME not in _card(html, theirs)
    assert va.get(f"/clone/jobs/{theirs}/status").json()["status"] == "done"
    assert va.get(f"/clone/jobs/{owners}").status_code == 404
    assert va.get(f"/clone/jobs/{owners}/status").status_code == 404
    assert va.get(f"/clone/files/{owners}/0").status_code == 404


def test_a_third_person_sees_neither(team, tmp_path):
    jobs = [
        _done("clone", team.va, tmp_path / "va.png"),
        _done("clone", team.owner, tmp_path / "owner.png"),
    ]
    other = _client(OTHER)

    assert _ids(other.get("/clone").text) == []
    assert _ids(other.get("/clone/gallery/latest").text) == []
    for job in jobs:
        assert other.get(f"/clone/jobs/{job}").status_code == 404
        assert other.get(f"/clone/files/{job}/0").status_code == 404
        assert other.get(f"/clone/files/{job}/0/preview").status_code == 404
        assert other.get(f"/clone/files/{job}/0/poster").status_code == 404


def test_another_owners_va_is_not_shared(team, tmp_path):
    """Sharing follows `users.owner_id`, not the role."""
    boss = make_user("second@example.com")
    stranger = _va("their-va@example.com", "", boss)
    job = _done("clone", stranger, tmp_path / "x.png")
    owner = _client(OWNER)

    assert _ids(owner.get("/clone").text) == []
    assert owner.get(f"/clone/jobs/{job}").status_code == 404


def test_hiding_is_the_makers_own_choice(team, tmp_path):
    """The owner cannot hide the VA's job (404, nothing changes) and their
    card of it offers no Hide; the VA hides it, and it leaves the owner's
    gallery too, counted under the owner's Show hidden."""
    job = _done("clone", team.va, tmp_path / "va.png")
    mine = _done("clone", team.owner, tmp_path / "owner.png")
    owner, va = _client(OWNER), _client(VA, Role.VA)

    refused = owner.post(f"/jobs/{job}/hidden", json={"hidden": True})
    assert refused.status_code == 404
    assert get_job(job).hidden_at is None
    html = owner.get("/clone/gallery").text
    assert "hide-btn" not in _card(html, job)
    assert "hide-btn" in _card(html, mine)

    assert va.post(f"/jobs/{job}/hidden", json={"hidden": True}).status_code == 200
    html = owner.get("/clone").text
    assert _ids(html) == [mine]
    assert "Show hidden (1)" in html
    assert _ids(owner.get("/clone/gallery?hidden=1").text) == [job]
    assert owner.post(f"/jobs/{job}/hidden", json={"hidden": False}).status_code == 404
    assert get_job(job).hidden_at is not None


def test_a_job_with_no_owner_is_nobodys(team):
    job = log_event("clone", actor=None, params={"prompt": "system"})
    assert get_job(job).user_id is None

    for client in (_client(OWNER), _client(VA, Role.VA), _client(OTHER)):
        assert job not in _ids(client.get("/clone").text)
        assert client.get(f"/clone/jobs/{job}").status_code == 404
        assert client.get(f"/clone/jobs/{job}/status").status_code == 404
    assert get_job(job, actor=str(team.owner.id)) is None
    assert get_job(job, actor=frozenset({str(team.owner.id), str(team.va.id)})) is None


def test_an_actor_that_is_no_user_id_stores_and_reads_nobodys_job(team):
    """A system name never becomes an owner, and never reads an ownerless
    job back by it."""
    job = log_event("sweep", actor="system")

    assert get_job(job).user_id is None
    assert get_job(job).actor is None
    assert get_job(job, actor="system") is None


def test_a_user_who_has_jobs_is_deactivated_not_deleted(team, tmp_path):
    """`jobs.user_id` is ON DELETE RESTRICT: a job never loses its owner."""
    job = _done("clone", team.va, tmp_path / "va.png")
    admin = _client("boss@example.com", Role.ADMIN)

    refused = admin.post(f"/users/{team.va.id}/delete")

    assert refused.status_code == 409
    assert "still owns jobs" in refused.text
    assert users.get_user(team.va.id) is not None
    assert get_job(job).user_id == team.va.id
