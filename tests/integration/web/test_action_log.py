import pytest
from fastapi.testclient import TestClient

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.schemas import Job
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.admin.action_log import _status_url

pytestmark = pytest.mark.filterwarnings("ignore")


def _job(task: str) -> Job:
    return Job(id="abc123", task=task, created_at=0.0)


@pytest.fixture
def admin_client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_status_url_points_at_the_tool_router():
    assert _status_url(_job("seedance")) == "/seedance/jobs/abc123"


@pytest.mark.parametrize("task", ["replicate", "replicate_intake"])
def test_removed_replicate_jobs_get_no_view_link(task):
    assert _status_url(_job(task)) is None


def test_dashboard_still_renders_with_a_leftover_replicate_job(admin_client):
    job_id = create_job("replicate_intake", {"source": "https://example.com/reel"})

    r = admin_client.get("/action-log")

    assert r.status_code == 200
    assert f"/replicate/jobs/{job_id}" not in r.text


def test_another_users_row_is_metadata_only(admin_client):
    from signin import make_user

    user = make_user("audited@example.com")
    theirs = create_job("seedance", {"prompt": "their private prompt"}, str(user.id))
    agency = str(users.agency_id())
    mine = create_job("seedance", {"prompt": "my own prompt"}, agency)

    html = admin_client.get("/action-log").text

    assert "audited@example.com" in html
    assert "their private prompt" not in html
    assert f"/seedance/jobs/{theirs}" not in html
    assert "my own prompt" in html
    assert f"/seedance/jobs/{mine}" in html
