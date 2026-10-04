"""
The action log's "view" link only renders when TASK_STATUS_PREFIX has an
entry for that job's task. Old jobs of a removed tool (replicate, taken out
with its router) stay in the jobs table, so they must get no link rather than
one pointing at a route that no longer exists.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.admin.action_log import _status_url
from ofmhelpers.web.stores.jobs import create_job

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def admin_client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_status_url_points_at_the_tool_router():
    assert _status_url({"task": "seedance", "id": "abc123"}) == "/seedance/jobs/abc123"


@pytest.mark.parametrize("task", ["replicate", "replicate_intake"])
def test_removed_replicate_jobs_get_no_view_link(task):
    assert _status_url({"task": task, "id": "abc123"}) is None


def test_dashboard_still_renders_with_a_leftover_replicate_job(admin_client):
    job_id = create_job("replicate_intake", {"source": "https://example.com/reel"})

    r = admin_client.get("/action-log")

    assert r.status_code == 200
    assert f"/replicate/jobs/{job_id}" not in r.text
