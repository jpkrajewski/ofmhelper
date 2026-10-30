"""A full recording, read back with exiftool (Docker test image) as the
independent check."""

import datetime
import json
import subprocess
from pathlib import Path

import pytest
from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.iphones import load_profile
from ofmhelpers.iphones.models import Recording
from ofmhelpers.iphones.recorder import record
from ofmhelpers.repurpose import Repurpose


class ExifRead(BaseModel):
    """The exiftool -j -G1 fields this test checks."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    major_brand: str = Field(alias="QuickTime:MajorBrand")
    minor_version: str = Field(alias="QuickTime:MinorVersion")
    make: str = Field(alias="Keys:Make")
    model: str = Field(alias="Keys:Model")
    software: str = Field(alias="Keys:Software")
    creation_date: str = Field(alias="Keys:CreationDate")
    full_frame_rate: int = Field(alias="Keys:FullFrameRatePlaybackIntent")
    lens: str = Field(alias="VideoKeys:LensModel-eng-US")
    maker_note_74: int = Field(alias="VideoKeys:Apple-maker-note74")
    video_handler: str = Field(alias="Track1:HandlerDescription")
    video_vendor: str = Field(alias="Track1:HandlerVendorID")
    video_language: str = Field(alias="Track1:MediaLanguageCode")
    compressor: str = Field(alias="Track1:CompressorName")
    aperture: str = Field(alias="Track1:CleanApertureDimensions")
    audio_rate: int = Field(alias="Track2:AudioSampleRate")


def _clip(dest: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-loglevel", "error",
            "-f", "lavfi", "-i", "testsrc=size=720x1280:duration=1:rate=25",
            "-f", "lavfi", "-i", "sine=duration=1",
            "-shortest", "-pix_fmt", "yuv420p", str(dest),
        ],
        check=True,
        capture_output=True,
    )  # fmt: skip
    return dest


@pytest.mark.slow
@pytest.mark.heavy
def test_recording_reads_back_as_the_iphone17_profile(tmp_path: Path):
    profile = load_profile("iphone17")
    end = datetime.datetime(
        2026, 10, 19, 15, 0, 1, tzinfo=datetime.timezone(datetime.timedelta(hours=-3))
    )
    dst = tmp_path / "out.mov"

    record(
        _clip(tmp_path / "in.mp4"), dst, profile, Recording(end=end, duration_s=0), 120
    )

    out = subprocess.run(
        ["exiftool", "-j", "-G1", "-n", "-u", str(dst)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    read = ExifRead.model_validate(json.loads(out)[0])
    assert read.major_brand == "qt  "
    assert read.minor_version == "0.0.0"
    assert (read.make, read.model, read.software) == (
        "Apple",
        "iPhone 17 Pro Max",
        "26.5.1",
    )
    assert read.creation_date == "2026:10:19 15:00:00-03:00"
    assert read.full_frame_rate == 1
    assert read.lens == "iPhone 17 Pro Max front camera 2.715mm f/1.9"
    assert read.maker_note_74 == 6
    assert read.video_handler == "Core Media Video"
    assert read.video_vendor == "appl"
    assert read.video_language == "und"
    assert read.compressor == "HEVC"
    assert read.aperture == "1080 1920"
    assert read.audio_rate == 48000
    assert b"FFMP" not in dst.read_bytes()

    # The same through repurpose: the iphone17 preset, same recording time.
    dst = tmp_path / "repurposed.mov"
    Repurpose(tmp_path / "in.mp4", dst, recorded_at=end).apply_json_preset(
        "iphone17"
    ).run()
    out = subprocess.run(
        ["exiftool", "-j", "-G1", "-n", "-u", str(dst)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    read = ExifRead.model_validate(json.loads(out)[0])
    assert read.major_brand == "qt  "
    assert read.minor_version == "0.0.0"
    assert (read.make, read.model, read.software) == (
        "Apple",
        "iPhone 17 Pro Max",
        "26.5.1",
    )
    assert read.creation_date == "2026:10:19 15:00:00-03:00"
    assert read.full_frame_rate == 1
    assert read.lens == "iPhone 17 Pro Max front camera 2.715mm f/1.9"
    assert read.maker_note_74 == 6
    assert read.video_handler == "Core Media Video"
    assert read.video_vendor == "appl"
    assert read.video_language == "und"
    assert read.compressor == "HEVC"
    assert read.aperture == "1080 1920"
    assert read.audio_rate == 48000
    assert b"FFMP" not in dst.read_bytes()
