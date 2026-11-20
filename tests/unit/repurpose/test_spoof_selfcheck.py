import random
from pathlib import Path

import pytest

from ofmhelpers.config import settings
from ofmhelpers.features.repurpose import service
from ofmhelpers.features.repurpose.models import CopyScore
from ofmhelpers.media.repurpose.audio_check import BrokenAudioError
from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.repurpose.presets import load_preset
from ofmhelpers.media.repurpose.probe import Source
from ofmhelpers.media.repurpose.transformations.video import (
    VideoDraws,
    VideoSettings,
    VideoTransformer,
)
from ofmhelpers.media.size import Size


class _Report:
    def __init__(self, caught: bool):
        self.caught = caught
        self.hashes = []
        self.descriptors = []
        self.tmk = None
        self.audio = None

    @property
    def looks_like_source(self):
        return self.caught

    @property
    def sounds_like_source(self):
        return False


@pytest.fixture
def made(monkeypatch):
    seeds: list[int] = []

    class _Repurpose:
        def __init__(self, source, copy, *, seed, timeout_s):
            assert (str(source), timeout_s) == (
                "src.mp4",
                settings.web.uniqify_encode_timeout_s,
            )
            self.copy = Path(copy)
            seeds.append(seed)

        def apply_json_preset(self, name):
            assert name == "spoof"
            return self

        def run(self):
            self.copy.write_bytes(b"x")

    monkeypatch.setattr(service, "Repurpose", _Repurpose)
    monkeypatch.setattr(service, "verify_audio", lambda src, dst: None)
    return seeds


def _broken_audio_first(monkeypatch):
    broken = iter([True])

    def verify(src, dst):
        if next(broken, False):
            msg = f"{dst.name}: audio has no dynamics"
            raise BrokenAudioError(msg)

    monkeypatch.setattr(service, "verify_audio", verify)


def test_a_copy_with_broken_audio_is_made_again_without_checking_it(
    made, monkeypatch, tmp_path
):
    _broken_audio_first(monkeypatch)
    _verdicts(monkeypatch, [False])

    [result] = service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert made == [11, 12]
    assert result.attempts == 2


def test_a_try_that_crashes_leaves_no_half_made_copy(made, monkeypatch, tmp_path):
    def crash(*args, **kwargs):
        msg = "ffmpeg ran past its time limit"
        raise TimeoutError(msg)

    monkeypatch.setattr(service, "check", crash)

    with pytest.raises(TimeoutError):
        service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert made == [11]
    assert list(tmp_path.iterdir()) == []


def test_the_spoof_preset_makes_an_iphone_16_pro_max_h264_recording():
    preset = load_preset(service.PRESET)
    assert preset.device is not None
    device = load_device(preset.device)

    assert device.video.codec == "libx264"
    assert device.tags.format["com.apple.quicktime.model"] == "iPhone 16 Pro Max"
    assert "+faststart" not in device.container.movflags


def _framings(video: VideoSettings) -> list[VideoDraws]:
    source = Source(duration=11.0, width=2160, height=3840, has_audio=True)
    size = Size(width=1080, height=1920)
    return [
        VideoTransformer(video, source, size, random.Random(seed)).draws  # noqa: S311 - seeded draws, not cryptography
        for seed in range(200)
    ]


def test_spoof_mirrors_half_the_copies_and_turns_1_to_3_degrees_either_way():
    draws = _framings(load_preset(service.PRESET).video)

    turns = [d.rotate or 0.0 for d in draws]
    assert 70 <= sum(d.mirror for d in draws) <= 130
    assert all(1.0 <= abs(turn) <= 3.0 for turn in turns)
    assert 70 <= sum(turn < 0 for turn in turns) <= 130


def test_a_turn_keeps_one_direction_unless_both_ways_is_on():
    draws = _framings(VideoSettings(rotate=(1.0, 3.0)))

    assert all(1.0 <= (d.rotate or 0.0) <= 3.0 for d in draws)


def _verdicts(monkeypatch, caught: list[bool]):
    answers = iter(caught)
    monkeypatch.setattr(service, "check", lambda *a, **k: _Report(next(answers)))


def test_a_copy_that_still_looks_like_its_source_is_made_again_with_a_new_seed(
    made, monkeypatch, tmp_path
):
    _verdicts(monkeypatch, [True, False])

    [result] = service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert made == [11, 12]
    assert result.attempts == 2
    assert result.score == CopyScore(
        methods=[], looks_like_source=False, sounds_like_source=False
    )
    assert Path(result.path).is_file()


def test_a_copy_that_never_passes_fails_the_job_and_leaves_no_file(
    made, monkeypatch, tmp_path
):
    _verdicts(monkeypatch, [True, True, True])

    with pytest.raises(service.StillLooksLikeSourceError, match="3 tries"):
        service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert list(tmp_path.iterdir()) == []


def test_a_job_queued_before_spoof_still_makes_a_copy(made, monkeypatch, tmp_path):
    _verdicts(monkeypatch, [False])
    old = {"preset": {}, "preset_name": "", "special": False, "device": "", "copies": 1}

    [result] = service.run_repurpose(str(tmp_path), "src.mp4", **old)

    assert len(made) == 1
    assert Path(result.path).is_file()
