"""APIYI behind the shared interface, on its fake at the wire (apiyi/fake.py
answers like docs.apiyi.com's wan3 pages): DashScope's nested body with the
async header, references as this server's own links, every answer mapped onto
the shared errors, a lost submit never sent again, and Wan 3.0
reference-to-video as /clone sends it."""

import os
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from ofmhelpers.integrations.ai_providers import runner
from ofmhelpers.integrations.ai_providers.apiyi.fake import FAKE_KEY, fake_apiyi
from ofmhelpers.integrations.ai_providers.apiyi.models import raise_for
from ofmhelpers.integrations.ai_providers.apiyi.provider import ApiyiProvider
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.integrations.ai_providers.errors import (
    InvalidInputError,
    ProviderAuthError,
    ProviderGenerationError,
    ProviderInputError,
    ProviderNoCreditsError,
    ProviderRateLimitError,
    ProviderUnavailableError,
)
from ofmhelpers.media import fake_media
from ofmhelpers.schemas.errors import error_reason
from ofmhelpers.web.main import app

WAN = "apiyi:wan3.0-video"
BASE = "https://ascendglobal.test/"


@pytest.fixture
def provider(fake_externals, monkeypatch):
    monkeypatch.setenv("APP_BASE_URL", BASE)
    monkeypatch.delenv("OFM_AIGEN_PUBLIC_BASE_URL", raising=False)
    monkeypatch.setenv("OFM_AIGEN_POLL_INTERVAL_S", "0.01")
    monkeypatch.setenv("OFM_AIGEN_POLL_MAX_INTERVAL_S", "0.02")
    monkeypatch.setenv("OFM_APIYI_MIN_POLL_INTERVAL_S", "0")
    return ApiyiProvider(FAKE_KEY)


@pytest.fixture
def image(tmp_path) -> Path:
    path = tmp_path / "face.png"
    Image.new("RGB", (300, 300), "teal").save(path)
    return path


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "dance.mp4"
    path.write_bytes(fake_media.mp4())
    return path


def _served(url: str) -> bytes:
    """What the provider gets when it fetches one of our links."""
    path = url.removeprefix(BASE.rstrip("/"))
    response = TestClient(app).get(path)
    assert response.status_code == 200
    return response.content


def test_text_to_video_sends_dashscopes_body_and_saves_the_video(provider):
    spec = get_spec(WAN)
    body = spec.request(
        {"prompt": "a lighthouse at dusk", "resolution": "480P", "duration": 5},
        provider.upload,
    )

    [saved] = runner.generate(provider, spec, body, "apiyi-t2v")

    assert saved.suffix == ".mp4"
    assert saved.stat().st_size > 0
    [sent] = fake_apiyi().created
    assert sent == {
        "model": "wan3.0-video",
        "input": {"prompt": "a lighthouse at dusk"},
        "parameters": {
            "prompt_extend": True,
            "watermark": False,
            "resolution": "480P",
            "duration": 5,
            "ratio": "adaptive",
            "audio": True,
        },
    }


def test_resolution_is_always_sent_even_when_the_form_leaves_it_out(provider):
    spec = get_spec(WAN)
    runner.generate(provider, spec, spec.request({"prompt": "p"}, provider.upload), "j")

    [sent] = fake_apiyi().created
    assert sent["parameters"]["resolution"] == "1080P"
    assert sent["parameters"]["duration"] == 5


def test_image_to_video_hands_apiyi_a_link_to_the_first_frame(provider, image):
    spec = get_spec(WAN)
    body = spec.request(
        {"prompt": "she waves", "first_frame_url": str(image), "resolution": "480P"},
        provider.upload,
    )

    runner.generate(provider, spec, body, "apiyi-i2v")

    [sent] = fake_apiyi().created
    [frame] = sent["input"]["media"]
    assert frame["type"] == "first_frame"
    assert frame["url"].startswith(f"{BASE}files/ai/")
    # Never the file's own name in a public link, only its type.
    assert frame["url"].endswith("/ref.png")
    assert _served(frame["url"]) == image.read_bytes()


