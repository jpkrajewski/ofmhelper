# Copyright (C) 2026 jpkrajewski

import hashlib
import json
import subprocess
from fractions import Fraction
from pathlib import Path

import pytest

from ofmhelpers.features.repurpose import service
from ofmhelpers.media.recorder import load_profile
from ofmhelpers.media.repurpose.pipelines.errors import StillSoundsLikeSourceError
from ofmhelpers.media.repurpose.pipelines.keeper import ExecutionKeeper
from ofmhelpers.media.repurpose.pipelines.models import (
    AudioRetry,
    Execution,
    ExecutionKind,
    RepurposeResult,
)
from ofmhelpers.media.repurpose.pipelines.pipeline import Pipeline
from ofmhelpers.media.repurpose.presets import load_preset
from ofmhelpers.media.repurpose.repurpose import Repurpose
from ofmhelpers.media.repurpose.transformations.audio import AudioSettings
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


def _by_the_job(tmp_path: Path, source: Path) -> RepurposeResult:
    [result] = service.run_repurpose(str(tmp_path), str(source), 7)
    return result


def _by_the_pipeline(tmp_path: Path, source: Path) -> RepurposeResult:
    [result] = (
        Pipeline.paths(source, tmp_path / "IMG_7.mov")
        .return_on_unique()
        .seed(7)
        .load("spoof")
        .run()
    )
    return result


@pytest.mark.parametrize("made_by", [_by_the_job, _by_the_pipeline])
def test_a_spoof_copy_is_a_real_iphone_h264_file_that_passed_the_self_check(
    tmp_path,
    made_by,
):
    source = _clip(tmp_path / "src.mp4")

    result = made_by(tmp_path, source)

    copy = Path(result.path)
    streams = _streams(copy)
    assert copy.is_file()
    assert copy != source
    assert result.report is not None
    assert result.report.unique
    assert result.step is not None
    assert (streams["video"]["codec_name"], streams["video"]["height"]) == (
        "h264",
        1920,
    )
    assert streams["video"]["avg_frame_rate"] == "30/1"
    assert streams["audio"]["codec_name"] == "aac"


