"""`as_mp4`: a .mov/.webm/.mkv/.m4v reference as an MP4 copy for providers
that take MP4 only (prod 2026-11-02: an iPhone .mov failed a Higgsfield
/clone run before upload). H.264 video and AAC/MP3 audio are copied
untouched; anything else is encoded, with HDR turned into SDR. The copy
only ever appears whole, and is reused until its source changes."""

import json
import os
from pathlib import Path

import pytest

from ofmhelpers.utils import video_mp4
from ofmhelpers.utils.ffmpeg import binary, run, run_bytes
from ofmhelpers.utils.video_mp4 import _mp4_args, _Probe, _Stream, as_mp4

SRC = Path("/in/clip.mov")
DEST = Path("/out/clip.mp4")


def _args(*streams: _Stream) -> list[str]:
    return _mp4_args(_Probe(streams=list(streams)), SRC, DEST)


def _after(args: list[str], flag: str) -> str:
    return args[args.index(flag) + 1]


H264 = _Stream(codec_type="video", codec_name="h264")
HEVC = _Stream(codec_type="video", codec_name="hevc")
AAC = _Stream(codec_type="audio", codec_name="aac")


def test_h264_and_aac_are_copied_untouched():
    args = _args(H264, AAC)

    assert _after(args, "-c:v") == "copy"
    assert _after(args, "-c:a") == "copy"
    assert args[-3:] == ["-f", "mp4", str(DEST)]


def test_hevc_is_encoded_to_visually_lossless_h264():
    args = _args(HEVC, AAC)

    assert _after(args, "-c:v") == "libx264"
    assert _after(args, "-crf") == "17"
    assert _after(args, "-pix_fmt") == "yuv420p"
    assert "-vf" not in args, "an SDR picture needs no colour filter"


@pytest.mark.parametrize("transfer", ["arib-std-b67", "smpte2084"])
def test_an_hdr_picture_is_turned_into_bt709_sdr(transfer):
    hdr = HEVC.model_copy(update={"color_transfer": transfer})

    args = _args(hdr, AAC)

    assert _after(args, "-vf").startswith(f"zscale=tin={transfer}:pin=bt2020")
    assert not _after(args, "-vf").endswith(",")
    assert _after(args, "-color_trc") == "bt709"


@pytest.mark.parametrize("codec", ["pcm_s16le", "alac", "opus"])
def test_audio_an_mp4_may_not_play_is_encoded_to_aac(codec):
    args = _args(H264, _Stream(codec_type="audio", codec_name=codec))

    assert (_after(args, "-c:a"), _after(args, "-b:a")) == ("aac", "192k")


def test_only_the_first_video_and_audio_are_kept():
    """A .mov's timecode/data tracks would make the MP4 muxer fail."""
    data = _Stream(codec_type="data", codec_name="tmcd")

    args = _args(data, H264, AAC, AAC)

    maps = [args[i + 1] for i, a in enumerate(args) if a == "-map"]
    assert maps == ["0:v:0", "0:a:0"]


def test_no_audio_maps_no_audio():
    args = _args(H264)

    assert "0:a:0" not in args
    assert "-c:a" not in args


def test_a_file_without_video_is_refused():
    with pytest.raises(ValueError, match="no video"):
        _args(AAC)


@pytest.mark.parametrize("name", ["clip.mp4", "CLIP.MP4", "face.png", "notes.txt"])
def test_an_mp4_or_a_non_video_is_used_as_it_is(tmp_path, name):
    src = tmp_path / name
    src.write_bytes(b"x")

    assert as_mp4(src, tmp_path / "out", 30) == src
    assert not (tmp_path / "out").exists()


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    """ffprobe says H.264; ffmpeg writes its output file. Every call recorded."""
    calls: list[list[str]] = []

    def fake_run(args, timeout_s):
        calls.append(args)
        Path(args[-1]).write_bytes(b"mp4 bytes")
        return ""

    monkeypatch.setattr(video_mp4, "_probe", lambda _src, _t: _Probe(streams=[H264]))
    monkeypatch.setattr(video_mp4, "run", fake_run)
    return calls


def test_a_copy_is_made_once_then_reused(tmp_path, fake_ffmpeg):
    src = tmp_path / "abc__Video-1.MOV"
    src.write_bytes(b"mov")

    first = as_mp4(src, tmp_path / "out", 30)
    second = as_mp4(src, tmp_path / "out", 30)

    assert first == second
    assert first.suffix == ".mp4"
    assert first.name.startswith("abc__Video-1-")
    assert first.read_bytes() == b"mp4 bytes"
    assert len(fake_ffmpeg) == 1
    assert src.read_bytes() == b"mov", "the original is never touched"


def test_a_changed_source_gets_a_new_copy(tmp_path, fake_ffmpeg):
    src = tmp_path / "clip.mov"
    src.write_bytes(b"mov")
    first = as_mp4(src, tmp_path / "out", 30)

    src.write_bytes(b"another mov")
    os.utime(src, ns=(1, 1))

    assert as_mp4(src, tmp_path / "out", 30) != first
    assert len(fake_ffmpeg) == 2


