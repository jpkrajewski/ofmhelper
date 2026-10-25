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
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.kaiai.client import KieAIClient, _session
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
        (402, ProviderNoCreditsError),
        (422, ProviderInputError),
        (429, ProviderRateLimitError),
        # Maintenance: retryable, but no rate limit -- a submit is not resent.
        (455, ProviderUnavailableError),
        (500, ProviderUnavailableError),
    ],
)
def test_kies_in_body_code_maps_to_an_error_type(provider, code, error):
    with kie_http() as http:
        http.request.return_value = response({"code": code, "msg": "nope"})
        with pytest.raises(error) as raised:
            provider.submit(get_spec("kie:wan/3-0-video"), {}, "job")

    assert type(raised.value) is error
    assert "nope" in str(raised.value) + getattr(raised.value, "detail", "")


def test_no_credits_keeps_the_text_the_card_reads(provider):
    """The card's 'No credits' reason reads '(code 402)'; callers decide by
    the type (a batch stops asking)."""
    with kie_http() as http:
        http.request.return_value = response({"code": 402, "msg": "Credits low"})
        with pytest.raises(ProviderNoCreditsError, match=r"\(code 402\)") as raised:
            provider.submit(get_spec("kie:wan/3-0-video"), {}, "job")

    assert isinstance(raised.value, ProviderInputError)
    assert not raised.value.retryable


def test_an_http_429_carries_its_retry_after(provider):
    answer = response({}, status_code=429)
    answer.headers = {"Retry-After": "7"}
    with kie_http() as http:
        http.request.return_value = answer
        with pytest.raises(ProviderRateLimitError) as raised:
            provider.submit(get_spec("kie:wan/3-0-video"), {}, "job")

    assert raised.value.retry_after_s == 7.0


def test_an_in_body_429_has_no_retry_after(provider):
    with kie_http() as http:
        http.request.return_value = response({"code": 429, "msg": "slow down"})
        with pytest.raises(ProviderRateLimitError) as raised:
            provider.submit(get_spec("kie:wan/3-0-video"), {}, "job")

    assert raised.value.retry_after_s is None


def test_the_upload_cache_key_never_carries_the_api_key(tmp_path):
    """It names a Redis key and a lock, and is logged when the lock fails."""
    api_key = "sk-secret-kie-key-123"
    client = KieAIClient(
        api_key=api_key,
        out_dir=tmp_path / "out",
        task_log=tmp_path / "t.jsonl",
        completions_log=tmp_path / "c.jsonl",
    )
    path = "/app/uploads/assets/users/7/ab__x.mp4"

    key = client._upload_cache_key(path)

    assert api_key not in key
    assert key.startswith("kieai:upload:")
    assert key.endswith(f":{path}")
    other = KieAIClient(
        api_key="another-key",
        out_dir=tmp_path / "out",
        task_log=tmp_path / "t.jsonl",
        completions_log=tmp_path / "c.jsonl",
    )
    assert other._upload_cache_key(path) != key, "uploads are per account"


def test_a_rejected_key_says_so_in_one_line(provider):
    with kie_http() as http:
        http.request.return_value = response(
            {"code": 401, "msg": "Authentication failed: Unauthorized"}
        )
        with pytest.raises(ProviderAuthError) as raised:
            provider.submit(get_spec("kie:wan/3-0-video"), {}, "job")

    assert str(raised.value) == "kie.ai: wrong API key"
    assert "Authentication failed" in raised.value.detail


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


def _not_json() -> object:
    answer = response({})
    answer.json.side_effect = json.JSONDecodeError("Expecting value", "<html>", 0)
    return answer


