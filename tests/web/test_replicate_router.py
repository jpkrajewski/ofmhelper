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

from ofmhelpers.reel_machine.hunt import HuntIdeas
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


def test_form_page_has_the_source_inputs_and_context_only(client):
    """The user gives us a video plus, optionally, a note about it. Still no
    shape/look/gender/persona/provider pickers -- the model reads all of that
    off the reel."""
    html = client.get("/replicate").text
    assert 'name="source_url"' in html
    assert 'name="source_file"' in html
    assert 'name="context"' in html
    for gone in ('name="shape"', 'name="look"', 'name="gender"', 'name="target"'):
        assert gone not in html
    assert 'name="llm_provider"' not in html


def test_intake_context_reaches_the_pipeline_and_is_stored(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ) as analyze:
        r = client.post(
            "/replicate/intake",
            data={
                "source_url": "https://example.com/reel",
                "context": "  she is a gym influencer  ",
            },
        )

    assert analyze.call_args.kwargs["context"] == "she is a gym influencer"
    # Stored too, so the Action log shows which note produced which analysis.
    assert get_job(r.json()["job_id"])["params"]["context"] == "she is a gym influencer"


def test_intake_without_context_passes_an_empty_string(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ) as analyze:
        client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        )

    assert analyze.call_args.kwargs["context"] == ""


def test_form_page_lists_past_analyses_and_links_to_them(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake",
            data={"source_url": "https://example.com/a-past-reel", "context": "a note"},
        ).json()["job_id"]

    html = client.get("/replicate").text
    assert f'href="/replicate/jobs/{job_id}"' in html
    assert "https://example.com/a-past-reel" in html
    assert "a note" in html


