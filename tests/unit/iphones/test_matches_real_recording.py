import datetime
import json
import re
import subprocess
from pathlib import Path

import pytest
from pydantic import JsonValue, RootModel
from repo_paths import FIXTURES

from ofmhelpers.media.recorder import load_profile
from ofmhelpers.media.recorder.models import Recording
from ofmhelpers.media.recorder.recorder import record
from ofmhelpers.media.repurpose import Repurpose

FIXTURE = FIXTURES / "iphones" / "iphone17_img0063.exif.json"
EXIFTOOL_ARGS = ["-j", "-a", "-G1:4", "-n", "-u"]

COPY = re.compile(r":Copy\d+")
FILE_SYSTEM = re.compile(r"^(SourceFile$|System:|ExifTool:)")
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
LOCATION = re.compile(r"GPS|Location")
NOT_REPRODUCIBLE = re.compile(r"^Track[3-9]:|^QuickTime(:Copy\d+)?:NextTrackID$")
EXIFTOOL_SKIPS_REAL_BOXES = re.compile(
    r"^Track1:(Unknown_hvcC|ColorProfiles|ColorPrimaries"
    r"|TransferCharacteristics|MatrixCoefficients)$"
)


class ExifDump(RootModel[list[dict[str, JsonValue]]]):
    @property
    def tags(self) -> dict[str, list[str]]:
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

    dst = tmp_path / "repurposed.mov"
    Repurpose(src, dst, recorded_at=end).apply_json_preset("iphone17").run()
    tags = _exif(dst).tags
    movie = tags["QuickTime:Duration"]
    assert tags["Track1:TrackDuration"] == movie
    assert tags["Track2:TrackDuration"] == movie