@pytest.mark.parametrize(
    "answer",
    [
        _not_json(),
        response({"msg": "no code"}),
        response({"code": 200, "data": {"state": "success"}}),
        _record("success", resultJson="{not json"),
        _record("success", resultJson=json.dumps(["https://cdn/v.mp4"])),
        _record("success", resultJson=json.dumps({"resultUrls": 5})),
    ],
    ids=[
        "not-json",
        "no-code",
        "no-task-id",
        "result-not-json",
        "result-not-object",
        "urls-not-list",
    ],
)
def test_an_unreadable_status_answer_is_transient(provider, answer):
    """The request exists (maybe paid): an answer that cannot be read is
    asked again, never a ValueError that fails the job unresumable."""
    with kie_http() as http:
        http.request.return_value = answer
        with pytest.raises(ProviderUnavailableError, match="unreadable") as raised:
            provider.status("t1")

    assert raised.value.retryable


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


def _value(field):
    """A valid value for one scalar setting, as the form would send it."""
    if field.default is not None:
        return field.default
    if field.enum:
        return field.enum[0]
    if field.type == "boolean":
        return False
    if field.type in ("integer", "number"):
        low = field.minimum if field.minimum is not None else 1
        return int(low) if field.type == "integer" else float(low)
    return "a red fox at dawn"


def _minimal(spec):
    """The required fields (and those of the first input variant, where the
    schema has `anyOf`), each with a valid value: local paths for files."""
    variants = spec.input_schema.get("anyOf") or [{}]
    names = spec.required | set(variants[0].get("required") or [])
    media = {f.name: f for f in spec.media_fields}
    ext = {"image": "png", "video": "mp4", "audio": "mp3"}
    values = {}
    for field in spec.form_fields:
        if field.name in names:
            values[field.name] = _value(field)
    for name in names & media.keys():
        values[name] = [f"/in/{name}.{ext[media[name].kind]}"]
    return values


@pytest.mark.parametrize("spec", models_for("kie"), ids=lambda s: s.model_id)
def test_every_kie_model_submits_its_catalog_body(provider, spec):
    """Each model, from its required fields, passes its own schema and goes
    to createTask as `{model, input}`: fixed values in, files uploaded, the
    form's own field names never inside the input."""
    values = _minimal(spec)
    spec.check(values)
    body = spec.request(values, lambda p: f"https://up/{p.name}")

    for name, field in {f.name: f for f in spec.media_fields}.items():
        if name in body:
            urls = body[name] if field.multiple else [body[name]]
            assert all(str(u).startswith("https://up/") for u in urls)
    assert spec.fixed.items() <= body.items()
    with kie_http() as http:
        http.request.return_value = response({"code": 200, "data": {"taskId": "t"}})
        provider.submit(spec, body, idempotency_key="job")

    assert http.request.call_args.kwargs["json"] == {
        "model": spec.model_id,
        "input": body,
    }


@pytest.mark.parametrize(
    ("key", "values", "body"),
    [
        (
            "kie:veo-3-1",
            {"prompt": "p", "image_urls": ["/a.png"]},
            {"model": "veo3_fast", "prompt": "p", "image_urls": ["https://up/a.png"]},
        ),
        (
            "kie:recraft/remove-background",
            {"image": ["/a.png"]},
            {"image": "https://up/a.png"},
        ),
        (
            "kie:wan/2-6-text-to-video",
            {"prompt": "line one\nline two"},
            {"nsfw_checker": False, "prompt": "line one\nline two"},
        ),
    ],
)
def test_generated_models_build_kies_body(key, values, body):
    """What the sync script decides: a `model` input is fixed, a bare
    `image` field is a file, nsfw_checker is off, multi-line prompts pass."""
    spec = get_spec(key)
    spec.check(values)

    assert spec.request(values, lambda p: f"https://up/{p.name}") == body


def test_input_variants_are_one_form_validated_per_variant():
    """Kling 3.0 Omni Image to Video: first frame alone or first + last
    frame -- one picker, each variant's limits still checked."""
    spec = get_spec("kie:kling-3.0-omni/image-to-video")
    spec.check({"prompt": "p", "image_urls": ["/a.png"]})

    with pytest.raises(ValueError):  # InvalidInputError
        spec.check({"prompt": "p", "image_urls": [f"/{i}.png" for i in range(9)]})
