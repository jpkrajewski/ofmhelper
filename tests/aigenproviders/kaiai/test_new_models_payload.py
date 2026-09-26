"""What the Seedream 4.5 / Seedream 5.0 / GPT Image 2.5 Flare / MiniMax H3 /
Seedance 2.5 wrappers send.

Several of these are two or three kie.ai model ids behind one method, picked by
which inputs were passed -- the mapping is what these pin down, along with the
per-model quirks the docs call out (4.5 has no output_format, "ultra" is
Lite-only, H3 text-to-video rejects "adaptive").
"""

import pytest

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient
from ofmhelpers.aigenproviders.kaiai.types import VIDEO_MODELS, KieModel


@pytest.fixture
def client(tmp_path):
    return KieAIClient(
        api_key="test-key",
        out_dir=tmp_path / "out",
        task_log=tmp_path / "tasks.jsonl",
        completions_log=tmp_path / "completions.jsonl",
        resolved_log=tmp_path / "resolved.jsonl",
    )


@pytest.fixture
def sent(client, monkeypatch):
    captured = {}

    def fake_create_task(model, payload, callback_url=None):
        captured["model"] = model
        captured["payload"] = payload
        return "t1"

    def fake_download(urls, tid, ext):
        captured["ext"] = ext
        return [client.OUT_DIR / f"{tid}.{ext}"]

    monkeypatch.setattr(client, "create_task", fake_create_task)
    monkeypatch.setattr(client, "poll_task", lambda *a, **k: ["https://x/out"])
    monkeypatch.setattr(client, "download_urls", fake_download)
    return captured


def test_seedream45_text_then_edit(client, sent):
    client.generate_image_seedream45(prompt="p")
    assert sent["model"] == "seedream/4.5-text-to-image"
    assert sent["payload"] == {
        "prompt": "p",
        "aspect_ratio": "1:1",
        "quality": "basic",
        "nsfw_checker": False,
    }

    client.generate_image_seedream45(prompt="p", image_urls=["https://x/ref.png"])
    assert sent["model"] == "seedream/4.5-edit"
    assert sent["payload"]["image_urls"] == ["https://x/ref.png"]


def test_seedream45_rejects_a_5_0_only_quality(client, sent):
    with pytest.raises(ValueError, match="not a valid Seedream45Quality"):
        client.generate_image_seedream45(prompt="p", quality="ultra")


def test_seedream5_defaults_to_pro_text_to_image(client, sent):
    client.generate_image_seedream5(prompt="p")

    assert sent["model"] == "seedream/5-pro-text-to-image"
    assert sent["payload"] == {
        "prompt": "p",
        "aspect_ratio": "1:1",
        "quality": "basic",
        "output_format": "png",
        "nsfw_checker": False,
    }


def test_seedream5_lite_image_to_image_saves_as_output_format(client, sent):
    client.generate_image_seedream5(
        prompt="p",
        variant="lite",
        image_urls=["https://x/ref.png"],
        quality="ultra",
        output_format="jpeg",
    )

    assert sent["model"] == "seedream/5-lite-image-to-image"
    assert sent["ext"] == "jpeg"


def test_seedream5_ultra_is_lite_only(client, sent):
    with pytest.raises(ValueError, match="ultra"):
        client.generate_image_seedream5(prompt="p", variant="pro", quality="ultra")


def test_gpt_image_25_flare_is_image_to_image(client, sent):
    client.generate_image_gpt25_flare(prompt="p", input_urls=["https://x/in.png"])

    assert sent["model"] == "gpt-image-2-5-flare-image-to-image"
    assert sent["payload"] == {
        "prompt": "p",
        "input_urls": ["https://x/in.png"],
        "aspect_ratio": "auto",
        "resolution": "1K",
        "background": "auto",
    }


def test_gpt_image_25_flare_requires_an_input_image(client, sent):
    with pytest.raises(ValueError, match="input_urls is required"):
        client.generate_image_gpt25_flare(prompt="p", input_urls=[])


def test_seedance25_is_its_own_model(client, sent):
    client.generate_video_seedance25(
        prompt="p", reference_image_urls=["https://x/ref.png"]
    )

    assert sent["model"] == "bytedance/seedance-2-5"
    assert sent["payload"]["aspect_ratio"] == "adaptive"
    assert sent["payload"]["reference_image_urls"] == ["https://x/ref.png"]
    assert sent["ext"] == "mp4"


def test_seedance2_no_longer_accepts_2_5(client, sent):
    with pytest.raises(ValueError, match="not a valid Seedance2Model"):
        client.generate_video_seedance2(prompt="p", model="bytedance/seedance-2-5")


def test_minimax_h3_text_to_video(client, sent):
    client.generate_video_minimax_h3(prompt="p")

    assert sent["model"] == "minimax-h3/text-to-video"
    assert sent["payload"] == {
        "prompt": "p",
        "resolution": "2K",
        "duration": 6,
        "aspect_ratio": "16:9",
    }
    assert sent["ext"] == "mp4"


def test_minimax_h3_text_to_video_rejects_adaptive(client, sent):
    with pytest.raises(ValueError, match="adaptive"):
        client.generate_video_minimax_h3(prompt="p", aspect_ratio="adaptive")


def test_minimax_h3_references_use_reference_model(client, sent):
    client.generate_video_minimax_h3(
        prompt="p",
        aspect_ratio="adaptive",
        reference_image_urls=["https://x/ref.png"],
    )

    assert sent["model"] == "minimax-h3/reference-to-video"
    assert sent["payload"]["aspect_ratio"] == "adaptive"
    assert sent["payload"]["reference_image_urls"] == ["https://x/ref.png"]


def test_minimax_h3_frames_win_and_drop_aspect_ratio(client, sent):
    client.generate_video_minimax_h3(
        prompt="p",
        first_frame_url="https://x/first.png",
        reference_image_urls=["https://x/ref.png"],
    )

    assert sent["model"] == "minimax-h3/image-to-video"
    assert sent["payload"]["first_frame_url"] == "https://x/first.png"
    assert "aspect_ratio" not in sent["payload"]
    assert "reference_image_urls" not in sent["payload"]


def test_minimax_h3_lowercase_resolution_is_rejected(client, sent):
    with pytest.raises(ValueError, match="not a valid MinimaxH3Resolution"):
        client.generate_video_minimax_h3(prompt="p", resolution="2k")


def test_recovery_names_a_logged_model_string_by_the_video_set():
    """tasks.jsonl stores the plain id string -- it must still match."""
    assert "minimax-h3/text-to-video" in VIDEO_MODELS
    assert KieModel.SEEDREAM_5_PRO_TEXT not in VIDEO_MODELS
