"""Stills of a video written as one iPhone's photos, from a JSON photo preset
(`<name>.json` here or any `.json` path, `models.PhotoPreset`; a new target
-- Instagram, Reddit -- is a new file, no code): JPEGs carrying the EXIF its
camera writes (`exif.py`: make, model, iOS, lens, exposure, when, where) for
the preset's device (`repurpose/devices/<name>.json`: recorder profile, time
zone, place), each a different picture from the video's sampled stills and
from the pictures before it (`distinct.py`). HDR (an iPhone's HLG) is turned
to SDR first, as a JPEG holds SDR.

The input is a video: stills are real frames (`video_probe.frame_times`),
so none is asked for past the last one. A photo as the input would be a
second source next to `_Video` (`still` + `stills`), not a new preset.

Every random draw comes from its own stream of the seed (`_rng`): the same
seed gives the same set, and a change in one part (more candidates, another
try) leaves the others' draws alone.

- `threads` -- /threads (owner 2026-11-17): 10 stills of a reel as an
  iPhone 16 Pro Max's front-camera photos, 2268x4032."""

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


class StillError(RuntimeError):
    """ffmpeg gave no still at a moment (it ran, it wrote nothing)."""


def _rng(seed: str, purpose: str) -> random.Random:
    return random.Random(f"{seed}/{purpose}")  # noqa: S311 - not cryptography


def _shots(
    count: int,
    tz: ZoneInfo,
    place: PlaceSample | None,
    preset: PhotoPreset,
    rng: random.Random,
) -> list[Shot]:
    """`count` shots ending now, `gap_s` apart, from one spot."""
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
    """A video stills are cut from, turned to SDR on the way when HDR."""

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
        """`count` stills spread over the video, each a real frame drawn
        inside its own slice (owner 2026-11-17: "timestamps with jitter so
        every image comes different": another seed, other frames); hashed at
        `how.hash_side`. Fewer when the video has fewer frames."""
        # Never the last frame: `trim.frame` seeks to the moment rounded to
        # 4 decimals, which can land past it (0.96666 -> 0.9667: no still).
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
    """Up to one photo per path in `dest`, each a different picture of
    `src`, written as the photo `preset` describes; what was written, in
    time order. Fewer when the video has fewer different pictures. `seed`
    makes the same set again from the same video."""
    how = load_photo_preset(preset)
    recorder = load_device(how.device).recorder
    if recorder is None:  # refused when the preset is loaded (models)
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
