"""The shape of a device profile: everything a recording from one iPhone
model carries that does not change between recordings, decoded.

A profile is a JSON file per model (`iphones/<model>/profile.json`),
validated into `DeviceProfile`. Values that change per recording (the
creation date, location) are not in it; `PerRecording` names them.
"""

import datetime
import math
import random
from enum import IntEnum, StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DataType(IntEnum):
    """QuickTime well-known metadata value types (the `data` atom's type)."""

    UTF8 = 1
    SIGNED_INT_BE = 21


class PerRecording(StrEnum):
    """Values a key takes from the recording, not the profile."""

    CREATION_DATE = "creation_date"
    LOCATION_ISO6709 = "location_iso6709"
    LOCATION_ACCURACY = "location_accuracy"


class Locale(_Frozen):
    """A `data` atom's locale; both empty means "no locale" (0)."""

    country: str = Field(default="", max_length=2)
    language: str = Field(default="", max_length=3)


class MetadataKey(_Frozen):
    """One `mdta` key, in file order: its name, value type, locale, and
    either a fixed value or the per-recording value it takes."""

    key: str
    data_type: DataType
    locale: Locale = Locale()
    value: str | int | None = None
    per_recording: PerRecording | None = None
    # Width in bytes of a SIGNED_INT_BE value.
    int_width: int | None = Field(default=None, ge=1, le=8)


class Device(_Frozen):
    make: str
    model: str
    software: str


class Container(_Frozen):
    major_brand: str = Field(min_length=4, max_length=4)
    # Samples are written in runs of about this long per track.
    chunk_duration_ms: int = Field(gt=0)
    minor_version: int
    compatible_brands: list[str]
    movie_timescale: int
    # iPhones write ftyp, wide, mdat, then moov.
    moov_after_mdat: bool


class Handlers(_Frozen):
    """hdlr names and the data reference every track uses."""

    vendor: str = Field(min_length=4, max_length=4)
    video: str
    audio: str
    data: str
    # `alis` data reference with the self-reference flag (media in this file).
    data_reference: str = Field(min_length=4, max_length=4)
    media_language: str = Field(min_length=3, max_length=3)


class TimingPlan(_Frozen):
    """One recording's frame timing: which source frames (after the fps
    filter) are dropped, and the output frames from which every later
    timestamp runs one timescale tick late."""

    dropped: list[int]
    nudges: list[int]
    last_frame_ticks: int


class FrameTiming(_Frozen):
    """The camera's frame timing, read off the 7 samples' stts (1/600 s):
    the first frame lasts `first_frame_slots` frames (80 = 4 x 20, camera
    start), then `startup_gaps` gaps of `gap_drops` dropped frames each
    (40 / 60), each after one kept frame; and the clock runs one tick late
    once per `nudge_every` frames (a 21 among the 20s); the last frame is
    cut short by the stop (12-21)."""

    first_frame_slots: int = Field(ge=1)
    startup_gaps: tuple[int, int]
    gap_drops: tuple[int, int]
    nudge_every: int = Field(ge=1)
    # The last frame's duration in ticks (shorter than a slot: the stop).
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
    # HEVC general_level_idc (120 = level 4).
    level: int
    width: int
    height: int
    pix_fmt: str
    color_primaries: str
    color_transfer: str
    color_space: str
    color_range: str
    # Where an SDR source's white lands in the HLG output, in nits: 203 is
    # ITU-R BT.2408's HDR reference white (75% HLG signal). Lower makes an
    # SDR source look darker than the phone's own footage.
    sdr_white_nits: int = Field(gt=0)
    fps: int
    timescale: int
    bitrate_kbps: int
    b_frames: int
    # Frames between keyframes, and the NAL unit type every keyframe is
    # (21 = CRA: open GOP), as the `sync` sample group declares it.
    gop_size: int
    sync_nal_unit_type: int
    # Composition offsets stored signed (ctts v1), with a `cslg`.
    negative_cts_offsets: bool
    # vmhd: QuickTime graphics mode (64 = ditherCopy) and its op color.
    graphics_mode: int
    op_color: int
    timing: FrameTiming
    # Sample entry temporal and spatial quality (512 = codecNormalQuality).
    sample_quality: int
    # The NAL unit type the encoder's keyframes are, as written to the
    # `sync` sample group. x265 (closed GOP) makes IDR_W_RADL = 19; an
    # iPhone's are CRA (`sync_nal_unit_type`), which x265 4.1 cannot make.
    encoded_sync_nal_unit_type: int


class Audio(_Frozen):
    codec: str
    profile: str
    codec_tag: str = Field(min_length=4, max_length=4)
    sample_rate: int
    channels: int
    bitrate_kbps: int
    # esds DecoderConfigDescriptor fields as an iPhone writes them.
    buffer_size: int
    max_bitrate: int
    avg_bitrate: int


# Metres per degree of latitude (and of longitude at the equator).
_M_PER_DEGREE = 111_320.0


class GeoPoint(_Frozen):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    altitude_m: float

    @property
    def iso6709(self) -> str:
        """As an iPhone writes it: "+40.7549-073.9840+010.000/"."""
        return f"{self.latitude:+08.4f}{self.longitude:+09.4f}{self.altitude_m:+08.3f}/"

    def moved(self, north_m: float, east_m: float) -> "GeoPoint":
        lon_m = _M_PER_DEGREE * math.cos(math.radians(self.latitude))
        return self.model_copy(
            update={
                "latitude": self.latitude + north_m / _M_PER_DEGREE,
                "longitude": self.longitude + east_m / lon_m,
            }
        )


class Place(_Frozen):
    """Where recordings are filmed: a centre, a radius each recording lands
    in (uniform over the disc), and the GPS accuracy range it reports."""

    center: GeoPoint
    jitter_m: float = Field(ge=0)
    accuracy_m: tuple[float, float]

    def sample(self, rng: random.Random) -> tuple[GeoPoint, float]:
        # sqrt: uniform over the disc's area, not bunched at its centre.
        distance = self.jitter_m * math.sqrt(rng.random())
        bearing = rng.uniform(0, 2 * math.pi)
        point = self.center.moved(
            distance * math.cos(bearing), distance * math.sin(bearing)
        )
        return point, rng.uniform(*self.accuracy_m)


class Recording(_Frozen):
    """The per-recording values: when it ended (in the phone's local time
    zone), how long it ran, and where (None: Location Services off)."""

    end: AwareDatetime
    duration_s: float = Field(ge=0)
    location: GeoPoint | None = None
    location_accuracy_m: float | None = None
    # Flipped left-right, as a front camera's preview shows it.
    mirror: bool = False
    # Drives this recording's random choices (frame timing).
    seed: int = Field(default_factory=lambda: random.SystemRandom().getrandbits(32))

    @classmethod
    def ending_now(
        cls,
        tz: datetime.tzinfo,
        place: Place | None = None,
        rng: random.Random | None = None,
    ) -> "Recording":
        """A recording ending now in `tz`, filmed somewhere in `place`."""
        end = datetime.datetime.now(tz)
        if place is None:
            return cls(end=end, duration_s=0)
        point, accuracy = place.sample(rng or random.Random())  # noqa: S311 - not cryptography
        return cls(end=end, duration_s=0, location=point, location_accuracy_m=accuracy)

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
    """One iPhone model's recording profile, decoded from real samples."""

    name: str
    samples: int = Field(description="How many recordings it was checked against")
    device: Device
    container: Container
    handlers: Handlers
    video: Video
    audio: Audio
    # Movie-level `meta` keys and the video track's, each in file order.
    movie_keys: list[MetadataKey]
    video_track_keys: list[MetadataKey]
