"""APIYI's other families behind the one provider, on the fake at the wire
(apiyi/fake_families.py answers like docs.apiyi.com): Nano Banana over
Gemini, GPT-Image / Seedream / FLUX over the OpenAI images API (answered in
the request), Seedance's Ark tasks and Veo's /v1/videos -- what goes over the
wire, what comes back saved, and the key each model runs on."""

import base64
import os
from pathlib import Path

os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from PIL import Image

from ofmhelpers.features.pictures import targets as picture_targets
from ofmhelpers.integrations.ai_providers import runner, tasks
from ofmhelpers.integrations.ai_providers.apiyi.fake import FAKE_KEY, fake_apiyi
from ofmhelpers.integrations.ai_providers.apiyi.families import split
from ofmhelpers.integrations.ai_providers.apiyi.models import raise_for
from ofmhelpers.integrations.ai_providers.apiyi.provider import ApiyiProvider
from ofmhelpers.integrations.ai_providers.catalog import get_spec
from ofmhelpers.integrations.ai_providers.errors import (
    InvalidInputError,
    ProviderAuthError,
    ProviderGenerationError,
    ProviderRateLimitError,
)
from ofmhelpers.media import fake_media
from ofmhelpers.schemas.errors import error_reason

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
    Image.new("RGB", (300, 400), "teal").save(path)
    return path


@pytest.fixture
def video(tmp_path) -> Path:
    path = tmp_path / "clip.mp4"
    path.write_bytes(fake_media.mp4())
    return path


def _calls(prefix: str) -> list:
    return [c for c in fake_apiyi().families.calls if c[0].startswith(prefix)]


def _run(provider, key: str, values: dict, job: str) -> list[Path]:
    spec = get_spec(key)
    spec.check(values)
    sent = spec.request(values, provider.upload).model_dump()
    return runner.generate(provider, spec, sent, job)


# --- Nano Banana (Gemini) --------------------------------------------------------


def test_nano_banana_pro_sends_gemini_body_and_keeps_the_last_image(provider):
    draft = base64.b64encode(fake_media.image("red", fmt="PNG")).decode()
    final_bytes = fake_media.image("blue", fmt="PNG")
    final = base64.b64encode(final_bytes).decode()
    fake_apiyi().families.gemini_next = {
        "candidates": [
            {
                "content": {
                    "parts": [
                        {"inlineData": {"mimeType": "image/png", "data": draft}},
                        {"inlineData": {"mimeType": "image/png", "data": final}},
                    ]
                },
                "finishReason": "STOP",
            }
        ]
    }

    [saved] = _run(
        provider,
        "apiyi:gemini-3-pro-image",
        {"prompt": "a lighthouse", "aspect_ratio": "3:4", "resolution": "2K"},
        "nb-t2i",
    )

    assert saved.read_bytes() == final_bytes, "the last image is the final one"
    [(path, body, _)] = _calls("/v1beta/models/")
    assert path == "/v1beta/models/gemini-3-pro-image:generateContent"
    assert body["contents"][0]["parts"] == [{"text": "a lighthouse"}]
    assert body["generationConfig"] == {
        "responseModalities": ["IMAGE"],
        "imageConfig": {"aspectRatio": "3:4", "imageSize": "2K"},
    }


def test_nano_banana_references_go_inline_with_no_public_address(
    provider, monkeypatch, image
):
    """Images need no link to this server: a dev box works as is."""
    monkeypatch.setenv("APP_BASE_URL", "http://localhost:8000/")

    _run(
        provider,
        "apiyi:gemini-3-pro-image",
        {"prompt": "her", "image_urls": [str(image)], "aspect_ratio": "auto"},
        "nb-edit",
    )

    [(_, body, _)] = _calls("/v1beta/models/")
    text, picture = body["contents"][0]["parts"]
    assert text == {"text": "her"}
    assert picture["inlineData"]["mimeType"] == "image/png"
    assert base64.b64decode(picture["inlineData"]["data"]) == image.read_bytes()
    # "auto" = the shape follows the input image: not sent.
    assert "imageConfig" not in body["generationConfig"] or (
        "aspectRatio" not in body["generationConfig"]["imageConfig"]
    )


@pytest.mark.parametrize(
    "answer",
    [
        {"candidates": [{"content": {"parts": None}, "finishReason": "IMAGE_SAFETY"}]},
        {"promptFeedback": {"blockReason": "OTHER"}, "usageMetadata": {}},
    ],
)
def test_a_refused_nano_banana_image_reads_as_flagged(provider, answer):
    fake_apiyi().families.gemini_next = answer

    with pytest.raises(ProviderGenerationError) as failed:
        _run(provider, "apiyi:gemini-3-pro-image", {"prompt": "x"}, "nb-flag")
    assert error_reason(str(failed.value)) == "Flagged content"


