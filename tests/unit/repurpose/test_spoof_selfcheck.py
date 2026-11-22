import random

import pytest

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

PRESET = "spoof"


def test_the_spoof_preset_makes_an_iphone_16_pro_max_h264_recording():
    preset = load_preset(PRESET)
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
    draws = _framings(load_preset(PRESET).video)

    turns = [d.rotate or 0.0 for d in draws]
    assert 70 <= sum(d.mirror for d in draws) <= 130
    assert all(1.0 <= abs(turn) <= 3.0 for turn in turns)
    assert 70 <= sum(turn < 0 for turn in turns) <= 130


def test_a_turn_keeps_one_direction_unless_both_ways_is_on():
    draws = _framings(VideoSettings(rotate=(1.0, 3.0)))

    assert all(1.0 <= (d.rotate or 0.0) <= 3.0 for d in draws)


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


def _set_values(knobs: VideoSettings | AudioSettings) -> dict[str, object]:
    fields = type(knobs).model_fields
    return {
        name: value
        for name, value in knobs
        if value is not None and fields[name].annotation == Value | None
    }


def test_every_range_knob_of_the_spoof_preset_gets_stronger_and_the_mirror_odds_stay():
    preset = load_preset(PRESET)
    video, audio = preset.video.stronger(1.02), preset.audio.stronger(1.02)

    unchanged = [
        name
        for knobs, stronger in ((preset.video, video), (preset.audio, audio))
        for name, value in _set_values(knobs).items()
        if dict(stronger)[name] == value
    ]
    assert unchanged == []
    assert video.mirror == preset.video.mirror == 0.5
    assert preset.video.adjust is not None
    assert video.adjust is not None
    assert video.adjust.sharpen == pytest.approx(5.1)


def test_a_2_percent_stronger_copy_turns_and_zooms_2_percent_further():
    preset = load_preset(PRESET).video

    for base, stronger in zip(
        _framings(preset), _framings(preset.stronger(1.02)), strict=True
    ):
        assert stronger.rotate == pytest.approx((base.rotate or 0.0) * 1.02)
        assert (stronger.corner_zoom or 0.0) - 1 == pytest.approx(
            ((base.corner_zoom or 0.0) - 1) * 1.02
        )


def test_a_stronger_repurpose_runs_the_stronger_preset():
    preset = load_preset(PRESET)

    run = Repurpose("src.mp4", "copy.mov").apply_json_preset(PRESET)
    run = run.stronger(1.02)

    assert run.video == preset.video.stronger(1.02)
    assert run.audio == preset.audio.stronger(1.02)
