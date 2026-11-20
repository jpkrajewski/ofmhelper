import datetime
import io
import random
from fractions import Fraction
from pathlib import Path
from zoneinfo import ZoneInfo

from PIL import Image
from pydantic import BaseModel

from ofmhelpers.media.hdr import sdr_filters
from ofmhelpers.media.recorder import load_profile
from ofmhelpers.media.repurpose.devices import load_device
from ofmhelpers.media.repurpose.photos.distinct import Still, changed, pdq, pick
from ofmhelpers.media.repurpose.photos.exif import exif_for, jpeg
from ofmhelpers.media.repurpose.photos.models import (
    Photo,
    PhotoPreset,
    Shot,
    Stills,
    load_photo_preset,
)
from ofmhelpers.media.trim import frame
from ofmhelpers.media.video_probe import colour_tags, frame_times
lazy from ofmhelpers.config.types import PlaceSample


class StillError(RuntimeError): ...


def _rng(seed: str, purpose: str) -> random.Random:
    return random.Random(f"{seed}/{purpose}")  # noqa: S311 - not cryptography


def _shots(
    count: int,
    tz: ZoneInfo,
    place: PlaceSample | None,
    preset: PhotoPreset,
    rng: random.Random,
) -> list[Shot]:
    taken = datetime.datetime.now(tz)
    shots: list[Shot] = []
    for _ in range(count):
        shots.append(
            Shot(
                taken=taken,
                place=place,
                exposure=Fraction(rng.choice(preset.lens.exposures)),
                iso=rng.randint(*preset.lens.iso),
            )
        )
        taken -= datetime.timedelta(seconds=rng.uniform(*preset.stills.gap_s))
    return shots[::-1]


class _Video(BaseModel, frozen=True):
    path: Path
    to_sdr: str
    timeout_s: int

    @classmethod
    def of(cls, path: Path, timeout_s: int) -> _Video:
        tags = colour_tags(path, timeout_s)
        to_sdr = sdr_filters(
            tags.color_transfer, tags.color_primaries, tags.color_space
        )
        return cls(path=path, to_sdr=to_sdr.rstrip(","), timeout_s=timeout_s)

    def still(self, at: float, short_side: int) -> Image.Image:
        raw = frame(
            self.path,
            at,
            short_side=short_side,
            threads=0,
            timeout_s=self.timeout_s,
            picture=self.to_sdr or None,
        )
        if not raw:
            msg = f"no still at {at:.3f} s of {self.path.name}"
            raise StillError(msg)
        with Image.open(io.BytesIO(raw)) as img:
            return img.convert("RGB")

    def stills(self, count: int, how: Stills, rng: random.Random) -> list[Still]:
        times = frame_times(self.path, self.timeout_s)[:-1]
        if not times:
            msg = f"{self.path.name} has no video frames"
            raise StillError(msg)
        picked: list[float] = []
        for i in range(count):
            lo = len(times) * (i + how.slice[0]) / count
            hi = len(times) * (i + how.slice[1]) / count
            frames = times[int(lo) : max(int(lo) + 1, int(hi))]
            if frames:
                picked.append(rng.choice(frames))
        return [
            Still(at=t, pdq=pdq(self.still(t, how.hash_side), how.hash_side))
            for t in dict.fromkeys(picked)
        ]


def write_photos(
    src: Path, dest: list[Path], *, preset: str, seed: str, timeout_s: int
) -> list[Photo]:
    how = load_photo_preset(preset)
    recorder = load_device(how.device).recorder
    if recorder is None:
        msg = f"device {how.device} has no recorder profile"
        raise ValueError(msg)
    camera = load_profile(recorder.profile).device
    place = recorder.place.sample(_rng(seed, "place")) if recorder.place else None

    video = _Video.of(src, timeout_s)
    candidates = video.stills(
        len(dest) * how.stills.candidates, how.stills, _rng(seed, "moments")
    )
    chosen = pick(candidates, len(dest))
    shots = _shots(
        len(chosen), ZoneInfo(recorder.time_zone), place, how, _rng(seed, "shots")
    )
    seen = [s.pdq for s in candidates]
    written: list[Photo] = []
    for i, (path, still, shot) in enumerate(zip(dest, chosen, shots, strict=False)):
        made = changed(
            video.still(still.at, how.lens.short_side),
            seen,
            how.retouch,
            how.stills.hash_side,
            _rng(seed, f"retouch/{i}"),
        )
        seen.append(made.pdq)
        exif = exif_for(camera, how.lens, shot, made.picture.size)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(jpeg(made.picture, exif, how.lens.quality))
        written.append(Photo(path=path, at=still.at, crop=made.crop, bits=made.bits))
    return written
