import json
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from ofmhelpers.features.repurpose import service
from ofmhelpers.media.recorder import load_profile
from ofmhelpers.media.repurpose.repurpose import Repurpose
from ofmhelpers.media.repurpose.transformations.video import VideoSettings

FPS_SLACK = 0.05
pytestmark = [pytest.mark.ci_only, pytest.mark.heavy, pytest.mark.slow]


def _clip(dest: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "mandelbrot=size=1080x1920:rate=30,trim=duration=3",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return dest


def _streams(path: Path) -> dict[str, dict]:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "stream=codec_type,codec_name,width,height,avg_frame_rate,r_frame_rate",
            "-of", "json", str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout  # fmt: skip
    return {s["codec_type"]: s for s in json.loads(out)["streams"]}


def test_a_spoof_copy_is_a_real_iphone_h264_file_that_passed_the_self_check(tmp_path):
    source = _clip(tmp_path / "src.mp4")

    [result] = service.run_repurpose(str(tmp_path), str(source), 7)

    copy = Path(result.path)
    streams = _streams(copy)
    assert copy.is_file()
    assert copy != source
    assert result.score.unique
    assert result.step is not None
    assert (streams["video"]["codec_name"], streams["video"]["height"]) == (
        "h264",
        1920,
    )
    assert streams["video"]["avg_frame_rate"] == "30/1"
    assert streams["audio"]["codec_name"] == "aac"


def _clip_4k60(dest: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc2=size=2160x3840:rate=60,trim=duration=2",
            "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
            "-shortest", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p",
            str(dest),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return dest


def test_an_edited_4k60_source_comes_out_in_the_recorder_profiles_frame_and_rate(
    tmp_path,
):
    source = _clip_4k60(tmp_path / "src.mp4")
    out = tmp_path / "out.mov"
    profile = load_profile("iphone16_4k").video
    repurpose = Repurpose(source, out, seed=3).apply_device_preset("iphone16_usa")
    repurpose.video = VideoSettings(saturation=1.05)

    result = repurpose.run()

    video = _streams(out)["video"]
    assert result.step is not None
    assert (video["width"], video["height"]) == (profile.width, profile.height)
    assert abs(Fraction(video["r_frame_rate"]) - profile.fps) < FPS_SLACK
