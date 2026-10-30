"""The snapchat pipeline's audio self-check judges a copy against its own
source: a nearly silent reel (mean -50 dB, prod 2026-10-30) makes a nearly
silent copy and passes; a copy far quieter than its source, or one with no
sound at all, still fails."""

import subprocess

import pytest

from ofmhelpers.repurpose.pipelines.snapchat_iphone16_usa import _verify_audio


def _tone(path, volume_db: float, *, audio: bool = True):
    """2 s of a pulsing tone at `volume_db` (pulses: real dynamics)."""
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

    _verify_audio(source, copy)  # mean ~-50 dB: under the old absolute floor


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