def test_two_sources_with_one_stem_never_share_a_copy(tmp_path, fake_ffmpeg):
    a, b = tmp_path / "a" / "clip.mov", tmp_path / "b" / "clip.mov"
    for path in (a, b):
        path.parent.mkdir()
        path.write_bytes(b"mov")

    assert as_mp4(a, tmp_path / "out", 30) != as_mp4(b, tmp_path / "out", 30)
    assert len(fake_ffmpeg) == 2


def test_a_failed_encode_leaves_no_file_and_is_tried_again(tmp_path, monkeypatch):
    """ffmpeg killed mid-write: neither half a copy nor its temp file stays,
    so the next upload converts again instead of sending a broken video."""
    attempts = []

    def half_then_fail(args, timeout_s):
        attempts.append(args)
        Path(args[-1]).write_bytes(b"half")
        msg = "ffmpeg timed out after 30 s"
        raise RuntimeError(msg)

    monkeypatch.setattr(video_mp4, "_probe", lambda _src, _t: _Probe(streams=[HEVC]))
    monkeypatch.setattr(video_mp4, "run", half_then_fail)
    src = tmp_path / "clip.mov"
    src.write_bytes(b"mov")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="timed out"):
            as_mp4(src, tmp_path / "out", 30)
        assert list((tmp_path / "out").iterdir()) == []
    assert len(attempts) == 2


def test_a_file_ffprobe_cannot_read_raises_and_leaves_nothing(tmp_path):
    src = tmp_path / "broken.mov"
    src.write_bytes(b"not a video")

    with pytest.raises(RuntimeError, match="ffprobe failed"):
        as_mp4(src, tmp_path / "out", 30)
    assert list((tmp_path / "out").iterdir()) == []


# --- real ffmpeg --------------------------------------------------------------


def _ffmpeg(*args: str) -> None:
    run([binary("ffmpeg"), "-y", "-loglevel", "error", *args], timeout_s=120)


def _streams(path: Path) -> list[dict]:
    entries = (
        "stream=codec_type,codec_name,width,height,pix_fmt,color_transfer"
        ":stream_side_data=rotation"
    )
    probe = [binary("ffprobe"), "-v", "error", "-show_entries", entries]
    out = run([*probe, "-of", "json", str(path)], timeout_s=30)
    return json.loads(out)["streams"]


def _rotated(src: Path, dest: Path) -> Path:
    """`src` with a 90 degree display matrix, as a phone held upright writes."""
    _ffmpeg("-display_rotation", "90", "-i", str(src), "-c", "copy", str(dest))
    return dest


@pytest.mark.heavy
def test_an_iphone_hdr_hevc_mov_becomes_upright_sdr_h264(tmp_path):
    """What an iPhone records by default: HEVC 10-bit HLG BT.2020, AAC, the
    rotation in the display matrix."""
    hlg = tmp_path / "hlg.mov"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=1",
        "-f", "lavfi", "-i", "sine=d=1",
        "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1",
        "-x265-params",
        "log-level=none:colorprim=bt2020:transfer=arib-std-b67:colormatrix=bt2020nc",
        "-c:a", "aac", str(hlg),
    )  # fmt: skip
    src = _rotated(hlg, tmp_path / "IMG_0001.MOV")

    video, audio = _streams(as_mp4(src, tmp_path / "out", 120))

    assert (video["codec_name"], video["pix_fmt"]) == ("h264", "yuv420p")
    assert video["color_transfer"] == "bt709"
    assert (video["width"], video["height"]) == (180, 320), "turned upright"
    assert audio["codec_name"] == "aac"


@pytest.mark.heavy
def test_an_h264_mov_is_remuxed_with_its_picture_untouched(tmp_path):
    plain = tmp_path / "plain.mov"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=1",
        "-f", "lavfi", "-i", "sine=d=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(plain),
    )  # fmt: skip
    src = _rotated(plain, tmp_path / "screen.mov")

    out = as_mp4(src, tmp_path / "out", 120)

    def picture(path: Path) -> bytes:
        read = [binary("ffmpeg"), "-v", "error", "-i", str(path), "-map", "0:v:0"]
        return run_bytes([*read, "-c", "copy", "-f", "h264", "-"], timeout_s=30)

    assert picture(out) == picture(src)
    video, audio = _streams(out)
    assert video["side_data_list"][0]["rotation"] == 90, "rotation kept"
    assert audio["codec_name"] == "aac"


@pytest.mark.heavy
def test_a_vp9_webm_without_sound_becomes_h264_without_sound(tmp_path):
    src = tmp_path / "clip.webm"
    _ffmpeg(
        "-f", "lavfi", "-i", "testsrc2=s=160x90:r=30:d=1",
        "-c:v", "libvpx-vp9", "-b:v", "200k", str(src),
    )  # fmt: skip

    streams = _streams(as_mp4(src, tmp_path / "out", 120))

    assert [s["codec_name"] for s in streams] == ["h264"]