def _video_md5(path: Path) -> str:
    return subprocess.run(
        [
            "ffmpeg", "-v", "error", "-i", str(path),
            "-map", "0:v:0", "-c", "copy", "-f", "md5", "-",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()  # fmt: skip


def _clip_with_a_tune(dest: Path) -> Path:
    tune = (
        "0.3*sin(2*PI*110*t)"
        "+0.3*sin(2*PI*(220+60*floor(mod(2*t,4)))*t)"
        "+0.2*sin(2*PI*660*t)*sin(2*PI*3*t)"
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "mandelbrot=size=1080x1920:rate=30,trim=duration=10",
            "-f", "lavfi", "-i", f"aevalsrc='{tune}':d=10:s=44100",
            "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(dest),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return dest


class _PictureOfEachExecution(ExecutionKeeper):
    def __init__(self, copy: Path) -> None:
        self.copy = copy
        self.video_md5: dict[int, str] = {}

    def keep(self, execution: Execution) -> None:
        self.video_md5[execution.number] = _video_md5(self.copy)


def _picture_only_preset(dest: Path) -> Path:
    spoof = load_preset("spoof")
    preset = {"video": spoof.video.model_dump(mode="json", exclude_unset=True)}
    dest.write_text(json.dumps(preset), encoding="utf-8")
    return dest


def test_a_copy_that_only_sounds_like_its_source_gets_new_audio_on_the_same_picture(
    tmp_path,
):
    source = _clip_with_a_tune(tmp_path / "src.mp4")
    copy = tmp_path / "IMG_7.mov"
    pictures = _PictureOfEachExecution(copy)
    pipeline = (
        Pipeline.paths(source, copy)
        .seed(7)
        .device("iphone16_h264")
        .return_on_unique()
        .stage(
            str(_picture_only_preset(tmp_path / "picture_only.json")),
            max_attempts=4,
        )
    )
    pipeline.spec.audio_retries = [
        AudioRetry(audio=AudioSettings(pitch=3.0, highpass_hz=300.0), tries=2),
    ]
    pipeline.keep_executions_with(pictures)

    [result] = pipeline.run()

    picture_runs: list[Execution] = []
    audio_runs: list[Execution] = []
    for execution in result.executions:
        if execution.kind == ExecutionKind.PICTURE:
            assert audio_runs == []
            picture_runs.append(execution)
        else:
            audio_runs.append(execution)
    approved = picture_runs[-1]
    assert result.report is not None
    assert result.report.unique
    assert approved.verdict.picture_passed
    assert not approved.verdict.audio_passed
    assert 1 <= len(audio_runs) <= 2
    assert audio_runs[-1].verdict.passed
    for audio_run in audio_runs:
        assert (
            pictures.video_md5[audio_run.number]
            == (pictures.video_md5[approved.number])
        )
    assert result.attempts == len(result.executions) <= 6
    assert result.step is not None
    assert result.step.audio.highpass_hz == 300.0
    assert _streams(copy)["audio"]["codec_name"] == "aac"
    assert not copy.with_name("IMG_7.audio.mov").exists()


def _pipeline_whose_audio_retry_changes_nothing(source: Path, copy: Path) -> Pipeline:
    pipeline = (
        Pipeline.paths(source, copy)
        .seed(7)
        .device("iphone16_h264")
        .return_on_unique()
        .stage(
            str(_picture_only_preset(copy.with_name("picture_only.json"))),
            max_attempts=4,
        )
    )
    pipeline.spec.audio_retries = [AudioRetry(audio=AudioSettings(), tries=1)]
    return pipeline


def test_a_copy_whose_sound_never_passes_is_delivered_with_its_first_sound(tmp_path):
    source = _clip_with_a_tune(tmp_path / "src.mp4")
    copy = tmp_path / "IMG_7.mov"
    files = _FileOfEachExecution(copy)
    pipeline = _pipeline_whose_audio_retry_changes_nothing(source, copy)
    pipeline.spec.keep_first_sound = True
    pipeline.keep_executions_with(files)

    [result] = pipeline.run()

    approved = result.executions[-2]
    audio_retry = result.executions[-1]
    assert result.kept_first_sound
    assert (approved.kind, audio_retry.kind) == (
        ExecutionKind.PICTURE,
        ExecutionKind.AUDIO,
    )
    assert approved.verdict.picture_passed
    assert not audio_retry.verdict.audio_passed
    assert result.report == approved.verdict.report
    assert _sha256(copy) == files.sha256[approved.number]
    assert _sha256(copy) != files.sha256[audio_retry.number]
    assert not copy.with_name("IMG_7.picture.mov").exists()


def test_an_audio_redo_that_crashes_still_delivers_the_first_sound(tmp_path):
    source = _clip_with_a_tune(tmp_path / "src.mp4")
    copy = tmp_path / "IMG_7.mov"
    files = _FileOfEachExecution(copy)
    pipeline = _pipeline_whose_audio_retry_changes_nothing(source, copy)
    pipeline.spec.audio_retries = [
        AudioRetry(audio=AudioSettings(highpass_hz=-5.0), tries=1),
    ]
    pipeline.spec.keep_first_sound = True
    pipeline.keep_executions_with(files)

    [result] = pipeline.run()

    approved = result.executions[-2]
    crashed = result.executions[-1]
    assert result.kept_first_sound
    assert crashed.kind == ExecutionKind.AUDIO
    assert crashed.verdict.broken_audio is not None
    assert crashed.verdict.broken_audio.startswith("audio redo failed: ")
    assert _sha256(copy) == files.sha256[approved.number]
    assert not copy.with_name("IMG_7.picture.mov").exists()


def test_without_keep_first_sound_the_same_copy_fails_and_leaves_no_file(tmp_path):
    source = _clip_with_a_tune(tmp_path / "src.mp4")
    copy = tmp_path / "IMG_7.mov"
    pipeline = _pipeline_whose_audio_retry_changes_nothing(source, copy)

    with pytest.raises(StillSoundsLikeSourceError, match="Sound still matches"):
        pipeline.run()

    assert not copy.exists()
    assert not copy.with_name("IMG_7.picture.mov").exists()


class _FileOfEachExecution(ExecutionKeeper):
    def __init__(self, copy: Path) -> None:
        self.copy = copy
        self.sha256: dict[int, str] = {}

    def keep(self, execution: Execution) -> None:
        if execution.verdict.broken_audio is None:
            self.sha256[execution.number] = _sha256(self.copy)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


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
