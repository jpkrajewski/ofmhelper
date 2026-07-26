"""
extract_frames shells out to ffmpeg twice: a %03d frame sequence, and a
single static contact-sheet image. The contact-sheet command needs
`-update 1` -- some ffmpeg builds hard-error on the image2 muxer without it
("does not contain an image sequence pattern"), which is exactly what broke
in production against a short (few-second) reel. These tests run the real
ffmpeg binary (skipped if not on PATH) against a short synthetic clip --
short enough that the 4x4 contact-sheet tile grid isn't fully filled, the
same shape that triggered the original failure.
"""

import shutil
import subprocess

import pytest

from ofmhelpers.reel_machine.intake import _run_ffmpeg, extract_frames

ffmpeg_available = shutil.which("ffmpeg") is not None


@pytest.fixture
def short_video(tmp_path):
    path = tmp_path / "short.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=320x480:d=3",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        capture_output=True,
        check=True,
    )
    return path


@pytest.mark.skipif(not ffmpeg_available, reason="ffmpeg not on PATH")
def test_extract_frames_handles_a_clip_shorter_than_the_tile_grid(
    tmp_path, short_video
):
    frames_dir, contact_sheet = extract_frames(short_video, tmp_path)

    assert len(list(frames_dir.glob("frame-*.png"))) == 3  # 3s clip @ 1fps
    assert contact_sheet.is_file()
    assert contact_sheet.stat().st_size > 0


def test_run_ffmpeg_surfaces_stderr_instead_of_a_bare_error(tmp_path):
    if not ffmpeg_available:
        pytest.skip("ffmpeg not on PATH")

    with pytest.raises(RuntimeError, match="ffmpeg failed"):
        _run_ffmpeg(
            ["ffmpeg", "-y", "-i", str(tmp_path / "does-not-exist.mp4"), "out.png"]
        )


def test_run_ffmpeg_reports_missing_binary_clearly(monkeypatch):
    def _raise_not_found(*args, **kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", _raise_not_found)
    with pytest.raises(RuntimeError, match="ffmpeg isn't installed"):
        _run_ffmpeg(["ffmpeg", "-version"])
