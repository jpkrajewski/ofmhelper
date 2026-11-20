from functools import lru_cache
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict
lazy from PIL import Image

from ofmhelpers.media.repurpose.checker import audio, frames, sscd, tmk, visual
from ofmhelpers.media.repurpose.checker.audio import AudioScore
from ofmhelpers.media.repurpose.checker.normalize import is_flat, normalize
from ofmhelpers.media.repurpose.checker.sscd import DescriptorScore
from ofmhelpers.media.repurpose.checker.tmk import TmkScore
from ofmhelpers.media.repurpose.checker.visual import HashKind, HashScore
from ofmhelpers.media.repurpose.probe import probe

_DEFAULT_FPS = 1.0
_SOURCE_FPS = 10.0
_DEFAULT_TIMEOUT_S = 600
CAUGHT_SHARE = 0.5


class CheckReport(BaseModel, frozen=True):
    source_file: Path
    copy_file: Path
    source_frames: int
    copy_frames: int
    flat_copy_frames: int
    hashes: list[HashScore]
    descriptors: list[DescriptorScore] = []
    tmk: TmkScore | None = None
    audio: AudioScore | None
    looks_like_source: bool
    sounds_like_source: bool | None

    @property
    def unique(self) -> bool:
        return not self.looks_like_source and not self.sounds_like_source


class _Usable(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    images: list[Image.Image]
    times: np.ndarray


class _SourceHashes(BaseModel):
    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    kept: dict[HashKind, np.ndarray]
    times: np.ndarray
    count: int


def _usable(sampled: frames.Sampled) -> _Usable:
    images = sampled.images
    picked = [i for i, f in enumerate(images) if not is_flat(f)] or list(
        range(len(images))
    )
    times = np.array([sampled.times[i] for i in picked])
    return _Usable(images=[normalize(images[i]) for i in picked], times=times)


@lru_cache(maxsize=32)
def _source(path: str, mtime: float, fps: float, timeout_s: int) -> _SourceHashes:
    del mtime
    sampled = frames.sample(Path(path), fps, timeout_s)
    usable = _usable(sampled)
    return _SourceHashes(
        kept={kind: visual.stored(kind, usable.images) for kind in HashKind},
        times=usable.times,
        count=len(sampled.images),
    )


def check(
    source: str | Path,
    copy: str | Path,
    *,
    fps: float = _DEFAULT_FPS,
    source_fps: float = _SOURCE_FPS,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> CheckReport:
    source, copy = Path(source), Path(copy)
    stored = _source(str(source), source.stat().st_mtime, source_fps, timeout_s)
    copy_sampled = frames.sample(copy, fps, timeout_s)
    copy_usable = _usable(copy_sampled)
    copy_frames, copy_t = copy_usable.images, copy_usable.times
    hashes = [
        visual.score(
            kind, stored.kept[kind], stored.times, copy_frames, copy_t, 1 / fps
        )
        for kind in HashKind
    ]
    found = (
        [sscd.score(source, source_fps, copy_sampled, 1 / fps, timeout_s)]
        if sscd.available()
        else []
    )
    whole = tmk.compare(source, copy, timeout_s) if tmk.available() else None
    sound = None
    if probe(source, timeout_s).has_audio and probe(copy, timeout_s).has_audio:
        sound = audio.compare(
            audio.fingerprint(source, timeout_s), audio.fingerprint(copy, timeout_s)
        )
    return CheckReport(
        source_file=source,
        copy_file=copy,
        source_frames=stored.count,
        copy_frames=len(copy_sampled.images),
        flat_copy_frames=len(copy_sampled.images) - len(copy_frames),
        hashes=hashes,
        descriptors=found,
        tmk=whole,
        audio=sound,
        looks_like_source=any(h.sequence.share >= CAUGHT_SHARE for h in hashes)
        or any(d.sequence.share >= CAUGHT_SHARE for d in found)
        or bool(whole and whole.matched),
        sounds_like_source=sound.matched_share >= CAUGHT_SHARE if sound else None,
    )
