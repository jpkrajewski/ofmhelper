"""
The Gemini call is one request, but the config on it is load-bearing: the
response is constrained to schema.ReelAnalysis, so the model cannot answer
with a fence, prose, a missing key or an invented one. Pinning that here --
`response_schema` (the OpenAPI subset) 400s on the `additionalProperties:
false` our `extra="forbid"` models emit, so it has to be
`response_json_schema`, and a silent revert would turn every job back into a
free-text guess that only schema.py catches.
"""

from unittest import mock

import pytest
from google import genai
from google.genai import errors, types

from ofmhelpers.reel_machine.llm import gemini_provider
from ofmhelpers.reel_machine.llm.gemini_provider import GeminiProvider
from ofmhelpers.reel_machine.models import ReelAnalysis


@pytest.fixture
def client():
    fake = mock.Mock()
    fake.files.upload.return_value = types.File(
        name="files/abc", state=types.FileState.ACTIVE
    )
    fake.models.generate_content.return_value = types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(parts=[types.Part(text='{"ok": true}')])
            )
        ]
    )
    with mock.patch.object(genai, "Client", return_value=fake):
        yield fake


def test_constrains_the_response_to_the_reel_analysis_schema(client, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")

    GeminiProvider(api_key="k", model="gemini-flash-latest").analyze_video(
        video, "analyze this"
    )

    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.response_json_schema == ReelAnalysis.model_json_schema()
    # The OpenAPI-subset field would reject that schema outright.
    assert config.response_schema is None
    assert config.response_mime_type == "application/json"
    # Low temperature is load-bearing too: sampling variety is what makes the
    # model paraphrase four camera beats into one summary shot.
    assert config.temperature == 0.15


def test_the_system_prompt_comes_from_the_caller(client, tmp_path):
    """Prompt text lives in reel_machine/prompts.py, not in the API client."""
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")

    GeminiProvider(api_key="k").analyze_video(video, "go", system_prompt="be terse")
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.system_instruction == "be terse"

    GeminiProvider(api_key="k").analyze_video(video, "go")
    config = client.models.generate_content.call_args.kwargs["config"]
    assert config.system_instruction is None


def test_sends_the_uploaded_video_and_the_prompt(client, tmp_path):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")

    provider = GeminiProvider(api_key="k", model="gemini-flash-latest")
    assert provider.analyze_video(video, "analyze this") == '{"ok": true}'

    kwargs = client.models.generate_content.call_args.kwargs
    assert kwargs["model"] == "gemini-flash-latest"
    uploaded, prompt = kwargs["contents"]
    assert prompt == "analyze this"
    assert uploaded.name == "files/abc"


def test_waits_for_the_upload_to_leave_processing(client, tmp_path, monkeypatch):
    """A file can only be referenced in generate_content once it is ACTIVE,
    so the upload is polled rather than used straight away."""
    monkeypatch.setattr(
        "ofmhelpers.reel_machine.llm.gemini_provider.time.sleep", lambda _: None
    )
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")
    client.files.upload.return_value = types.File(
        name="files/abc", state=types.FileState.PROCESSING
    )
    client.files.get.side_effect = [
        types.File(name="files/abc", state=types.FileState.PROCESSING),
        types.File(name="files/abc", state=types.FileState.ACTIVE),
    ]

    GeminiProvider(api_key="k").analyze_video(video, "analyze this")

    assert client.files.get.call_count == 2
    uploaded, _ = client.models.generate_content.call_args.kwargs["contents"]
    assert uploaded.state is types.FileState.ACTIVE


def test_a_video_gemini_cannot_process_raises(client, tmp_path):
    """No downgrade to stills: a static grid can't answer what the prompt
    asks about motion and timing."""
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")
    client.files.upload.return_value = types.File(
        name="files/abc", state=types.FileState.FAILED
    )

    with pytest.raises(RuntimeError, match="could not process"):
        GeminiProvider(api_key="k").analyze_video(video, "analyze this")


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(
        "ofmhelpers.reel_machine.llm.gemini_provider.time.sleep", lambda _: None
    )


def _busy(code=503):
    return errors.APIError(
        code,
        {"error": {"code": code, "message": "high demand", "status": "UNAVAILABLE"}},
    )


def test_a_busy_model_is_retried_rather_than_failing_the_job(
    client, tmp_path, no_sleep
):
    """503 UNAVAILABLE is Gemini saying "try again in a moment" -- failing the
    intake there also throws away the download it took to get here."""
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")
    ok = client.models.generate_content.return_value
    client.models.generate_content.side_effect = [_busy(), _busy(500), ok]

    assert GeminiProvider(api_key="k").analyze_video(video, "go") == '{"ok": true}'
    assert client.models.generate_content.call_count == 3
    # The video is uploaded once: a retry re-asks about the ACTIVE file.
    assert client.files.upload.call_count == 1


def test_a_non_transient_error_is_not_retried(client, tmp_path, no_sleep):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")
    client.models.generate_content.side_effect = _busy(400)

    with pytest.raises(errors.APIError):
        GeminiProvider(api_key="k").analyze_video(video, "go")
    assert client.models.generate_content.call_count == 1


def test_a_429_is_not_retried(client, tmp_path, no_sleep):
    """On the free tier a 429 is normally the daily quota, which sleeping
    never clears -- it fails fast and the VA reruns the intake later (the
    rerun reuses the already-downloaded reel, so nothing is lost)."""
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")
    client.models.generate_content.side_effect = _busy(429)

    with pytest.raises(errors.APIError):
        GeminiProvider(api_key="k").analyze_video(video, "go")
    assert client.models.generate_content.call_count == 1


def test_a_model_that_stays_busy_eventually_raises(client, tmp_path, no_sleep):
    video = tmp_path / "reel.mp4"
    video.write_bytes(b"fake video")
    client.models.generate_content.side_effect = _busy()

    with pytest.raises(errors.APIError):
        GeminiProvider(api_key="k").analyze_video(video, "go")
    assert client.models.generate_content.call_count == gemini_provider._MAX_ATTEMPTS