def test_a_sync_image_is_a_task_like_any_other(provider):
    """Answered in the request, yet read back by id: a resume or recovery
    finds the saved image."""
    _run(provider, "apiyi:gemini-3.1-flash-lite-image", {"prompt": "x"}, "nb-task")

    remembered = tasks.recall("nb-task")
    assert remembered is not None
    task = split(remembered.task_id)
    family, own = task.family, task.own_id
    assert remembered.task_id.startswith("img:")
    assert provider.status(remembered.task_id).state == "done"
    assert family.tag == "img"
    assert own


# --- GPT-Image-2 / Seedream / FLUX ---------------------------------------------


def test_gpt_image_2_official_never_sends_response_format(provider):
    [saved] = _run(
        provider,
        "apiyi:gpt-image-2",
        {"prompt": "a cat", "size": "1024x1024", "quality": "low"},
        "gpt-t2i",
    )

    assert saved.stat().st_size > 0
    [(_, body, _)] = _calls("/v1/images/generations")
    assert "response_format" not in body
    assert (body["size"], body["quality"]) == ("1024x1024", "low")


@pytest.mark.parametrize(
    ("model", "field"),
    [
        ("gpt-image-2", "image[]"),
        ("gpt-image-2-all", "image"),
        ("gpt-image-2-vip", "image"),
    ],
)
def test_gpt_image_references_go_as_files_to_edits(provider, image, model, field):
    _run(
        provider,
        f"apiyi:{model}",
        {"prompt": "her in red", "image_urls": [str(image), str(image)]},
        f"gpt-edit-{model}",
    )

    [(path, fields, files)] = _calls("/v1/images/edits")
    assert path == "/v1/images/edits"
    assert [name for name, _ in files] == [field, field]
    assert fields["model"] == model
    assert fields["prompt"] == "her in red"


def test_seedream_sends_data_urls_without_a_watermark_and_saves_the_url(
    provider, image
):
    [saved] = _run(
        provider,
        "apiyi:seedream-5-0-flash-260915",
        {"prompt": "outfit swap", "image_urls": [str(image)], "size": "2K"},
        "sd-edit",
    )

    assert saved.suffix == ".png"
    [(_, body, _)] = _calls("/v1/images/generations")
    assert body["model"] == "seedream-5-0-flash-260915"
    assert body["watermark"] is False
    assert body["response_format"] == "url"
    [ref] = body["image"]
    assert ref.startswith("data:image/png;base64,")
    assert "image_urls" not in body


def test_flux_edit_sends_numbered_inputs_and_its_ratio_not_a_size(provider, image):
    _run(
        provider,
        "apiyi:flux-2-pro",
        {"prompt": "x", "image_urls": [str(image), str(image)], "aspect_ratio": "3:4"},
        "flux-edit",
    )

    [(_, body, _)] = _calls("/v1/images/generations")
    assert body["input_image"].startswith("data:image/")
    assert body["input_image_2"].startswith("data:image/")
    assert body["aspect_ratio"] == "3:4"
    assert "size" not in body


def test_flux_text_to_image_sends_its_size_not_a_ratio(provider):
    _run(provider, "apiyi:flux-2-pro", {"prompt": "x", "size": "1536x1024"}, "flux-t2i")

    [(_, body, _)] = _calls("/v1/images/generations")
    assert body["size"] == "1536x1024"
    assert "aspect_ratio" not in body
    assert "input_image" not in body


# --- Seedance --------------------------------------------------------------------


def test_seedance_reference_run_sends_ark_content_and_saves_the_video(
    provider, image, video
):
    [saved] = _run(
        provider,
        "apiyi:doubao-seedance-2-0-260128",
        {
            "prompt": "Replace the subject in @Video1 with @Image1",
            "reference_image_urls": [str(image)],
            "reference_video_urls": [str(video)],
            "resolution": "720p",
            "duration": 5,
        },
        "sd2-r2v",
    )

    assert saved.suffix == ".mp4"
    [(path, body, _)] = _calls("/seedance/")
    assert path == "/seedance/api/v3/contents/generations/tasks"
    text, picture, clip = body["content"]
    assert text == {"type": "text", "text": "Replace the subject in @视频1 with @图像1"}
    assert picture["role"] == "reference_image"
    assert picture["image_url"]["url"].startswith(f"{BASE}files/ai/")
    assert clip["role"] == "reference_video"
    assert clip["video_url"]["url"].startswith(f"{BASE}files/ai/")
    assert (body["resolution"], body["duration"], body["watermark"]) == (
        "720p",
        5,
        False,
    )
    assert body["ratio"] == "adaptive"
    assert body["generate_audio"] is True
    remembered = tasks.recall("sd2-r2v")
    assert remembered is not None
    assert remembered.task_id.startswith("seedance:cgt-")


def test_seedance_2_5_first_frame_runs_take_only_adaptive(provider, image):
    _run(
        provider,
        "apiyi:doubao-seedance-2-5-260628",
        {"prompt": "she turns", "first_frame_url": str(image), "aspect_ratio": "9:16"},
        "sd25-i2v",
    )

    [(_, body, _)] = _calls("/seedance/")
    assert body["ratio"] == "adaptive"
    assert body["content"][1]["role"] == "first_frame"