def test_a_clone_sends_its_references_in_order_cited_the_wan_way(
    provider, image, video
):
    spec = get_spec(WAN)
    body = spec.request(
        {
            "prompt": "Replace the subject in @Video1 with subject from @Image1",
            "reference_image_urls": [str(image)],
            "reference_video_urls": [str(video)],
            "aspect_ratio": "adaptive",
            "resolution": "720P",
            "duration": 5,
            "audio": True,
        },
        provider.upload,
    )

    [saved] = runner.generate(provider, spec, body, "apiyi-clone")

    assert saved.stat().st_size > 0
    [sent] = fake_apiyi().created
    assert sent["input"]["prompt"] == (
        "Replace the subject in Video 1 with subject from Image 1"
    )
    assert [m["type"] for m in sent["input"]["media"]] == [
        "reference_image",
        "reference_video",
    ]
    image_url, video_url = (m["url"] for m in sent["input"]["media"])
    assert _served(image_url) == image.read_bytes()
    assert _served(video_url) == video.read_bytes()
    assert sent["parameters"]["ratio"] == "adaptive"
    assert "aspect_ratio" not in sent["parameters"]


def test_the_same_reference_is_shared_by_one_link(provider, image):
    assert provider.upload(image) == provider.upload(image)


def test_the_key_is_checked_once_before_anything_is_shared(provider, image, tmp_path):
    other = tmp_path / "other.png"
    Image.new("RGB", (300, 300), "red").save(other)
    provider.upload(image)
    provider.upload(other)

    assert fake_apiyi().model_lists == 1


def test_a_wrong_key_is_refused_before_a_link_is_handed_out(fake_externals, image):
    with pytest.raises(ProviderAuthError, match="APIYI: wrong API key"):
        ApiyiProvider("sk-wrong").upload(image)


def test_without_a_public_https_address_a_reference_is_refused_before_paying(
    provider, monkeypatch, image
):
    """Wan fetches its references by link: on a server with no public
    address the run stops at submit, before anything is sent."""
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000/")
    spec = get_spec(WAN)
    body = spec.request({"prompt": "p", "first_frame_url": str(image)}, provider.upload)

    with pytest.raises(ProviderInputError, match="no public https address"):
        runner.generate(provider, spec, body, "apiyi-no-address")
    assert fake_apiyi().created == []


def test_a_first_frame_with_references_is_refused_before_anything_is_sent(image):
    spec = get_spec(WAN)

    with pytest.raises(InvalidInputError):
        spec.check(
            {
                "prompt": "p",
                "first_frame_url": str(image),
                "reference_image_urls": [str(image)],
            }
        )


def test_a_last_frame_needs_a_first_frame(image):
    spec = get_spec(WAN)

    with pytest.raises(InvalidInputError, match="first_frame_url"):
        spec.check({"prompt": "p", "last_frame_url": str(image)})


def test_a_file_wan_does_not_take_is_refused_before_paying(provider, tmp_path):
    clip = tmp_path / "voice.ogg"
    clip.write_bytes(b"OggS")

    with pytest.raises(ProviderInputError, match="not voice.ogg"):
        provider.upload(clip)


def test_a_failed_task_fails_the_run_with_upstreams_reason(provider):
    fake_apiyi().fail_next = ("InvalidParameter", "duration out of range")
    spec = get_spec(WAN)

    with pytest.raises(ProviderGenerationError, match="duration out of range"):
        runner.generate(provider, spec, {"prompt": "p"}, "apiyi-failed")


def test_a_moderated_task_reads_as_flagged_content(provider):
    fake_apiyi().fail_next = ("DataInspectionFailed", "Input data may contain risk")
    spec = get_spec(WAN)

    with pytest.raises(ProviderGenerationError) as failed:
        runner.generate(provider, spec, {"prompt": "p"}, "apiyi-flagged")
    assert error_reason(str(failed.value)) == "Flagged content"


def test_a_submit_whose_answer_is_lost_is_never_sent_again(provider):
    fake_apiyi().lose_next = True
    spec = get_spec(WAN)

    with pytest.raises(ProviderUnavailableError, match="may be made and paid"):
        runner.generate(provider, spec, {"prompt": "p"}, "apiyi-lost")
    assert len(fake_apiyi().created) == 1

    # Nothing remembered: recovery has no task to re-run the job on either.
    from ofmhelpers.integrations.ai_providers import tasks

    assert tasks.recall("apiyi-lost") is None


def test_a_rate_limited_submit_is_sent_again_and_made_once(provider, monkeypatch):
    from ofmhelpers.integrations.ai_providers import self_heal

    monkeypatch.setattr(self_heal, "_sleep", lambda _s: None)
    fake_apiyi().refuse_next = (429, "rate_limit", "upstream saturated")
    spec = get_spec(WAN)

    runner.generate(provider, spec, {"prompt": "p"}, "apiyi-limited")

    assert len(fake_apiyi().created) == 2
    assert len(fake_apiyi().tasks) == 1


