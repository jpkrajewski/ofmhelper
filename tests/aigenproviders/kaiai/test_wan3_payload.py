"""What generate_video_wan3 actually sends to kie.ai.

Two things the docs make easy to get wrong and no other test would catch: the
model id (`wan/3-0-video`, not a `wan3` shorthand) and the rule that first/last
frames and the reference_* lists cannot be sent together -- passing both must
send only the frames, like Seedance's wrapper does.
"""

import pytest

from ofmhelpers.aigenproviders.kaiai.client import KieAIClient


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

    monkeypatch.setattr(client, "create_task", fake_create_task)
    monkeypatch.setattr(client, "poll_task", lambda *a, **k: ["https://x/clip.mp4"])
    monkeypatch.setattr(
        client,
        "download_urls",
        lambda urls, tid, ext: [client.OUT_DIR / f"{tid}.{ext}"],
    )
    return captured


def test_model_id_and_defaults(client, sent):
    client.generate_video_wan3(prompt="p")

    assert sent["model"] == "wan/3-0-video"
    assert sent["payload"] == {
        "prompt": "p",
        "resolution": "1080P",
        "aspect_ratio": "adaptive",
        "duration": 5,
        "audio": True,
        "nsfw_checker": False,
    }


def test_frames_win_over_references(client, sent):
    client.generate_video_wan3(
        prompt="p",
        first_frame_url="https://x/first.png",
        last_frame_url="https://x/last.png",
        reference_image_urls=["https://x/ref.png"],
    )

    payload = sent["payload"]
    assert payload["first_frame_url"] == "https://x/first.png"
    assert payload["last_frame_url"] == "https://x/last.png"
    assert "reference_image_urls" not in payload


def test_references_are_sent_when_no_frame_is_given(client, sent):
    client.generate_video_wan3(
        prompt="p",
        reference_image_urls=["https://x/ref.png"],
        reference_audio_urls=["https://x/ref.mp3"],
    )

    payload = sent["payload"]
    assert payload["reference_image_urls"] == ["https://x/ref.png"]
    assert payload["reference_audio_urls"] == ["https://x/ref.mp3"]
    assert "reference_video_urls" not in payload


def test_lowercase_resolution_is_rejected(client, sent):
    """kie.ai spells Wan's tiers 480P/720P/1080P -- every other video model in
    this client uses the lower-case form, so a copy-paste is the likely bug."""
    with pytest.raises(ValueError, match="not a valid Wan3Resolution"):
        client.generate_video_wan3(prompt="p", resolution="1080p")
