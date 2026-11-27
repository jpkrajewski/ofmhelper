# Copyright (C) 2026 jpkrajewski

import subprocess

import pytest

from ofmhelpers.media.repurpose.audio_check import levels, verify_audio


def _verify_audio(source, copy) -> None:
    verify_audio(levels(source), copy)


def _tone(path, volume_db: float, *, audio: bool = True):
    args = ["ffmpeg", "-y", "-loglevel", "error"]
    args += ["-f", "lavfi", "-i", "color=black:size=64x64:rate=10:duration=2"]
    if audio:
        args += ["-f", "lavfi", "-i", "sine=frequency=440:duration=2"]
        args += ["-af", f"volume={volume_db}dB,tremolo=f=2:d=0.9", "-shortest"]
    args += ["-c:v", "libx264", str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.mark.heavy
def test_a_quiet_reel_makes_a_quiet_copy_that_passes(tmp_path):
    source = _tone(tmp_path / "src.mp4", -38)
    copy = _tone(tmp_path / "copy.mp4", -39)

    _verify_audio(source, copy)


@pytest.mark.heavy
def test_a_copy_far_under_its_source_fails(tmp_path):
    source = _tone(tmp_path / "src.mp4", -6)
    copy = _tone(tmp_path / "copy.mp4", -30)

    with pytest.raises(RuntimeError, match="audio mean"):
        _verify_audio(source, copy)


@pytest.mark.heavy
def test_a_copy_without_sound_fails(tmp_path):
    source = _tone(tmp_path / "src.mp4", -6)
    copy = _tone(tmp_path / "copy.mp4", 0, audio=False)

    with pytest.raises(RuntimeError, match="no audio stream"):
        _verify_audio(source, copy)