@pytest.mark.parametrize(
    ("status", "body", "raised"),
    [
        (401, '{"error": {"message": "invalid token"}}', ProviderAuthError),
        (
            403,
            (
                '{"error": {"message": "user quota is not enough", '
                '"code": "insufficient_user_quota"}}'
            ),
            ProviderNoCreditsError,
        ),
        (402, '{"error": {"message": "pay"}}', ProviderNoCreditsError),
        (
            403,
            '{"error": {"message": "group Wan&HappyHorse required"}}',
            ProviderAuthError,
        ),
        # Dev 2026-11-06, a `default` group key asking for Wan 3.0.
        (
            503,
            (
                '{"error": {"message": "Current group default has no available '
                "channels for model wan3.0-video under billing mode "
                '[pay-as-you-go, pay-per-request]"}}'
            ),
            ProviderAuthError,
        ),
        # The right group with its channels down for now: sent again later.
        (
            503,
            (
                '{"error": {"message": "Current group Wan&HappyHorse has no '
                "available channels for model wan3.0-video under billing mode "
                '[pay-as-you-go]"}}'
            ),
            ProviderRateLimitError,
        ),
        # A per-call key of the right group: never works.
        (
            503,
            (
                '{"error": {"message": "Current group Wan&HappyHorse has no '
                "available channels for model wan3.0-video under billing mode "
                '[pay-per-request]"}}'
            ),
            ProviderAuthError,
        ),
        (
            403,
            '{"error": {"message": "insufficient permissions for this model"}}',
            ProviderAuthError,
        ),
        (
            500,
            '{"error": {"message": "upstream channel error"}}',
            ProviderUnavailableError,
        ),
        (429, '{"error": {"message": "slow down"}}', ProviderRateLimitError),
        (400, '{"code": "InvalidParameter", "message": "bad"}', ProviderInputError),
        (500, "<html>oops</html>", ProviderUnavailableError),
    ],
)
def test_every_documented_answer_maps_onto_the_shared_errors(status, body, raised):
    with pytest.raises(raised) as caught:
        raise_for(status, body)
    assert type(caught.value) is raised


def test_an_empty_balance_in_the_accounts_language_is_no_credits():
    """Dev 2026-11-06: an account with no money answered 403 in Polish."""
    with pytest.raises(ProviderNoCreditsError) as caught:
        raise_for(403, '{"error": {"message": "Niewystarczające saldo.", "code": 403}}')
    assert error_reason(str(caught.value)) == "No credits"


def test_no_credits_reads_as_no_credits_on_the_card():
    with pytest.raises(ProviderNoCreditsError) as caught:
        raise_for(403, '{"error": {"message": "user quota is not enough"}}')
    assert error_reason(str(caught.value)) == "No credits"


def test_an_unknown_status_keeps_the_task_remembered(provider):
    from ofmhelpers.integrations.ai_providers.apiyi.fake import FakeReply

    fake = fake_apiyi()
    fake.tasks["t-1"] = {"polls": 99, "failure": None}
    fake._read = staticmethod(  # type: ignore[method-assign]
        lambda task_id, state, **_: FakeReply.json_body(
            {"id": task_id, "status": "paused"}
        )
    )

    with pytest.raises(ProviderUnavailableError, match="paused"):
        provider.status("t-1")


def test_a_task_read_wrapped_in_data_is_read_too():
    from ofmhelpers.integrations.ai_providers.apiyi.models import ApiyiTask

    task = ApiyiTask.model_validate(
        {"data": {"id": "t", "status": "completed", "result_url": "https://x/y.mp4"}}
    )

    assert (task.state, task.urls) == ("completed", ["https://x/y.mp4"])


SEEDANCE_KEY = "sk-seedanceGroupKey0123456789abcdefghijklmnop"


def test_an_admin_key_runs_wan_on_the_wan_groups_key(provider, monkeypatch):
    """One APIYI key reaches one group: the admin's Seedance key, pre-filled
    or picked, is swapped for the Wan key on a Wan model, and the task is
    remembered with the key it was made with."""
    from ofmhelpers.integrations.ai_providers import tasks

    monkeypatch.setenv("APIYI_API_KEY_WAN", FAKE_KEY)
    monkeypatch.setenv("APIYI_API_KEY_SEEDANCE", SEEDANCE_KEY)
    seedance_side = ApiyiProvider(SEEDANCE_KEY)

    runner.generate(seedance_side, get_spec(WAN), {"prompt": "p"}, "apiyi-keys")

    assert len(fake_apiyi().created) == 1
    assert seedance_side.api_key == FAKE_KEY
    remembered = tasks.recall("apiyi-keys")
    assert remembered is not None
    assert remembered.api_key == FAKE_KEY


