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
from google.genai import types

from ofmhelpers.reel_machine.llm.gemini_provider import GeminiProvider
from ofmhelpers.reel_machine.schema import ReelAnalysis


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
