# Copyright (C) 2026 jpkrajewski

import datetime
import random
from enum import IntEnum, StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ofmhelpers.config.types import GeoPoint, Place


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DataType(IntEnum):
    UTF8 = 1
    SIGNED_INT_BE = 21


class PerRecording(StrEnum):
    CREATION_DATE = "creation_date"
    LOCATION_ISO6709 = "location_iso6709"
    LOCATION_ACCURACY = "location_accuracy"


class Locale(_Frozen):
    country: str = Field(default="", max_length=2)
    language: str = Field(default="", max_length=3)


class MetadataKey(_Frozen):
    key: str
    data_type: DataType
    locale: Locale = Locale()
    value: str | int | None = None
    per_recording: PerRecording | None = None
    int_width: int | None = Field(default=None, ge=1, le=8)


class Device(_Frozen):
    make: str
    model: str
    software: str


class Container(_Frozen):
    major_brand: str = Field(min_length=4, max_length=4)
    chunk_duration_ms: int = Field(gt=0)
    minor_version: int
    compatible_brands: list[str]
    movie_timescale: int
    moov_after_mdat: bool


class Handlers(_Frozen):
    vendor: str = Field(min_length=4, max_length=4)
    video: str
    audio: str
    data: str
    data_reference: str = Field(min_length=4, max_length=4)
    media_language: str = Field(min_length=3, max_length=3)


class TimingPlan(_Frozen):
    dropped: list[int]
    nudges: list[int]
    last_frame_ticks: int


class FrameTiming(_Frozen):
    first_frame_slots: int = Field(ge=1)
    startup_gaps: tuple[int, int]
    gap_drops: tuple[int, int]
    nudge_every: int = Field(ge=1)
    last_frame_ticks: tuple[int, int]

    def plan(self, rng: random.Random, frames: int) -> TimingPlan:
        dropped = list(range(1, self.first_frame_slots))
        pos = self.first_frame_slots
        for _ in range(rng.randint(*self.startup_gaps)):
            drops = rng.randint(*self.gap_drops)
            dropped += range(pos + 1, pos + 1 + drops)
            pos += 1 + drops
        dropped = [i for i in dropped if i < frames - 1]
        kept = frames - len(dropped)
        count = max(1, round(kept / self.nudge_every))
        start = min(pos + 1, kept - 1)
        nudges = sorted(rng.sample(range(start, kept), min(count, kept - start)))
        return TimingPlan(
            dropped=dropped,
            nudges=nudges,
            last_frame_ticks=rng.randint(*self.last_frame_ticks),
        )


class Video(_Frozen):
    codec: str
    profile: str
    codec_tag: str = Field(min_length=4, max_length=4)
    compressor_name: str
    level: int
    width: int
    height: int
    pix_fmt: str
    color_primaries: str
    color_transfer: str
    color_space: str
    color_range: str
    sdr_white_nits: int = Field(gt=0)
    fps: int
    timescale: int
    bitrate_kbps: int
    b_frames: int
    x265_preset: str | None = None
    gop_size: int
    sync_nal_unit_type: int
    negative_cts_offsets: bool
    graphics_mode: int
    op_color: int
    timing: FrameTiming
    sample_quality: int
    encoded_sync_nal_unit_type: int


class Audio(_Frozen):
    codec: str
    profile: str
    codec_tag: str = Field(min_length=4, max_length=4)
    sample_rate: int
    channels: int
    bitrate_kbps: int
    buffer_size: int
    max_bitrate: int
    avg_bitrate: int


class Recording(_Frozen):
    end: AwareDatetime
    duration_s: float = Field(ge=0)
    location: GeoPoint | None = None
    location_accuracy_m: float | None = None
    mirror: bool = False
    seed: int = Field(default_factory=lambda: random.SystemRandom().getrandbits(32))

    @classmethod
    def ending_now(
        cls,
        tz: datetime.tzinfo,
        place: Place | None = None,
        rng: random.Random | None = None,
    ) -> Recording:
        end = datetime.datetime.now(tz)
        if place is None:
            return cls(end=end, duration_s=0)
        at = place.sample(rng or random.Random())  # noqa: S311 - not cryptography
        return cls(
            end=end,
            duration_s=0,
            location=at.point,
            location_accuracy_m=at.accuracy_m,
        )

    @property
    def start(self) -> datetime.datetime:
        return self.end - datetime.timedelta(seconds=self.duration_s)

    def value(self, field: PerRecording) -> str | None:
        if field is PerRecording.CREATION_DATE:
            return self.start.strftime("%Y-%m-%dT%H:%M:%S%z")
        if self.location is None:
            return None
        if field is PerRecording.LOCATION_ISO6709:
            return self.location.iso6709
        return f"{self.location_accuracy_m:.6f}"


class DeviceProfile(_Frozen):
    name: str
    samples: int = Field(description="How many recordings it was checked against")
    device: Device
    container: Container
    handlers: Handlers
    video: Video
    audio: Audio
    movie_keys: list[MetadataKey]
    video_track_keys: list[MetadataKey]