def test_form_page_flags_an_analysis_that_needs_fixing(client, tmp_path):
    """A done intake whose response failed validation still opens -- the list
    has to say so rather than showing a plain green "done"."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_unvalidated_analysis(tmp_path),
    ):
        client.post("/replicate/intake", data={"source_url": "https://example.com/bad"})

    assert "needs fixing" in client.get("/replicate").text


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
    # The prompt textarea is wired to the JSON editor, in the wide (70%) column.
    assert "data-json-editor" in html
    assert "/static/js/json-editor.js" in html
    assert "generate-layout--split-30-70" in html


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


def test_generate_minifies_the_prompt_json_it_sends(client, tmp_path):
    """A <textarea> submits its value CRLF-normalized, so the pretty-printed
    JSON the review page shows used to reach Seedance as a blob full of \\r\\n."""
    fake_video = tmp_path / "clone3.mp4"
    fake_video.write_bytes(b"x")
    pretty = json.dumps({"format": "9:16", "style": "raw"}, indent=2).replace(
        "\n", "\r\n"
    )

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ) as clone:
        job_id = client.post(
            "/replicate/generate",
            data={"api_key": "k", "script": pretty, "duration": "15"},
        ).json()["job_id"]

    sent = clone.call_args.kwargs["prompt"]
    assert "\r" not in sent
    assert "\n" not in sent
    assert json.loads(sent) == {"format": "9:16", "style": "raw"}
    # Stored minified too -- the Action log should show what was actually sent.
    assert get_job(job_id)["params"]["prompt"] == sent


def test_generate_drops_nulls_from_the_prompt_json(client, tmp_path):
    """The analysis prompt asks for nulls as a signal to itself ("line: null if
    no dialogue", "pose: null if off camera"), so a correctly filled-in answer
    still carries one on most scene_events entries. Seedance reads the prompt as
    instructions, and an absent key says the same thing for free."""
    fake_video = tmp_path / "clone7.mp4"
    fake_video.write_bytes(b"x")
    script = json.dumps(
        {
            "format": "9:16",
            "scene_events": [
                {"speaker": "subject", "line": "hi", "pose": None},
                {"speaker": "person_2", "line": None, "pose": None},
            ],
            "shots": [{"time": "0:00", "scene_event_cue": None}],
        }
    )

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ) as clone:
        client.post(
            "/replicate/generate",
            data={"api_key": "k", "script": script, "duration": "15"},
        )

    sent = json.loads(clone.call_args.kwargs["prompt"])
    assert "null" not in clone.call_args.kwargs["prompt"]
    assert sent["scene_events"][0] == {"speaker": "subject", "line": "hi"}
    assert sent["scene_events"][1] == {"speaker": "person_2"}
    assert sent["shots"][0] == {"time": "0:00"}
    assert sent["format"] == "9:16"  # non-null values untouched


def test_generate_keeps_an_empty_string_which_is_a_real_answer(client, tmp_path):
    """Only nulls go. "" is a (poor) answer the model actually gave, and
    dropping it would be indistinguishable from the model omitting the key."""
    fake_video = tmp_path / "clone8.mp4"
    fake_video.write_bytes(b"x")

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ) as clone:
        client.post(
            "/replicate/generate",
            data={
                "api_key": "k",
                "script": '{"style": "", "imperfections": []}',
                "duration": "15",
            },
        )

    assert json.loads(clone.call_args.kwargs["prompt"]) == {
        "style": "",
        "imperfections": [],
    }


def test_generate_keeps_non_json_text_but_normalizes_its_newlines(client, tmp_path):
    """An unvalidated raw answer a VA is fixing by hand still has to submit."""
    fake_video = tmp_path / "clone4.mp4"
    fake_video.write_bytes(b"x")

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ) as clone:
        client.post(
            "/replicate/generate",
            data={"api_key": "k", "script": "line one\r\nline two", "duration": "15"},
        )

    assert clone.call_args.kwargs["prompt"] == "line one\nline two"


def test_generate_passes_video_and_audio_references(client, tmp_path):
    fake_video = tmp_path / "clone5.mp4"
    fake_video.write_bytes(b"x")

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ) as clone:
        job_id = client.post(
            "/replicate/generate",
            data={
                "api_key": "k",
                "script": "{}",
                "duration": "15",
                "character_images_manifest": '[{"kind": "new"}]',
                "character_videos_manifest": '[{"kind": "new"}]',
                "character_audio_manifest": '[{"kind": "new"}]',
            },
            files=[
                ("character_images", ("face.png", b"img", "image/png")),
                ("character_videos", ("motion.mp4", b"vid", "video/mp4")),
                ("character_audio", ("voice.mp3", b"aud", "audio/mpeg")),
            ],
        ).json()["job_id"]

    assert len(clone.call_args.kwargs["video_ref_paths"]) == 1
    assert len(clone.call_args.kwargs["audio_ref_paths"]) == 1
    params = get_job(job_id)["params"]
    assert params["character_videos"]
    assert params["character_audio"]


def test_generate_sends_no_video_or_audio_references_when_none_were_picked(
    client, tmp_path
):
    fake_video = tmp_path / "clone6.mp4"
    fake_video.write_bytes(b"x")

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.generation.generate_reel_clone",
        return_value=fake_video,
    ) as clone:
        client.post(
            "/replicate/generate",
            data={"api_key": "k", "script": "{}", "duration": "15"},
        )

    assert clone.call_args.kwargs["video_ref_paths"] == []
    assert clone.call_args.kwargs["audio_ref_paths"] == []


def test_review_page_offers_video_and_audio_reference_pickers(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert 'data-field="character_images"' in html
    assert 'data-field="character_videos"' in html
    assert 'data-field="character_audio"' in html


def test_failed_analysis_still_renders_the_summary_cells_and_voice_step(
    client, tmp_path
):
    """The case that most needs the live sync: nothing validated, so the VA
    pastes a corrected prompt -- the cells it writes into have to exist, and
    the voice step has to be reachable even though there is no typed speech."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_unvalidated_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert 'data-field="environment"' in html
    assert 'data-field="scenes_shots"' in html
    assert 'action="/helpers/elevenlabs/run"' in html


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


def test_rerun_prefills_the_context_and_reuses_the_downloaded_reel(client, tmp_path):
    """A failed analysis shouldn't cost the VA the context note or the
    download -- /replicate?from=<job> comes back with both."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake",
            data={
                "source_url": "https://example.com/reel",
                "context": "she is a gym influencer",
            },
        ).json()["job_id"]

    html = client.get(f"/replicate?from={job_id}").text
    assert f'name="reuse_job_id" value="{job_id}"' in html
    assert "she is a gym influencer" in html
    assert "reference.mp4" in html


def test_rerun_analyzes_the_already_downloaded_file_not_the_link(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        first = client.post(
            "/replicate/intake",
            data={"source_url": "https://example.com/reel", "context": "a note"},
        ).json()["job_id"]

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ) as analyze:
        rerun = client.post(
            "/replicate/intake",
            data={"reuse_job_id": first, "context": "a sharper note"},
        ).json()["job_id"]

    # The reel is already on disk; asking Instagram for it a second time is
    # exactly what fails.
    assert analyze.call_args.args[0] == str(tmp_path / "reference.mp4")
    assert analyze.call_args.kwargs["context"] == "a sharper note"
    assert rerun != first


def test_rerun_falls_back_to_the_original_link_when_the_file_is_gone(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        first = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    # Built before the file is removed -- _analysis() writes it back otherwise.
    second = _analysis(tmp_path)
    (tmp_path / "reference.mp4").unlink()

    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=second,
    ) as analyze:
        client.post("/replicate/intake", data={"reuse_job_id": first})

    assert analyze.call_args.args[0] == "https://example.com/reel"


def test_rerun_of_an_unknown_job_still_needs_a_source(client):
    r = client.post("/replicate/intake", data={"reuse_job_id": "nope"})
    assert r.status_code == 400


def test_past_analyses_list_links_to_a_rerun(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    assert f'href="/replicate?from={job_id}"' in client.get("/replicate").text


def test_review_page_offers_outfit_searches_built_from_the_analysis(client, tmp_path):
    """The wardrobe step (find an outfit in the reel's niche) is a manual
    hunt -- the page pre-types the search off the analysis' own environment."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path, environment="a golf course at sunset"),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    # "girl" is prepended: a bare clothing query comes back full of menswear.
    assert (
        "pinterest.com/search/pins/?q=girl+a+golf+course+at+sunset+outfit+inspo" in html
    )
    assert (
        "google.com/search?tbm=isch&amp;q=girl+a+golf+course+at+sunset+outfit+inspo"
        in html
    )


def test_an_unvalidated_analysis_has_no_outfit_searches(client, tmp_path):
    """Nothing to build a query from until the JSON parses."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_unvalidated_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    assert "pinterest.com" not in client.get(f"/replicate/jobs/{job_id}").text


def test_review_page_offers_similar_reel_searches_and_the_trending_page(
    client, tmp_path
):
    """The next reel to clone is found the same manual way the outfit is."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path, environment="a golf course at sunset"),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "tiktok.com/search?q=a+golf+course+at+sunset+reel" in html
    # Instagram reels are reachable logged-out only through a site: search.
    assert "site%3Ainstagram.com%2Freel%2F" in html
    # Instagram is reachable logged-out only via a site: search here; the
    # niche pages need the second pass, which this job did not run.
    assert "site%3Ainstagram.com%2Freel%2F" in html


def test_an_unvalidated_analysis_has_no_reel_searches_either(client, tmp_path):
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_unvalidated_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "Similar reel hunt" not in html
    assert "instagram.com/popular" not in html


def _analysis_with_hunt(tmp_path, **hunt_kwargs):
    result = _analysis(tmp_path, environment="a starbucks at noon")
    result.hunt = HuntIdeas(**hunt_kwargs)
    return result


def test_review_page_uses_the_free_models_tags_and_queries(client, tmp_path):
    """Gemini says what the reel is; the second (free) model turns that into
    hashtags and phrases people actually search."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis_with_hunt(
            tmp_path,
            instagram_topics=["starbucks-girl", "starbucks-skit"],
            search_queries=["starbucks barista skit"],
            outfit_ideas=["oversized green apron fit"],
        ),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    # Stored with the job, so reopening it doesn't re-ask the model.
    assert get_job(job_id)["result"]["hunt"]["instagram_topics"] == [
        "starbucks-girl",
        "starbucks-skit",
    ]

    html = client.get(f"/replicate/jobs/{job_id}").text
    # The niche pages are /popular/<slug>: Instagram's own search and its
    # hashtag pages are login-gated, these are not.
    assert 'href="https://www.instagram.com/popular/starbucks-girl/"' in html
    assert 'href="https://www.instagram.com/popular/starbucks-skit/"' in html
    # Bare /popular/ is a generic signed-out landing page -- not linked.
    assert 'href="https://www.instagram.com/popular/"' not in html
    assert "instagram.com/explore/" not in html
    assert "tiktok.com/search?q=starbucks+barista+skit" in html
    # Outfit searches say "girl" -- a bare clothing description comes back full
    # of menswear.
    assert "pinterest.com/search/pins/?q=girl+oversized+green+apron+fit" in html


def test_the_derived_searches_survive_when_the_free_model_said_nothing(
    client, tmp_path
):
    """No GROQ_API_KEY (or a failed call) leaves the analysis-derived terms as
    the floor -- the page never comes back empty."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis_with_hunt(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "pinterest.com/search/pins/?q=girl+a+starbucks+at+noon+outfit+inspo" in html
    assert "tiktok.com/search?q=a+starbucks+at+noon+reel" in html
    # No topic pages without the second pass: a slug can't be sliced out of
    # prose, and there is no bare /popular/ button to fall back to.
    assert "instagram.com/popular" not in html


def test_outfit_searches_ignore_everyone_but_the_main_subject(client, tmp_path):
    """example.json's second person is the cameraman, wardrobe "not visible" --
    searching Pinterest for that is how the block filled up with junk."""
    with mock.patch(
        "ofmhelpers.web.routers.generation.replicate.pipeline.analyze",
        return_value=_analysis(tmp_path),
    ):
        job_id = client.post(
            "/replicate/intake", data={"source_url": "https://example.com/reel"}
        ).json()["job_id"]

    html = client.get(f"/replicate/jobs/{job_id}").text
    assert "not+visible" not in html
    assert "Yellow-and-green halter crop top" in html  # the subject's own
