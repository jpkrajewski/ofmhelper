"""TikTok's H.264 re-encode is bounded (review #12): an ffmpeg that runs
past `OFM_DOWNLOAD_REENCODE_TIMEOUT_S` is stopped, its partial output
removed and the downloaded original kept, as on any ffmpeg failure."""

import sys
import time

from ofmhelpers.downloaders import generic
from ofmhelpers.downloaders.generic import DownloadConfig

# ffmpeg stand-ins: write the output (argv[1]), then hang / fail / finish.
_HANG = "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_bytes(b'p'); time.sleep(60)"
_FAIL = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'p'); sys.exit(1)"
_DONE = "import pathlib,sys; pathlib.Path(sys.argv[1]).write_bytes(b'h264')"


def _ffmpeg(monkeypatch, script: str) -> None:
    monkeypatch.setattr(
        DownloadConfig,
        "ffmpeg_cmd",
        lambda self, src, dst: [sys.executable, "-c", script, str(dst)],
    )


def _original(tmp_path):
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"hevc")
    return path


def test_a_hanging_re_encode_is_stopped_and_the_original_kept(monkeypatch, tmp_path):
    monkeypatch.setenv("OFM_DOWNLOAD_REENCODE_TIMEOUT_S", "1")
    _ffmpeg(monkeypatch, _HANG)
    original = _original(tmp_path)

    started = time.monotonic()
    kept = generic.reencode_to_h264(original, DownloadConfig(output_dir=tmp_path))

    assert time.monotonic() - started < 10
    assert kept == original
    assert original.read_bytes() == b"hevc"
    assert not (tmp_path / "clip_h264.mp4").exists()


def test_a_failing_re_encode_keeps_the_original_and_no_partial(monkeypatch, tmp_path):
    _ffmpeg(monkeypatch, _FAIL)
    original = _original(tmp_path)

    kept = generic.reencode_to_h264(original, DownloadConfig(output_dir=tmp_path))

    assert kept == original
    assert not (tmp_path / "clip_h264.mp4").exists()


def test_a_finished_re_encode_replaces_the_original(monkeypatch, tmp_path):
    _ffmpeg(monkeypatch, _DONE)
    original = _original(tmp_path)

    kept = generic.reencode_to_h264(original, DownloadConfig(output_dir=tmp_path))

    assert kept == tmp_path / "clip_h264.mp4"
    assert kept.read_bytes() == b"h264"
    assert not original.exists()