def test_a_seedance_403_is_moderation_not_the_key():
    with pytest.raises(ProviderGenerationError) as caught:
        raise_for(
            403,
            '{"error": {"message": "real person detected"}}',
            forbidden_is_refusal=True,
        )
    assert error_reason(str(caught.value)) == "Flagged content"


@pytest.mark.parametrize(
    ("group", "raised"),
    [("SeeDance2", ProviderRateLimitError), ("default", ProviderAuthError)],
)
def test_no_channel_is_judged_against_the_models_own_group(group, raised):
    body = (
        '{"error": {"message": "Current group ' + group + " has no available "
        "channels for model doubao-seedance-2-5-260628 under billing mode "
        '[pay-as-you-go]"}}'
    )
    with pytest.raises(raised):
        raise_for(503, body)


# --- Veo -------------------------------------------------------------------------


def test_veo_text_to_video_sends_seconds_as_text_and_saves_the_video(provider):
    [saved] = _run(
        provider,
        "apiyi:veo-3.1-fast-generate-preview",
        {
            "prompt": "waves",
            "resolution": "720p",
            "aspect_ratio": "9:16",
            "duration": 6,
        },
        "veo-t2v",
    )

    assert saved.suffix == ".mp4"
    [(_, body, _)] = _calls("/v1/videos")
    assert body["seconds"] == "6"
    assert body["size"] == "720x1280"
    assert body["metadata"] == {"resolution": "720p", "aspectRatio": "9:16"}


def test_veo_without_a_video_url_is_fetched_from_content_with_the_key(provider):
    fake_apiyi().families.veo_url = False

    [saved] = _run(
        provider,
        "apiyi:veo-3.1-fast-generate-preview",
        {"prompt": "waves"},
        "veo-content",
    )

    assert saved.stat().st_size > 0


def test_veo_image_to_video_uploads_the_one_image(provider, image):
    _run(
        provider,
        "apiyi:veo-3.1-generate-preview",
        {"prompt": "she smiles", "first_frame_url": str(image)},
        "veo-i2v",
    )

    [(_, fields, files)] = _calls("/v1/videos")
    assert [name for name, _ in files] == ["input_reference"]
    assert fields["seconds"] == "8"
    assert (fields["resolution"], fields["aspectRatio"]) == ("720p", "16:9")
    assert "metadata" not in fields


def test_veo_1080p_takes_only_8_seconds():
    spec = get_spec("apiyi:veo-3.1-generate-preview")

    with pytest.raises(InvalidInputError):
        spec.check({"prompt": "x", "resolution": "1080p", "duration": 4})
    spec.check({"prompt": "x", "resolution": "1080p", "duration": 8})


# --- Keys ------------------------------------------------------------------------


def test_an_image_model_on_the_admins_wan_key_runs_on_the_default_groups(
    provider, monkeypatch
):
    monkeypatch.setenv("APIYI_API_KEY_WAN", "sk-adminWanKey0123456789abcdefghijkl")
    monkeypatch.setenv("APIYI_API_KEY_DEFAULT", FAKE_KEY)
    admin = ApiyiProvider("sk-adminWanKey0123456789abcdefghijkl")

    _run(admin, "apiyi:gemini-3-pro-image", {"prompt": "x"}, "keys-default")

    remembered = tasks.recall("keys-default")
    assert remembered is not None
    assert remembered.api_key == FAKE_KEY


def test_every_catalog_model_has_a_family():
    from ofmhelpers.integrations.ai_providers.apiyi.families import family_for
    from ofmhelpers.integrations.ai_providers.catalog import models_for

    specs = models_for("apiyi")
    assert len(specs) >= 30
    for spec in specs:
        family_for(spec.model_id)


def test_a_pictures_run_on_apiyi_is_nano_banana_pro_at_2k(provider, image):
    """/pictures' APIYI row: the preset's references + the picture, the
    shape and 2K, run through Gemini inline."""
    from ofmhelpers.web.routers.image_tools import pictures

    target = pictures.PICTURE_TARGETS["apiyi"]
    values = picture_targets.values(
        target,
        {
            "prompt": "Refer to the first images, replicate the last one",
            "reference_images": [str(image), str(image)],
            "aspect_ratio": pictures.run_aspect(
                "3:4", str(image), target.spec, target.aspect
            ),
            "resolution": pictures._resolution(target.spec, target.resolution),
        },
    ).model_dump()
    target.spec.check(values)

    sent = target.spec.request(values, provider.upload).model_dump()
    [saved] = runner.generate(provider, target.spec, sent, "pic-apiyi")

    assert saved.stat().st_size > 0
    assert target.max_images == 14
    [(_, body, _)] = _calls("/v1beta/models/")
    assert body["generationConfig"]["imageConfig"] == {
        "aspectRatio": "3:4",
        "imageSize": "2K",
    }
    assert len(body["contents"][0]["parts"]) == 3
