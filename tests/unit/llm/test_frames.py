"""Contact sheets of videos a reel check really gets: audio running past
the picture, a file whose stream gives no length, and HDR exports (the
iPhone export writes HLG BT.2020 10-bit) -- each made here with ffmpeg."""

from pathlib import Path

import pytest
from PIL import Image, ImageStat

from ofmhelpers.llm.frames import contact_sheet, video_seconds
from ofmhelpers.utils import fake_media
from ofmhelpers.utils.ffmpeg import binary, run

# The iPhone export's SDR -> HDR step (iphones recorder `_video_filter`,
# profile.json), with the transfer swapped in.
_TO_HDR = (
    "zscale=tin=bt709:pin=bt709:min=bt709:t={transfer}:p=bt2020:m=bt2020nc"
    ":r=tv:npl=203,format=yuv420p10le"
)


def _ffmpeg(*args: str) -> None:
    run([binary("ffmpeg"), "-y", "-loglevel", "error", *args], timeout_s=60)


@pytest.fixture
def sdr(tmp_path) -> Path:
    """The fakes' 2 s colour-bar reel (testsrc), BT.709 SDR."""
    path = tmp_path / "sdr.mp4"
    path.write_bytes(fake_media.mp4())
    return path


def _hdr(sdr: Path, transfer: str) -> Path:
    out = sdr.with_name(f"{transfer}.mp4")
    _ffmpeg(
        "-i",
        str(sdr),
        "-vf",
        _TO_HDR.format(transfer=transfer),
        "-c:v",
        "libx264",
        "-color_primaries",
        "bt2020",
        "-color_trc",
        transfer,
        "-colorspace",
        "bt2020nc",
        "-an",
        str(out),
    )
    return out


def _saturation(sheet: Path) -> float:
    with Image.open(sheet) as img:
        return ImageStat.Stat(img.convert("HSV")).mean[1]


def test_stills_span_the_picture_not_audio_running_past_it(tmp_path):
    """2 s of picture, 4 s of sound: the file says 4 s, and stills planned
    from it would find no frame after 2 s (half the sheet gone)."""
    video = tmp_path / "long-audio.mp4"
    _ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=size=320x568:rate=25:duration=2",
        "-f",
        "lavfi",
        "-i",
        "sine=frequency=440:duration=4",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        str(video),
    )

    assert video_seconds(video, 60) == pytest.approx(2.0, abs=0.05)
    sheet = contact_sheet(video, tmp_path / "sheet.jpg", 4, 100, 60)
    with Image.open(sheet) as img:
        assert img.size == (2 * 100, 2 * 178)  # all 4 stills, 2x2


def test_a_file_whose_stream_gives_no_length_uses_the_files(sdr, tmp_path):
    """Matroska keeps no per-stream duration ffprobe reads."""
    video = tmp_path / "clip.mkv"
    _ffmpeg("-i", str(sdr), "-c", "copy", str(video))
    assert video_seconds(video, 60) == pytest.approx(2.0, abs=0.05)


@pytest.mark.parametrize("transfer", ["arib-std-b67", "smpte2084"])
def test_an_hdr_video_reads_like_its_sdr_original(sdr, tmp_path, transfer):
    """Shown as if SDR, the HLG export's stills lost about 40% of their
    saturation (PQ: 55%), the reel no longer as it plays on a phone."""
    hdr = _hdr(sdr, transfer)

    original = _saturation(contact_sheet(sdr, tmp_path / "sdr.jpg", 4, 160, 60))
    found = _saturation(contact_sheet(hdr, tmp_path / "hdr.jpg", 4, 160, 60))

    assert found == pytest.approx(original, rel=0.08)
