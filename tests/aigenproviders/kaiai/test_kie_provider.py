"""kie.ai behind the shared interface, its HTTP faked at the wire: what a
request sends, how each answer maps to a state or an error, and the exact
body each catalog model produces."""

import json

import pytest
from kie_http_fake import kie_http, response

from ofmhelpers.aigenproviders.catalog import get_spec, models_for
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderInputError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.kaiai.client import _session
from ofmhelpers.aigenproviders.kaiai.provider import KieProvider


@pytest.fixture
def provider(tmp_path, monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_OUT_DIR", str(tmp_path / "out"))
    monkeypatch.setenv("OFM_KIEAI_TASK_LOG", str(tmp_path / "tasks.jsonl"))
    monkeypatch.setenv("OFM_KIEAI_COMPLETIONS_LOG", str(tmp_path / "done.jsonl"))
    return KieProvider("kie-key")


def _record(state, **extra):
    return response({"code": 200, "data": {"taskId": "t1", "state": state, **extra}})


def test_submit_sends_model_and_input_and_logs_the_task(provider, tmp_path):
    spec = get_spec("kie:wan/3-0-video")
    with kie_http() as http:
        http.request.return_value = response({"code": 200, "data": {"taskId": "t1"}})
        task_id = provider.submit(spec, {"prompt": "p"}, idempotency_key="job1")

    assert task_id == "t1"
    method, url = http.request.call_args.args
    assert (method, url) == ("POST", "https://api.kie.ai/api/v1/jobs/createTask")
    assert http.request.call_args.kwargs["json"] == {
        "model": "wan/3-0-video",
        "input": {"prompt": "p"},
    }
    assert http.request.call_args.kwargs["headers"] == {
        "Authorization": "Bearer kie-key"
    }
    logged = json.loads((tmp_path / "tasks.jsonl").read_text())
    assert (logged["taskId"], logged["jobId"]) == ("t1", "job1")


@pytest.mark.parametrize(
    ("code", "error"),
    [
        (401, ProviderAuthError),
        (402, ProviderInputError),
        (422, ProviderInputError),
        (429, ProviderUnavailableError),
        (455, ProviderUnavailableError),
        (500, ProviderUnavailableError),
    ],
)
def test_kies_in_body_code_maps_to_an_error_type(provider, code, error):
    with kie_http() as http:
        http.request.return_value = response({"code": code, "msg": "nope"})
        with pytest.raises(error, match="nope"):
            provider.submit(get_spec("kie:wan/3-0-video"), {}, "job")


def test_an_http_error_maps_by_status(provider):
    with kie_http() as http:
        http.request.return_value = response({}, status_code=503)
        with pytest.raises(ProviderUnavailableError):
            provider.status("t1")


def test_create_task_is_never_retried_by_the_transport():
    """A createTask that reached kie.ai and timed out must not be sent again:
    it would be paid twice."""
    retry = _session().get_adapter("https://api.kie.ai").max_retries

    assert "POST" not in retry.allowed_methods
    assert "GET" in retry.allowed_methods


def test_status_success_returns_the_result_urls_and_logs_credits(provider, tmp_path):
    with kie_http() as http:
        http.request.return_value = _record(
            "success",
            resultJson=json.dumps({"resultUrls": ["https://cdn/v.mp4"]}),
            creditsConsumed=12.5,
        )
        state = provider.status("t1")

    assert (state.state, state.urls) == ("done", ["https://cdn/v.mp4"])
    assert json.loads((tmp_path / "done.jsonl").read_text())["credits"] == 12.5


def test_status_fail_carries_kies_reason(provider):
    with kie_http() as http:
        http.request.return_value = _record("fail", failMsg="content flagged")
        state = provider.status("t1")

    assert (state.state, state.error) == ("failed", "content flagged")


@pytest.mark.parametrize("state", ["waiting", "queuing", "generating"])
def test_in_flight_states_are_pending(provider, state):
    with kie_http() as http:
        http.request.return_value = _record(state)
        assert provider.status("t1").state == "pending"


# The body each migrated model sends with its form defaults: the per-model
# quirks the old wrappers hard-coded now live in the catalog.
@pytest.mark.parametrize(
    ("key", "values", "body"),
    [
        (
            "kie:bytedance/seedance-2-5",
            {"prompt": "p", "reference_image_urls": ["/a.png"]},
            {
                "web_search": False,
                "return_last_frame": False,
                "nsfw_checker": False,
                "prompt": "p",
                "reference_image_urls": ["https://up/a.png"],
            },
        ),
        (
            "kie:nano-banana-pro",
            {"prompt": "p"},
            {"image_input": [], "prompt": "p"},
        ),
        (
            "kie:kling-3.0/video",
            {"prompt": "p", "image_urls": ["/a.png"]},
            {"multi_shots": False, "prompt": "p", "image_urls": ["https://up/a.png"]},
        ),
        (
            "kie:minimax-h3/image-to-video",
            {"prompt": "p", "first_frame_url": ["/f.png"]},
            {"prompt": "p", "first_frame_url": "https://up/f.png"},
        ),
        (
            "kie:seedream/5-lite-image-to-image",
            {"prompt": "p", "quality": "ultra", "image_urls": ["/a.png"]},
            {
                "nsfw_checker": False,
                "prompt": "p",
                "quality": "ultra",
                "image_urls": ["https://up/a.png"],
            },
        ),
    ],
)
def test_catalog_models_build_kies_body(key, values, body):
    spec = get_spec(key)
    spec.check(values)

    assert spec.request(values, lambda p: f"https://up/{p.name}") == body


@pytest.mark.parametrize(
    ("key", "values"),
    [
        ("kie:gpt-image-2-5-flare-image-to-image", {"prompt": "p"}),
        (
            "kie:seedream/5-pro-image-to-image",
            {"prompt": "p", "quality": "ultra", "image_urls": ["/a"]},
        ),
        ("kie:minimax-h3/text-to-video", {"prompt": "p", "aspect_ratio": "adaptive"}),
        ("kie:minimax-h3/image-to-video", {"prompt": "p"}),
    ],
)
def test_catalog_rejects_what_kie_would(key, values):
    """The rules the old wrappers raised on are schema rules now."""
    with pytest.raises(ValueError):  # InvalidInputError
        get_spec(key).check(values)


def test_every_kie_model_has_a_prompt():
    assert all("prompt" in s.required for s in models_for("kie"))
