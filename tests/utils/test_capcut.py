import json
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from ofmhelpers.utils.capcut import (
    DEFAULT_PRESET,
    CapcutPreset,
    Jitter,
    SourceInfo,
    apply_preset,
)

needs_ffmpeg = pytest.mark.skipif(
    any(shutil.which(b) is None for b in ("ffmpeg", "ffprobe")),
    reason="ffmpeg not installed",
)

PORTRAIT = SourceInfo(duration=9.0, width=720, height=1280, has_audio=True)
NEUTRAL = Jitter(
    saturation=1.0, contrast=1.0, brightness=0.0, zoom=1.0, noise=0.0, volume=1.0
)


def _write_clip(dest: Path, size: str, seconds: float) -> Path:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"testsrc=size={size}:duration={seconds}:rate=30",
            "-f",
            "lavfi",
            "-i",
            f"sine=duration={seconds}",
            "-shortest",
            "-pix_fmt",
            "yuv420p",
            "-metadata",
            "title=source",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )
    return dest


def _probe(path: Path, entries: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entries, "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return json.loads(out)


def test_default_preset_maps_every_slider():
    vf = DEFAULT_PRESET.video_filter(PORTRAIT, NEUTRAL)
    assert "saturation=1.0556:gamma=1.0250" in vf
    assert "exposure=exposure=-0.0200" in vf
    assert "scale=2160:3840:flags=lanczos" in vf
    assert "unsharp=5:5:0.3000" in vf
    assert "noise=alls=1:allf=t" in vf
    assert "setpts=PTS/1.03" in vf
    assert DEFAULT_PRESET.audio_filter(NEUTRAL) == "atempo=1.03,volume=1.0"


def test_crop_zooms_110_and_moves_the_picture_down():
    # 720/1.1 -> 654, 1280/1.1 -> 1162; centred y would be 59, the window
    # moves up 2% of 1280 (25.6 px) so the picture moves down.
    assert DEFAULT_PRESET.crop(PORTRAIT, zoom=1.0) == "crop=654:1162:32:32"


def test_crop_offset_is_clamped_inside_the_frame():
    preset = CapcutPreset(scale=110, move_down=50)
    assert preset.crop(PORTRAIT, zoom=1.0) == "crop=654:1162:32:0"


def test_jitter_grain_goes_in_before_exposure_and_upscale():
    # exposure outputs float RGB; noise after it turns into colour static.
    jitter = NEUTRAL.model_copy(update={"noise": 2.5})
    vf = DEFAULT_PRESET.video_filter(PORTRAIT, jitter)
    assert vf.index("noise=alls=2.5") < vf.index("exposure=") < vf.index("scale=")


def test_jitter_stays_inside_the_medium_ranges():
    for seed in range(50):
        j = Jitter.sample(random.Random(seed))  # noqa: S311 - test jitter
        assert 0.949 <= j.saturation <= 1.0816
        assert 1.0 <= j.zoom <= 1.0022
        assert 0.0 <= j.noise <= 5.1


def test_landscape_goes_to_3840x2160():
    landscape = PORTRAIT.model_copy(update={"width": 1280, "height": 720})
    assert landscape.size_4k() == (3840, 2160)


def test_out_of_range_slider_rejected():
    with pytest.raises(ValueError, match="exposure"):
        CapcutPreset(exposure=51)


@needs_ffmpeg
def test_trims_longer_than_the_clip_are_rejected(tmp_path: Path):
    src = _write_clip(tmp_path / "in.mp4", "320x240", 0.5)
    with pytest.raises(ValueError, match="shorter than the trims"):
        apply_preset(src, tmp_path / "out.mp4", DEFAULT_PRESET, timeout_s=60)


@needs_ffmpeg
def test_apply_preset_encodes_like_capcut_4k(tmp_path: Path):
    src = _write_clip(tmp_path / "in.mp4", "720x1280", 2.0)
    dst = tmp_path / "out.mp4"
    apply_preset(src, dst, DEFAULT_PRESET, timeout_s=120, seed=1)
    probe = _probe(
        dst,
        "stream=codec_type,codec_name,codec_tag_string,profile,level,has_b_frames,"
        "r_frame_rate,color_primaries,sample_rate,channels,width,height"
        ":format=duration",
    )
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    audio = next(s for s in probe["streams"] if s["codec_type"] == "audio")
    assert (video["width"], video["height"]) == (2160, 3840)
    assert (video["codec_name"], video["codec_tag_string"]) == ("h264", "avc1")
    assert video["profile"] == "Main"
    assert video["level"] == 51  # 5.1, as CapCut's 4K export
    assert video["has_b_frames"] == 0
    assert video["r_frame_rate"] == "60000/1001"
    assert video["color_primaries"] == "bt709"
    assert (audio["sample_rate"], audio["channels"]) == ("44100", 2)
    # 0.3 s off each end, then 3% faster.
    expected = (2.0 - 0.6) / 1.03
    assert float(probe["format"]["duration"]) == pytest.approx(expected, abs=0.05)


@needs_ffmpeg
def test_apply_preset_writes_capcut_tags(tmp_path: Path):
    src = _write_clip(tmp_path / "in.mp4", "320x240", 1.0)
    dst = tmp_path / "out.mp4"
    apply_preset(src, dst, DEFAULT_PRESET, timeout_s=60)
    probe = _probe(dst, "format_tags:stream_tags")
    tags = probe["format"]["tags"]
    assert tags["major_brand"] == "isom"
    assert tags["compatible_brands"] == "isomiso2avc1mp41"
    assert tags["te_is_reencode"] == "1"
    assert tags["Hw"] == "1"
    assert tags["bitrate"] == "55000000"
    assert tags["encoder"] == "Lavf61.1.100"
    assert "title" not in tags
    assert "zzcodec" not in tags
    handlers = {s["tags"]["handler_name"] for s in probe["streams"]}
    assert handlers == {"VideoHandler", "SoundHandler"}
    assert all("encoder" not in s["tags"] for s in probe["streams"])
    # No encoder version strings anywhere: x264's SEI, AAC's "Lavc" fill.
    data = dst.read_bytes()
    assert b"x264" not in data
    assert b"Lavc" not in data


@needs_ffmpeg
def test_apply_preset_handles_a_silent_clip(tmp_path: Path):
    src = tmp_path / "in.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:duration=1:rate=30",
            "-pix_fmt",
            "yuv420p",
            str(src),
        ],
        check=True,
        capture_output=True,
    )
    dst = tmp_path / "out.mp4"
    apply_preset(src, dst, DEFAULT_PRESET, timeout_s=60)
    streams = _probe(dst, "stream=codec_type")["streams"]
    assert [s["codec_type"] for s in streams] == ["video"]


def test_mirror_flips_before_the_crop():
    preset = DEFAULT_PRESET.model_copy(update={"mirror": True})
    assert preset.video_filter(PORTRAIT, NEUTRAL).startswith("hflip,crop=")
    assert "hflip" not in DEFAULT_PRESET.video_filter(PORTRAIT, NEUTRAL)