def test_a_users_own_key_is_never_swapped(provider, monkeypatch):
    monkeypatch.setenv("APIYI_API_KEY_WAN", FAKE_KEY)
    monkeypatch.setenv("APIYI_API_KEY_SEEDANCE", SEEDANCE_KEY)

    with pytest.raises(ProviderAuthError):
        runner.generate(
            ApiyiProvider("sk-usersOwnKey"), get_spec(WAN), {"prompt": "p"}, "own"
        )
    assert fake_apiyi().created == []


@pytest.mark.parametrize(
    ("model", "given", "used"),
    [
        ("wan3.0-video", SEEDANCE_KEY, FAKE_KEY),
        ("wan3.0-video-prime", FAKE_KEY, FAKE_KEY),
        ("happyhorse-1.1-r2v", SEEDANCE_KEY, FAKE_KEY),
        # APIYI's real Seedance ids (the Seedance key's /v1/models, 2026-11-06).
        ("doubao-seedance-2-0-260128", FAKE_KEY, SEEDANCE_KEY),
        ("doubao-seedance-2-5-260628", FAKE_KEY, SEEDANCE_KEY),
        ("some-other-model", FAKE_KEY, FAKE_KEY),
        ("doubao-seedance-2-0-260128", "sk-usersOwnKey", "sk-usersOwnKey"),
    ],
)
def test_each_model_family_maps_to_its_groups_key(monkeypatch, model, given, used):
    from ofmhelpers.integrations.ai_providers.apiyi.keys import key_for

    monkeypatch.setenv("APIYI_API_KEY_WAN", FAKE_KEY)
    monkeypatch.setenv("APIYI_API_KEY_SEEDANCE", SEEDANCE_KEY)

    assert key_for(model, given) == used


@pytest.mark.parametrize(
    "refused",
    [
        (500, "upstream_error", "upstream channel error"),
        (503, "model_not_found", "no available channel"),
        (403, "forbidden", "insufficient permissions"),
        (404, "task_not_found", "task not found yet"),
    ],
)
def test_a_task_read_that_errs_is_asked_again_until_it_ends(provider, refused):
    """A paid task is never given up on a read's error: only a 401 (the key
    gone) ends the wait."""
    fake_apiyi().refuse_reads = [refused, refused]
    spec = get_spec(WAN)

    [saved] = runner.generate(provider, spec, {"prompt": "p"}, "apiyi-read-errs")

    assert saved.stat().st_size > 0


@pytest.mark.parametrize(
    "body",
    [
        # Text fields answered null or as numbers.
        {
            "id": 12345,
            "status": "completed",
            "result_url": "https://x/y.mp4",
            "error": {"code": "", "message": None},
        },
        # DashScope's own shape, handed on unconverted.
        {
            "status": None,
            "output": {"task_status": "SUCCEEDED", "video_url": "https://x/y.mp4"},
        },
    ],
)
def test_an_odd_but_finished_task_body_still_reads_as_done(body):
    from ofmhelpers.integrations.ai_providers.apiyi.models import ApiyiTask

    task = ApiyiTask.model_validate(body)

    assert task.urls == ["https://x/y.mp4"]
    assert task.state in {"completed", "succeeded"}


def test_a_submit_answer_with_a_numeric_task_id_is_read():
    from ofmhelpers.integrations.ai_providers.apiyi.models import ApiyiSubmit

    assert ApiyiSubmit.model_validate({"output": {"task_id": 42}}).task_id == "42"


def test_a_failed_media_link_is_not_read_as_moderation():
    from ofmhelpers.integrations.ai_providers.apiyi.models import ApiyiTask

    task = ApiyiTask.model_validate(
        {
            "status": "failed",
            "error": {
                "code": "InvalidURL",
                "message": "url not reachable: https://s/green_dress.jpg",
            },
        }
    )

    assert error_reason(task.failure) != "Flagged content"


def test_status_reads_wait_at_least_apiyis_floor(provider, monkeypatch):
    """Docs: never faster than every 3 s."""
    import time
    from types import SimpleNamespace

    from ofmhelpers.integrations.ai_providers import runner as runner_module

    monkeypatch.setenv("OFM_APIYI_MIN_POLL_INTERVAL_S", "4")
    waits: list[float] = []
    # The runner's own clock only: patching time.sleep itself also catches
    # subprocess's waits (fake media made with ffmpeg).
    monkeypatch.setattr(
        runner_module,
        "time",
        SimpleNamespace(sleep=waits.append, monotonic=time.monotonic),
    )
    spec = get_spec(WAN)

    runner.generate(provider, spec, {"prompt": "p"}, "apiyi-floor")

    assert waits
    assert min(waits) >= 4
