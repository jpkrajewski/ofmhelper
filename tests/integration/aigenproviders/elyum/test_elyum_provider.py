"""Elyum behind the shared interface, on its fake at the wire (elyum/fake.py
answers like elyum.ai/open/docs: real HTTP statuses, one error envelope):
upload to a `file_...` id, a generation's body with Elyum's `inputs`, the
Idempotency-Key, every documented error code onto the shared errors, and
Wan 3.0 reference-to-video as /clone sends it."""

from pathlib import Path

import pytest
from PIL import Image

from ofmhelpers.aigenproviders import runner
from ofmhelpers.aigenproviders.catalog import get_spec
from ofmhelpers.aigenproviders.elyum.fake import FAKE_KEY, fake_elyum
from ofmhelpers.aigenproviders.elyum.provider import ElyumProvider, wire_body
from ofmhelpers.aigenproviders.errors import (
    ProviderAuthError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.utils import fake_media
from ofmhelpers.web.routers.generation import clone
from ofmhelpers.web.schemas.errors import error_reason

WAN = "elyum:alibaba/wan-3.0/reference-to-video"


@pytest.fixture
def provider(fake_externals, monkeypatch):
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")
    return ElyumProvider(FAKE_KEY)


@pytest.fixture
def image(tmp_path) -> Path:
    path = tmp_path / "face.png"
    Image.new("RGB", (64, 64), "teal").save(path)
    return path


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "dance.mp4"
    path.write_bytes(fake_media.mp4())
    return path


def test_a_clone_runs_end_to_end_and_saves_its_video(provider, image, video):
    spec = get_spec(WAN)
    body = spec.request(
        {
            "prompt": "Replace the subject in @Video1 with subject from @Image1",
            "image_urls": [str(image)],
            "video_urls": [str(video)],
            "resolution": "720p",
            "duration": 5,
        },
        provider.upload,
    )

    [saved] = runner.generate(provider, spec, body, "elyum-job-1")

    assert saved.suffix == ".mp4"
    assert saved.stat().st_size > 0
    [(sent, key)] = fake_elyum().created
    assert key == "elyum-job-1"
    assert sent["model"] == "alibaba/wan-3.0/reference-to-video"
    # Elyum cites inputs in lower case, numbered per kind in the order sent.
    assert sent["prompt"] == "Replace the subject in @video1 with subject from @image1"
    assert sent["inputs"] == [
        {"role": "reference", "file": "file_000001"},
        {"role": "reference", "file": "file_000002"},
    ]
    assert (sent["resolution"], sent["duration"]) == ("720p", 5)
    assert "image_urls" not in sent
    assert "video_urls" not in sent


def test_a_clone_run_is_valid_for_wan_3_on_elyum():
    """No "adaptive" on Elyum: the shape is left to the model (it follows
    the inputs), never a 400 for a value it does not take."""
    target = clone.CLONE_TARGETS["elyum"]
    values = clone._clone_values(
        target,
        prompt="p",
        resolution="720P",
        duration=12,
        audio=True,
        images=["/s.png", "/t.png"],
        video="/v.mp4",
    )

    target.spec.check(values)
    body = wire_body(
        target.spec, target.spec.request(values, lambda p: f"https://u/{p.name}")
    )

    assert "aspect_ratio" not in body
    assert (body["resolution"], body["duration"], body["audio"]) == ("720p", 12, True)
    assert body["inputs"] == [
        {"role": "reference", "url": "https://u/s.png"},
        {"role": "reference", "url": "https://u/t.png"},
        {"role": "reference", "url": "https://u/v.mp4"},
    ]


def test_the_same_file_is_uploaded_once_per_account(provider, image):
    first = provider.upload(image)
    second = provider.upload(image)

    assert first == second
    assert first.startswith("file_")
    assert fake_elyum().uploads == ["face.png"]


def test_a_resent_create_returns_the_generation_already_made(provider):
    """The Idempotency-Key: a lost answer sent again is never paid twice."""
    first = provider.submit(get_spec(WAN), {"prompt": "p"}, "job:1")
    again = provider.submit(get_spec(WAN), {"prompt": "p"}, "job:1")

    assert first == again
    assert len(fake_elyum().created) == 1


def test_an_mkv_goes_up_as_its_mp4_copy(provider, tmp_path):
    clip = tmp_path / "clip.mkv"
    clip.write_bytes(fake_media.mp4())  # H.264: remuxed, nothing encoded

    provider.upload(clip)

    [name] = fake_elyum().uploads
    assert name.startswith("clip-")
    assert name.endswith(".mp4")


def test_a_file_elyum_does_not_take_is_refused_before_any_call(provider, tmp_path):
    gif = tmp_path / "loop.gif"
    Image.new("RGB", (8, 8)).save(gif)

    with pytest.raises(ProviderInputError, match=r"not loop\.gif"):
        provider.upload(gif)
    assert fake_elyum().uploads == []


def test_a_file_over_elyums_limit_is_refused_before_any_call(
    provider, video, monkeypatch
):
    monkeypatch.setenv("OFM_ELYUM_MAX_FILE_BYTES", "1000")

    with pytest.raises(ProviderInputError, match="over Elyum's"):
        provider.upload(video)
    assert fake_elyum().uploads == []


@pytest.mark.parametrize(
    ("status", "code", "error", "reason"),
    [
        (401, "auth", ProviderAuthError, "Wrong API key"),
        (403, "scope", ProviderAuthError, "Wrong API key"),
        (402, "insufficient_balance", ProviderNoCreditsError, "No credits"),
        (429, "key_cap", ProviderNoCreditsError, "No credits"),
        (429, "member_budget", ProviderNoCreditsError, "No credits"),
        (429, "concurrency", ProviderRateLimitError, "Rate limited"),
        (429, "rate_limited", ProviderRateLimitError, "Rate limited"),
        (503, "maintenance", ProviderRateLimitError, "Provider error"),
        (500, "internal", ProviderUnavailableError, "Provider error"),
        (400, "invalid_parameter", ProviderInputError, None),
        (409, "idempotency_conflict", ProviderInputError, None),
    ],
)
def test_a_refused_create_is_the_shared_error(provider, status, code, error, reason):
    fake_elyum().refuse_next = (status, code, "refused")

    with pytest.raises(error) as raised:
        provider.submit(get_spec(WAN), {"prompt": "p"}, "job")

    assert type(raised.value) is error
    if reason is not None:
        assert error_reason(str(raised.value)) == reason


def test_a_refused_generation_is_flagged_content(provider):
    fake_elyum().fail_next = ("content_refused", "The prompt was refused")

    with pytest.raises(ProviderGenerationError) as raised:
        runner.generate(provider, get_spec(WAN), {"prompt": "p"}, "elyum-job-2")

    assert "The prompt was refused" in str(raised.value)
    assert error_reason(str(raised.value)) == "Flagged content"


def test_any_other_failed_generation_is_a_failed_generation_with_its_reason(provider):
    fake_elyum().fail_next = ("generation_failed", "The model gave up")

    with pytest.raises(ProviderGenerationError, match="The model gave up"):
        runner.generate(provider, get_spec(WAN), {"prompt": "p"}, "elyum-job-3")


def test_a_wrong_key_is_refused_on_status_and_upload(fake_externals, image):
    wrong = ElyumProvider("ek_live_not-a-key")

    with pytest.raises(ProviderAuthError, match="Elyum: wrong API key"):
        wrong.status("g_000001")
    with pytest.raises(ProviderAuthError, match="Elyum: wrong API key"):
        wrong.upload(image)


def test_an_unknown_generation_is_an_input_error(provider):
    with pytest.raises(ProviderInputError, match="not_found"):
        provider.status("g_999999")
