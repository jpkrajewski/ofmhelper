"""A recording made as `iphone17` against a real iPhone 17 Pro Max file,
tag by tag, both read by the same exiftool command.

The fixture is the full exiftool dump of IMG_0063.MOV (one of the 7
recordings the profile was decoded from, location included), made in the
Docker tests image with the same arguments as `EXIFTOOL_ARGS`:

    exiftool -j -a -G1:4 -n -u IMG_0063.MOV
        > tests/iphones/fixtures/iphone17_img0063.exif.json

`-G1:4` keeps duplicate tags (a track's media and data handler both have a
HandlerClass). Copy numbers count across the whole file, so they are
dropped and each group:tag compares as the sorted list of its values. Every tag must match except those in
`FILE_SYSTEM`, `PER_RECORDING`, `LOCATION`, `NOT_REPRODUCIBLE` and
`EXIFTOOL_SKIPS_REAL_BOXES`, each with its reason.
"""

import datetime
import json
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import JsonValue, RootModel
from repo_paths import FIXTURES

from ofmhelpers.iphones import load_profile
from ofmhelpers.iphones.models import Recording
from ofmhelpers.iphones.recorder import record
from ofmhelpers.repurpose import Repurpose

FIXTURE = FIXTURES / "iphones" / "iphone17_img0063.exif.json"
EXIFTOOL_ARGS = ["-j", "-a", "-G1:4", "-n", "-u"]

COPY = re.compile(r":Copy\d+")
# About the file on disk, not its content.
FILE_SYSTEM = re.compile(r"^(SourceFile$|System:|ExifTool:)")
# Differ between any two real recordings: when, how long, how big.
PER_RECORDING = re.compile(
    r"SourceFile$"
    r"|(Create|Modify|Creation)Date$"
    r"|Duration$"
    r"|MediaData(Size)?$"
    r"|VideoFrameRate$"
    r"|AvgBitrate$"
    r"|(SampleSizes|ChunkOffset|SampleToChunk|TimeToSampleTable"
    r"|CompositionTimeToSample|SyncSampleTable|IdependentAndDisposableSamples"
    r"|SampleToGroup)$"
)
# Where it was filmed: a recording's own, compared by the location tests.
LOCATION = re.compile(r"GPS|Location")
# What ffmpeg cannot write (see iphones/recorder.py): tracks 3-8 (APAC
# spatial audio, mebx timed metadata) and the movie's next track ID that
# counts them.
NOT_REPRODUCIBLE = re.compile(r"^Track[3-9]:|^QuickTime(:Copy\d+)?:NextTrackID$")
# exiftool 13.25 reads the real hvc1 entry (hvcC, dvvC, colr, amve) as one
# binary block and parses none of its boxes, while it parses ours (hvcC,
# colr). The bytes are compared directly instead, in
# test_video_sample_entry_boxes_match_the_real_ones.
EXIFTOOL_SKIPS_REAL_BOXES = re.compile(
    r"^Track1:(Unknown_hvcC|ColorProfiles|ColorPrimaries"
    r"|TransferCharacteristics|MatrixCoefficients)$"
)


class ExifDump(RootModel[list[dict[str, JsonValue]]]):
    """exiftool -j output: one {group:tag -> value} object per file."""

    @property
    def tags(self) -> dict[str, list[str]]:
        """group:tag -> its values (duplicates included), copy numbers off."""
        [only] = self.root
        out: dict[str, list[str]] = {}
        for key, value in only.items():
            out.setdefault(COPY.sub("", key), []).append(json.dumps(value))
        return {k: sorted(v) for k, v in out.items()}


