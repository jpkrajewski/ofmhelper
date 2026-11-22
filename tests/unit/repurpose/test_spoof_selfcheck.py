import random
from pathlib import Path
from types import SimpleNamespace

import pytest
from spoof_draws import drawn_step

from ofmhelpers.config import settings
from ofmhelpers.features.repurpose import service
from ofmhelpers.features.repurpose.models import RepurposeResult
from ofmhelpers.media.repurpose.audio_check import BrokenAudioError
from ofmhelpers.media.repurpose.checker.schema import CheckReport
from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.repurpose.presets import load_preset
from ofmhelpers.media.repurpose.probe import Source
from ofmhelpers.media.repurpose.repurpose import Repurpose
from ofmhelpers.media.repurpose.transformations.audio import AudioSettings
from ofmhelpers.media.repurpose.transformations.video import (
    VideoDraws,
    VideoSettings,
    VideoTransformer,
)
from ofmhelpers.media.repurpose.values import Value
from ofmhelpers.media.size import Size


def _report(caught: bool) -> CheckReport:
    return CheckReport(
        source_file=Path("src.mp4"),
        copy_file=Path("copy.mov"),
        source_frames=1,
        copy_frames=1,
        flat_copy_frames=0,
        hashes=[],
        audio=None,
        looks_like_source=caught,
        sounds_like_source=False,
    )


@pytest.fixture
def strengths() -> list[float]:
    return []


@pytest.fixture
def made(monkeypatch, strengths):
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

        def stronger(self, factor):
            strengths.append(factor)
            return self

        def run(self):
            self.copy.write_bytes(b"x")
            return SimpleNamespace(step=drawn_step(seeds[-1]))

    monkeypatch.setattr(service, "Repurpose", _Repurpose)
    monkeypatch.setattr(service, "verify_audio", lambda src, dst: None)
    return seeds


def test_a_copy_keeps_what_was_drawn_for_it(made, monkeypatch, tmp_path):
    _verdicts(monkeypatch, [False])

    [result] = service.run_repurpose(str(tmp_path), "src.mp4", 10)
    kept = RepurposeResult.model_validate_json(result.model_dump_json())

    assert kept.step == drawn_step(11)
    assert kept.step is not None
    assert 1.0 <= abs(kept.step.video.rotate or 0.0) <= 3.0


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
    monkeypatch.setattr(service, "check", lambda *a, **k: _report(next(answers)))


def test_a_copy_that_still_looks_like_its_source_is_made_again_with_a_new_seed(
    made, monkeypatch, tmp_path
):
    _verdicts(monkeypatch, [True, False])

    [result] = service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert made == [11, 12]
    assert result.attempts == 2
    assert result.report == _report(caught=False)
    assert Path(result.path).is_file()


def test_a_copy_that_never_passes_fails_the_job_and_leaves_no_file(
    made, strengths, monkeypatch, tmp_path
):
    _verdicts(monkeypatch, [True] * 6)

    with pytest.raises(service.StillLooksLikeSourceError, match="6 tries"):
        service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert strengths == [1.0, 1.0, 1.0, 1.02, 1.02, 1.02]
    assert list(tmp_path.iterdir()) == []


def test_no_settings_make_a_copy_try_more_than_10_times(made, monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_REPURPOSE_ATTEMPTS", "10")
    monkeypatch.setenv("OFM_REPURPOSE_STRENGTH_ROUNDS", "3")
    _verdicts(monkeypatch, [True] * 30)

    with pytest.raises(service.StillLooksLikeSourceError, match="after 10 tries"):
        service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert len(made) == 10


def test_after_3_failed_tries_the_next_copies_are_2_percent_stronger(
    made, strengths, monkeypatch, tmp_path
):
    _verdicts(monkeypatch, [True, True, True, False])

    [result] = service.run_repurpose(str(tmp_path), "src.mp4", 10)

    assert made == [11, 12, 13, 14]
    assert strengths == [1.0, 1.0, 1.0, 1.02]
    assert (result.attempts, result.strength) == (4, 1.02)


def test_a_stronger_preset_moves_every_knob_2_percent_further_from_untouched():
    video = VideoSettings(
        corner_zoom=(1.10, 1.14),
        rotate=(1.0, 3.0),
        tilt=(-0.3, 0.3),
        colortemp=(6296.0, 6704.0),
        mirror=0.5,
        rotate_both_ways=True,
    ).stronger(1.02)
    audio = AudioSettings(
        pitch=(0.7, 0.8), volume=(0.985, 1.015), limit=0.97, loudness=0.8
    ).stronger(1.02)

    assert video.corner_zoom == pytest.approx((1.102, 1.1428))
    assert video.rotate == pytest.approx((1.02, 3.06))
    assert video.tilt == pytest.approx((-0.306, 0.306))
    assert video.colortemp == pytest.approx((6291.92, 6708.08))
    assert (video.mirror, video.rotate_both_ways, video.speed) == (0.5, True, None)
    assert audio.pitch == pytest.approx((0.714, 0.816))
    assert audio.volume == pytest.approx((0.9847, 1.0153))
    assert audio.limit == pytest.approx(0.9694)
    assert audio.loudness == 0.8


def _set_values(knobs) -> dict[str, object]:
    return {
        name: getattr(knobs, name)
        for name, field in type(knobs).model_fields.items()
        if getattr(knobs, name) is not None and field.annotation == Value | None
    }


def test_every_range_knob_of_the_spoof_preset_gets_stronger_and_the_mirror_odds_stay():
    preset = load_preset(service.PRESET)
    video, audio = preset.video.stronger(1.02), preset.audio.stronger(1.02)

    unchanged = [
        name
        for knobs, stronger in ((preset.video, video), (preset.audio, audio))
        for name, value in _set_values(knobs).items()
        if getattr(stronger, name) == value
    ]
    assert unchanged == []
    assert video.mirror == preset.video.mirror == 0.5
    assert preset.video.adjust is not None
    assert video.adjust is not None
    assert video.adjust.sharpen == pytest.approx(5.1)


def test_a_2_percent_stronger_copy_turns_and_zooms_2_percent_further():
    preset = load_preset(service.PRESET).video

    for base, stronger in zip(
        _framings(preset), _framings(preset.stronger(1.02)), strict=True
    ):
        assert stronger.rotate == pytest.approx((base.rotate or 0.0) * 1.02)
        assert (stronger.corner_zoom or 0.0) - 1 == pytest.approx(
            ((base.corner_zoom or 0.0) - 1) * 1.02
        )


def test_a_stronger_repurpose_runs_the_stronger_preset():
    preset = load_preset(service.PRESET)

    run = Repurpose("src.mp4", "copy.mov").apply_json_preset(service.PRESET)
    run = run.stronger(1.02)

    assert run.video == preset.video.stronger(1.02)
    assert run.audio == preset.audio.stronger(1.02)


def test_a_job_queued_before_spoof_still_makes_a_copy(made, monkeypatch, tmp_path):
    _verdicts(monkeypatch, [False])
    old = {"preset": {}, "preset_name": "", "special": False, "device": "", "copies": 1}

    [result] = service.run_repurpose(str(tmp_path), "src.mp4", **old)

    assert len(made) == 1
    assert Path(result.path).is_file()
