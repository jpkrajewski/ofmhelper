"""
Covers /replicate end to end at the router level, with reel_machine's own
pipeline/generation calls mocked out (no real download/ffmpeg/whisper/kie.ai
calls) -- mirrors the mocking style tests/test_generate_reuse.py already
uses for seedance/kling3/nanobanana.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.reel_machine.pipeline import DraftResult
from ofmhelpers.web.jobs import get_job
from ofmhelpers.web.main import app

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _mock_intake_result(tmp_path, duration=15.0):
    contact_sheet = tmp_path / "contact-sheet.jpg"
    contact_sheet.write_bytes(b"fake jpg")
    return mock.Mock(
        contact_sheet=contact_sheet,
        transcript=mock.Mock(text="hi there, watch this"),
        video_path=tmp_path / "reference.mp4",
        duration=duration,
    )


def test_form_page_lists_shapes_and_looks(client):
    html = client.get("/replicate").text
    assert "Solo UGC monologue" in html
    assert "Phone front-cam selfie" in html


def test_intake_requires_a_source(client):
    r = client.post("/replicate/intake", data={})
    assert r.status_code == 400


def test_intake_creates_a_job_and_drafts_a_script(client, tmp_path):
    with (
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.intake_reel",
            return_value=_mock_intake_result(tmp_path),
        ),
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.draft_script_full",
            return_value=DraftResult(script="DRAFT SCRIPT TEXT"),
        ),
    ):
        r = client.post(
            "/replicate/intake",
            data={
                "source_url": "https://example.com/reel",
                "shape": "solo_monologue",
                "look": "phone_selfie",
                "llm_provider": "template",
            },
        )

    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = get_job(job_id)
    assert job["task"] == "replicate_intake"
    assert job["status"] == "done"
    assert job["result"]["draft_script"] == "DRAFT SCRIPT TEXT"
    assert job["result"]["duration"] == 15  # auto-detected from the source reel


def test_intake_threads_target_and_gender_to_draft_script(client, tmp_path):
    with (
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.intake_reel",
            return_value=_mock_intake_result(tmp_path),
        ),
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.draft_script_full",
            return_value=DraftResult(script="DRAFT"),
        ) as mock_draft_script,
    ):
        job_id = client.post(
            "/replicate/intake",
            data={
                "source_url": "https://example.com/reel",
                "target": "confident fitness coach pitching a program",
                "gender": "male",
            },
        ).json()["job_id"]

    assert mock_draft_script.call_args.kwargs["target"] == (
        "confident fitness coach pitching a program"
    )
    assert mock_draft_script.call_args.kwargs["gender"] == "male"
    job = get_job(job_id)
    assert job["result"]["target"] == "confident fitness coach pitching a program"
    assert job["result"]["gender"] == "male"


def test_form_page_lists_genders(client):
    html = client.get("/replicate").text
    assert 'name="gender"' in html
    assert 'name="target"' in html
    assert "Male" in html


def test_review_page_renders_the_draft_script_editable(client, tmp_path):
    with (
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.intake_reel",
            return_value=_mock_intake_result(tmp_path),
        ),
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.draft_script_full",
            return_value=DraftResult(script="MY DRAFT SCRIPT"),
        ),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "MY DRAFT SCRIPT" in html
    assert '<textarea name="script"' in html
    # Stage 2's form wires straight into the shared generation.js poller.
    assert 'data-prefix="/replicate"' in html
    assert 'data-result-kind="video"' in html


def test_intake_job_failure_is_surfaced_on_the_review_page(client):
    with mock.patch(
        "ofmhelpers.web.routers.replicate.pipeline.intake_reel",
        side_effect=RuntimeError("could not download reel"),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "could not download reel" in html


def test_generate_requires_a_script(client):
    r = client.post("/replicate/generate", data={"api_key": "k", "script": "   "})
    assert r.status_code == 400


def test_generate_creates_a_replicate_job(client, tmp_path):
    fake_video = tmp_path / "clone.mp4"
    fake_video.write_bytes(b"fake video bytes")

    with mock.patch(
        "ofmhelpers.web.routers.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ):
        r = client.post(
            "/replicate/generate",
            data={
                "api_key": "k",
                "script": "a full seedance script",
                "duration": "15",
                "resolution": "720p",
            },
        )

    assert r.status_code == 200
    job_id = r.json()["job_id"]
    job = get_job(job_id)
    assert job["task"] == "replicate"
    assert job["status"] == "done"
    assert job["result"][0]["name"] == "clone.mp4"


def test_generate_job_status_page_reuses_job_status_html(client, tmp_path):
    fake_video = tmp_path / "clone2.mp4"
    fake_video.write_bytes(b"fake video bytes")

    with mock.patch(
        "ofmhelpers.web.routers.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ):
        job_id = client.post(
            "/replicate/generate",
            data={"api_key": "k", "script": "a script", "duration": "15"},
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "clone2.mp4" in html or "Replicate" in html


def test_replicate_registered_in_generate_gallery():
    from ofmhelpers.web.routers.generate import FILES_PREFIX, TASK_LABELS

    assert TASK_LABELS["replicate"] == "Replicate (Reel Clone)"
    assert FILES_PREFIX["replicate"] == "/replicate/files"


def test_jobs_status_json_dispatches_by_task(client, tmp_path):
    with (
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.intake_reel",
            return_value=_mock_intake_result(tmp_path),
        ),
        mock.patch(
            "ofmhelpers.web.routers.replicate.pipeline.draft_script_full",
            return_value=DraftResult(script="a draft"),
        ),
    ):
        intake_job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    status = client.get(f"/replicate/jobs/{intake_job_id}/status").json()
    assert status["task"] == "replicate_intake"
    assert status["status"] == "done"
    assert "result" not in status  # intake jobs don't produce asset-shaped results
