"""RunningHub behind the shared interface, on its fake at the wire
(runninghub/fake.py answers like the Standard Model API: HTTP 200, the
code in the body): upload, submit, query, every documented error code onto
the shared errors, and Wan 3.0 reference-to-video as /clone sends it."""

from pathlib import Path

import pytest
from PIL import Image

from ofmhelpers.aigenproviders import runner
from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.aigenproviders.runninghub.fake import FAKE_KEY, fake_runninghub
from ofmhelpers.aigenproviders.runninghub.provider import (
    RunningHubProvider,
    wire_body,
)
from ofmhelpers.web.routers.generation import clone
from ofmhelpers.web.schemas.errors import error_reason

WAN = "runninghub:alibaba/wan-3.0/reference-to-video"


@pytest.fixture
def provider(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")
    return RunningHubProvider(FAKE_KEY)


@pytest.fixture
def image(tmp_path) -> Path:
    path = tmp_path / "face.png"
    Image.new("RGB", (64, 64), "teal").save(path)
    return path


def test_a_generation_runs_end_to_end_and_saves_its_video(provider, image, tmp_path):
    spec = get_spec(WAN)
    body = spec.request(
        {"prompt": "Image 1 dances", "imageUrls": [str(image)], "duration": 5},
        provider.upload,
    )

    [saved] = runner.generate(provider, spec, body, "rh-job-1")

    assert saved.suffix == ".mp4"
    assert saved.stat().st_size > 0
    [(model, sent)] = fake_runninghub().submitted
    assert model == "alibaba/wan-3.0/reference-to-video"
    assert sent["imageUrls"][0].startswith("https://fake-files.runninghub.ai/input/")


def test_the_body_fills_required_defaults_and_sends_seconds_as_a_string():
    spec = get_spec(WAN)
    body = wire_body(spec, {"prompt": "p", "duration": 15})

    assert body["duration"] == "15"
    # Required by RunningHub, left out by a form that kept the default.
    assert (body["resolution"], body["aspectRatio"]) == ("1080P", "adaptive")
    # The docs' sample asset URLs never become defaults.
    assert "videoUrls" not in body
    assert "imageUrls" not in body


def test_a_clone_run_is_valid_for_wan_3_on_runninghub():
    target = clone.CLONE_TARGETS["runninghub"]
    values = clone._clone_values(
        target,
        prompt="Replace Video 1's person with Image 1",
        resolution="720p",
        duration=12,
        audio=True,
        images=["/s.png"],
        video="/v.mp4",
    )

    target.spec.check(values)
    body = wire_body(
        target.spec, target.spec.request(values, lambda p: f"https://u/{p.name}")
    )

    assert body["aspectRatio"] == "adaptive"
    assert body["resolution"] == "720P"
    assert body["duration"] == "12"
    assert (body["imageUrls"], body["videoUrls"]) == (
        ["https://u/s.png"],
        ["https://u/v.mp4"],
    )
    assert body["audio"] is True


def test_the_same_file_is_uploaded_once_per_account(provider, image):
    first = provider.upload(image)
    second = provider.upload(image)

    assert first == second
    assert len(fake_runninghub().uploads) == 1


@pytest.mark.parametrize(
    ("code", "message", "error", "reason"),
    [
        (806, "APIKEY_USER_NOT_FOUND", ProviderAuthError, "Wrong API key"),
        (
            1014,
            "Standard Model API is restricted to Enterprise-Shared API Keys only",
            ProviderAuthError,
            "Wrong API key",
        ),
        (
            416,
            "TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET",
            ProviderNoCreditsError,
            "No credits",
        ),
        (812, "CORPAPIKEY_INSUFFICIENT_FUNDS", ProviderNoCreditsError, "No credits"),
        (421, "TASK_QUEUE_MAXED", ProviderRateLimitError, "Rate limited"),
        (
            1505,
            "Photorealistic real people are prohibited",
            ProviderGenerationError,
            "Flagged content",
        ),
        (1000, "unknown error", ProviderUnavailableError, None),
        (301, "PARAMS_INVALID", ProviderInputError, None),
    ],
)
def test_a_refused_submit_is_the_shared_error(provider, code, message, error, reason):
    fake_runninghub().refuse_next = (code, message)

    with pytest.raises(error) as raised:
        provider.submit(get_spec(WAN), {"prompt": "p"}, "job")

    assert type(raised.value) is error
    if reason is not None:
        assert error_reason(str(raised.value)) == reason


def test_a_task_ended_for_want_of_balance_is_no_credits(provider):
    fake_runninghub().fail_next = (416, "TASK_CREATE_FAILED_BY_NOT_ENOUGH_WALLET")

    with pytest.raises(ProviderNoCreditsError):
        runner.generate(provider, get_spec(WAN), {"prompt": "p"}, "rh-job-2")


def test_any_other_failed_task_is_a_failed_generation_with_its_reason(provider):
    fake_runninghub().fail_next = (1505, "Photorealistic real people are prohibited")

    with pytest.raises(ProviderGenerationError) as raised:
        runner.generate(provider, get_spec(WAN), {"prompt": "p"}, "rh-job-3")

    assert "prohibited (code 1505)" in str(raised.value)
    assert error_reason(str(raised.value)) == "Flagged content"


def test_a_wrong_key_is_refused_on_query_and_upload(fake_externals, image):
    wrong = RunningHubProvider("not-a-key")

    with pytest.raises(ProviderAuthError, match="RunningHub: wrong API key"):
        wrong.status("1910246754753896450")
    with pytest.raises(ProviderAuthError, match="RunningHub: wrong API key"):
        wrong.upload(image)


def test_a_key_revoked_while_its_task_runs_is_a_wrong_key_not_a_failure(provider):
    task_id = provider.submit(get_spec(WAN), {"prompt": "p"}, "rh-job-4")

    with pytest.raises(ProviderAuthError, match="RunningHub: wrong API key"):
        RunningHubProvider("revoked-key").status(task_id)


def test_an_unknown_task_is_an_input_error(provider):
    with pytest.raises(ProviderInputError, match="code 807"):
        provider.status("1910246754753896450")
