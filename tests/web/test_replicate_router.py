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

import json
from pathlib import Path
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.reel_machine.pipeline import AnalysisResult
from ofmhelpers.reel_machine.schema import ReelAnalysis
from ofmhelpers.web.main import app
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job

pytestmark = pytest.mark.filterwarnings("ignore")

EXAMPLE = Path(__file__).parents[1] / "reel_machine" / "example.json"


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


def _analysis(tmp_path, duration=15, environment="a lift lobby"):
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"fake video bytes")
    payload = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    payload["environment"] = environment
    return AnalysisResult(
        video_path=video,
        duration=duration,
        provider="gemini",
        raw=json.dumps(payload),
        prompt=ReelAnalysis.model_validate(payload),
    )


def _unvalidated_analysis(tmp_path, raw="sorry, I can't help with that"):
    video = tmp_path / "reference.mp4"
    video.write_bytes(b"fake video bytes")
    return AnalysisResult(
        video_path=video,
        duration=15,
        provider="gemini",
        raw=raw,
        error="model did not return valid JSON: line 1 column 1",
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
    assert job["result"]["analysis_error"] is None
    assert job["result"]["speech"].startswith("[playful, pleading tone] Babe")


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


def test_review_page_sends_the_subjects_speech_to_elevenlabs(client, tmp_path):
    """The speech box is a real ElevenLabs form, not a readonly preview --
    the point of the page is picking assets and generating the voice."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert 'action="/helpers/elevenlabs/run"' in html
    assert 'name="text"' in html
    assert 'name="api_key"' in html
    assert "George" in html  # a voice from the ElevenLabs router's roster
    # The prompt textarea is wired to the JSON editor, on an even split.
    assert "data-json-editor" in html
    assert "/static/js/json-editor.js" in html
    assert "generate-layout--even" in html


def test_elevenlabs_key_is_prefilled_from_the_env(client, tmp_path, monkeypatch):
    monkeypatch.setenv("ELEVENLABS_API_KEY", "el-key-123")
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    assert "el-key-123" in client.get(f"/replicate/jobs/{job_id}").text
    assert "el-key-123" in client.get("/helpers/elevenlabs").text


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
    """Everything before validation (download, API key, provider error) still
    fails the job -- the message has to reach the review page."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        side_effect=RuntimeError("could not download the reel"),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "could not download the reel" in html


def test_an_unvalidated_response_reaches_the_review_page_as_raw_text(client, tmp_path):
    """A model answer that doesn't match the schema is shown verbatim, not
    thrown away: the job succeeds and the VA edits what came back."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_unvalidated_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    job = get_job(job_id)
    assert job["status"] == "done"
    assert job["result"]["prompt"] is None
    assert job["result"]["prompt_text"] == "sorry, I can't help with that"

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "model did not return valid JSON" in html
    assert "match the expected prompt shape" in html


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


def _boom():
    msg = "kie.ai rejected the request"
    raise RuntimeError(msg)


def test_json_prompt_and_api_key_are_collapsed_behind_details(client, tmp_path):
    """The redesign hides the JSON wall and the API key behind <details> --
    collapsed by default so the page reads as a summary, not a JSON dump."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "<details>" in html
    assert "Seedance API key" in html
    assert "Prompt JSON" in html
    # The JSON editor still initializes on the (hidden) textarea.
    assert "data-json-editor" in html


def test_analysis_summary_exposes_data_fields_for_the_live_json_sync(client, tmp_path):
    """The Analysis table's cells carry data-field="..." so the page's inline
    script can keep them in sync with hand-edits to the JSON textarea."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path, environment="a rooftop at golden hour"),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert 'id="analysis-summary"' in html
    for field in (
        "format",
        "style",
        "environment",
        "lighting",
        "camera_logic",
        "audio",
        "pacing",
    ):
        assert f'data-field="{field}"' in html
    assert "a rooftop at golden hour" in html


def test_elevenlabs_run_returns_job_id_json_not_a_redirect(client):
    """/run used to 303-redirect to its own job page (opening a new tab from
    the review page); it must now answer like every other generation tool so
    generation.js's inline poller can drive it."""
    fake_client = mock.MagicMock()
    fake_client.text_to_speech.convert.return_value = [b"fake-mp3-bytes"]
    with mock.patch(
        "ofmhelpers.web.routers.helpers.elevenlabs.ElevenLabs",
        return_value=fake_client,
    ):
        r = client.post(
            "/helpers/elevenlabs/run",
            data={"api_key": "k", "text": "hello", "voice": "George"},
        )

    assert r.status_code == 200
    assert not r.is_redirect
    assert "job_id" in r.json()


def test_elevenlabs_run_has_a_json_status_endpoint_for_polling(client):
    fake_client = mock.MagicMock()
    fake_client.text_to_speech.convert.return_value = [b"fake-mp3-bytes"]
    with mock.patch(
        "ofmhelpers.web.routers.helpers.elevenlabs.ElevenLabs",
        return_value=fake_client,
    ):
        job_id = client.post(
            "/helpers/elevenlabs/run",
            data={"api_key": "k", "text": "hello", "voice": "George"},
        ).json()["job_id"]

    status = client.get(f"/helpers/elevenlabs/jobs/{job_id}/status").json()
    assert status["status"] == "done"
    assert status["result"][0]["kind"] == "audio"


def test_generate_links_back_to_its_source_job_and_survives_a_reload(client, tmp_path):
    """A finished Seedance generation made from this review page must still
    show up after a plain page reload -- it used to only exist in a
    client-side generation.js card and vanished on refresh."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        intake_job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    fake_video = tmp_path / "clone.mp4"
    fake_video.write_bytes(b"fake video bytes")
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ):
        client.post(
            "/replicate/generate",
            data={
                "api_key": "k",
                "script": "{}",
                "duration": "15",
                "source_job_id": intake_job_id,
            },
        )

    html = client.get(f"/replicate/jobs/{intake_job_id}").text
    assert "clone.mp4" in html
    assert "Seedance video" in html


def test_voice_generation_links_back_and_survives_a_reload(client, tmp_path):
    """Same as the video case, for the inline ElevenLabs step this session
    added -- this is the exact bug that was manually caught and fixed."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        intake_job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    fake_client = mock.MagicMock()
    fake_client.text_to_speech.convert.return_value = [b"fake-mp3-bytes"]
    with mock.patch(
        "ofmhelpers.web.routers.helpers.elevenlabs.ElevenLabs",
        return_value=fake_client,
    ):
        client.post(
            "/helpers/elevenlabs/run",
            data={
                "api_key": "k",
                "text": "hello",
                "voice": "George",
                "source_job_id": intake_job_id,
            },
        )

    html = client.get(f"/replicate/jobs/{intake_job_id}").text
    assert "ElevenLabs voice" in html
    assert 'data-poll-kind="audio"' not in html  # it finished -- not a pending card


def test_review_page_resumes_a_still_running_voice_job_on_reload(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        intake_job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    # create_job() alone leaves the job "running" -- no run_job() call to
    # transition it, simulating a reload while the worker is still busy.
    voice_job_id = create_job(
        "elevenlabs", {"source_job_id": intake_job_id}, actor="admin"
    )

    html = client.get(f"/replicate/jobs/{intake_job_id}").text
    assert f'data-job-id="{voice_job_id}"' in html
    assert 'data-poll-prefix="/helpers/elevenlabs"' in html
    assert 'data-poll-kind="audio"' in html


def test_review_page_shows_a_failed_child_job_inline(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        intake_job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    video_job_id = create_job(
        "replicate", {"source_job_id": intake_job_id}, actor="admin"
    )
    run_job(video_job_id, _boom, {})

    html = client.get(f"/replicate/jobs/{intake_job_id}").text
    assert "kie.ai rejected the request" in html