def _exif(path: Path) -> ExifDump:
    out = subprocess.run(
        ["exiftool", *EXIFTOOL_ARGS, str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return ExifDump.model_validate_json(out)


def _sample(dest: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=720x1280:duration=2:rate=25",
            "-f", "lavfi", "-i", "sine=duration=2",
            "-shortest", "-pix_fmt", "yuv420p", str(dest),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return dest


def _compared(tag: str) -> bool:
    return not any(
        rule.search(tag)
        for rule in (
            FILE_SYSTEM,
            PER_RECORDING,
            LOCATION,
            NOT_REPRODUCIBLE,
            EXIFTOOL_SKIPS_REAL_BOXES,
        )
    )


@pytest.mark.slow
@pytest.mark.heavy
def test_recording_matches_a_real_iphone17_file_tag_for_tag(tmp_path: Path):
    real = ExifDump.model_validate_json(FIXTURE.read_text(encoding="utf-8")).tags
    end = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=-3)))
    dst = tmp_path / "out.mov"

    record(
        _sample(tmp_path / "in.mp4"),
        dst,
        load_profile("iphone17"),
        Recording(end=end, duration_s=0),
        120,
    )

    made = _exif(dst).tags
    diff = {
        tag: {"real": real.get(tag), "made": made.get(tag)}
        for tag in sorted(set(real) | set(made))
        if _compared(tag) and real.get(tag) != made.get(tag)
    }
    assert diff == {}, json.dumps(diff, indent=1)

    # The same through repurpose: the iphone17 preset.
    dst = tmp_path / "repurposed.mov"
    Repurpose(tmp_path / "in.mp4", dst, recorded_at=end).apply_json_preset(
        "iphone17"
    ).run()
    made = _exif(dst).tags
    diff = {
        tag: {"real": real.get(tag), "made": made.get(tag)}
        for tag in sorted(set(real) | set(made))
        if _compared(tag) and real.get(tag) != made.get(tag)
    }
    assert diff == {}, json.dumps(diff, indent=1)


# From the real IMG_0063 hvc1 entry: colr box, and hvcC's
# configurationVersion, profile space/tier/idc, compatibility flags,
# then level -- the constraint flags (6 bytes) between them differ:
# the iPhone sets general_non_packed_constraint_flag (0xb0), x265 has no
# option for it (0x90).
REAL_COLR = bytes.fromhex("00000012636f6c726e636c63000900120009")
REAL_HVCC_PROFILE = bytes.fromhex("010220000000")
REAL_HVCC_LEVEL = 0x78


@pytest.mark.slow
@pytest.mark.heavy
def test_video_sample_entry_boxes_match_the_real_ones(tmp_path: Path):
    dst = tmp_path / "out.mov"
    end = datetime.datetime.now(datetime.UTC)
    record(
        _sample(tmp_path / "in.mp4"),
        dst,
        load_profile("iphone17"),
        Recording(end=end, duration_s=0),
        120,
    )

    data = dst.read_bytes()
    hvcc = data[data.index(b"hvcC") + 4 :]
    assert hvcc[:6] == REAL_HVCC_PROFILE
    assert hvcc[12] == REAL_HVCC_LEVEL
    assert REAL_COLR in data

    # The same through repurpose: the iphone17 preset.
    dst = tmp_path / "repurposed.mov"
    Repurpose(tmp_path / "in.mp4", dst, recorded_at=end).apply_json_preset(
        "iphone17"
    ).run()
    data = dst.read_bytes()
    hvcc = data[data.index(b"hvcC") + 4 :]
    assert hvcc[:6] == REAL_HVCC_PROFILE
    assert hvcc[12] == REAL_HVCC_LEVEL
    assert REAL_COLR in data


@pytest.mark.slow
@pytest.mark.heavy
def test_every_track_ends_with_the_movie_like_an_iphone(tmp_path: Path):
    src = tmp_path / "in.mp4"
    # Audio outruns the video by 0.4 s, as in a downloaded reel.
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=720x1280:duration=2:rate=25",
            "-f", "lavfi", "-i", "sine=duration=2.4", str(src),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    dst = tmp_path / "out.mov"
    end = datetime.datetime.now(datetime.UTC)
    record(src, dst, load_profile("iphone17"), Recording(end=end, duration_s=0), 120)

    tags = _exif(dst).tags
    movie = tags["QuickTime:Duration"]
    assert tags["Track1:TrackDuration"] == movie
    assert tags["Track2:TrackDuration"] == movie

    # The same through repurpose: the iphone17 preset.
    dst = tmp_path / "repurposed.mov"
    Repurpose(src, dst, recorded_at=end).apply_json_preset("iphone17").run()
    tags = _exif(dst).tags
    movie = tags["QuickTime:Duration"]
    assert tags["Track1:TrackDuration"] == movie
    assert tags["Track2:TrackDuration"] == movie
