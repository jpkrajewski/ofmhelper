"""
The action log's "view" link only renders when TASK_STATUS_PREFIX has an
entry for that job's task -- replicate's two-stage flow (replicate_intake,
then replicate) was missing both entries, so jobs from /replicate never got
a working "view" link on this admin dashboard.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.routers.admin.action_log import TASK_STATUS_PREFIX, _status_url
from ofmhelpers.web.stores.jobs import create_job

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def admin_client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def test_replicate_intake_and_replicate_have_a_status_prefix():
    assert TASK_STATUS_PREFIX["replicate_intake"] == "/replicate"
    assert TASK_STATUS_PREFIX["replicate"] == "/replicate"


def test_status_url_points_at_the_replicate_router_for_both_stages():
    intake_job = {"task": "replicate_intake", "id": "abc123"}
    generate_job = {"task": "replicate", "id": "def456"}
    assert _status_url(intake_job) == "/replicate/jobs/abc123"
    assert _status_url(generate_job) == "/replicate/jobs/def456"


def test_dashboard_renders_a_view_link_for_a_replicate_intake_job(admin_client):
    job_id = create_job("replicate_intake", {"source": "https://example.com/reel"})

    html = admin_client.get("/action-log").text

    assert f"/replicate/jobs/{job_id}" in html
    assert "view" in html
