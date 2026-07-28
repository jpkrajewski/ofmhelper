"""
Covers /replicate end to end at the router level, with reel_machine's own
pipeline/generation calls mocked out (no real download/ffmpeg/LLM/kie.ai
calls) -- mirrors the mocking style tests/web/test_generate_reuse.py already
uses for seedance/kling3/nanobanana.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

from unittest import mock

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.reel_machine.pipeline import AnalysisResult
from ofmhelpers.web.main import app
from ofmhelpers.web.stores.jobs import get_job

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _analysis(tmp_path, duration=15, environment="a lift lobby"):
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"fake video bytes")
    return AnalysisResult(
        video_path=video,
        duration=duration,
        prompt={"format": "9:16 vertical", "environment": environment},
        provider="gemini",
    )


def test_form_page_has_only_the_two_source_inputs(client):
    """The whole point of the rewrite: the user gives us a video, nothing
    else. No shape/look/gender/persona/provider pickers."""
    html = client.get("/replicate").text
    assert 'name="source_url"' in html
    assert 'name="source_file"' in html
    for gone in ('name="shape"', 'name="look"', 'name="gender"', 'name="target"'):
        assert gone not in html
    assert 'name="llm_provider"' not in html


def test_intake_requires_a_source(client):
    r = client.post("/replicate/intake", data={})
    assert r.status_code == 400


def test_intake_stores_the_validated_prompt_and_the_text_sent_to_seedance(
    client, tmp_path
):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        r = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        )

    assert r.status_code == 200
    job = get_job(r.json()["job_id"])
    assert job["task"] == "replicate_intake"
    assert job["status"] == "done"
    assert job["result"]["prompt"]["environment"] == "a lift lobby"
    assert '"environment": "a lift lobby"' in job["result"]["prompt_text"]
    assert job["result"]["duration"] == 15  # auto-detected from the source reel
    assert job["result"]["provider"] == "gemini"


def test_review_page_plays_the_source_video_and_shows_editable_json(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path, environment="a rooftop at golden hour"),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    # The input video itself, not a contact sheet of frames.
    assert f'src="/replicate/video/{job_id}"' in html
    assert "contact-sheet" not in html
    assert "a rooftop at golden hour" in html
    assert '<textarea name="script"' in html
    # Stage 2's form wires straight into the shared generation.js poller.
    assert 'data-prefix="/replicate"' in html
    assert 'data-result-kind="video"' in html


def test_source_video_endpoint_streams_the_downloaded_reel(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    r = client.get(f"/replicate/video/{job_id}")
    assert r.status_code == 200
    assert r.content == b"fake video bytes"
    assert r.headers["content-type"] == "video/mp4"


def test_source_video_404s_for_an_unknown_job(client):
    assert client.get("/replicate/video/nope").status_code == 404


def test_intake_job_failure_is_surfaced_on_the_review_page(client):
    """A model that returns something other than the requested JSON fails the
    job (schema.parse_analysis raises) instead of handing over a broken
    prompt -- the message has to reach the review page."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        side_effect=RuntimeError("model did not return valid JSON"),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "model did not return valid JSON" in html


def test_generate_requires_a_script(client):
    r = client.post("/replicate/generate", data={"api_key": "k", "script": "   "})
    assert r.status_code == 400


def test_generate_creates_a_replicate_job(client, tmp_path):
    fake_video = tmp_path / "clone.mp4"
    fake_video.write_bytes(b"fake video bytes")

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ):
        r = client.post(
            "/replicate/generate",
            data={
                "api_key": "k",
                "script": '{"format": "9:16 vertical"}',
                "duration": "15",
                "resolution": "720p",
            },
        )

    assert r.status_code == 200
    job = get_job(r.json()["job_id"])
    assert job["task"] == "replicate"
    assert job["status"] == "done"
    assert job["result"][0]["name"] == "clone.mp4"


def test_generate_job_status_page_reuses_job_status_html(client, tmp_path):
    fake_video = tmp_path / "clone2.mp4"
    fake_video.write_bytes(b"fake video bytes")

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ):
        job_id = client.post(
            "/replicate/generate",
            data={"api_key": "k", "script": "{}", "duration": "15"},
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "clone2.mp4" in html or "Replicate" in html


def test_replicate_registered_in_generate_gallery():
    from ofmhelpers.web.routers.generation.index import FILES_PREFIX, TASK_LABELS

    assert TASK_LABELS["replicate"] == "Replicate (Reel Clone)"
    assert FILES_PREFIX["replicate"] == "/replicate/files"


def test_jobs_status_json_dispatches_by_task(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        intake_job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    status = client.get(f"/replicate/jobs/{intake_job_id}/status").json()
    assert status["task"] == "replicate_intake"
    assert status["status"] == "done"
    assert "result" not in status  # intake jobs don't produce asset-shaped results
