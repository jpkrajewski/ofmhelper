# Copyright (C) 2026 jpkrajewski

from functools import lru_cache
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict
lazy from PIL import Image

from ofmhelpers.media.repurpose.checker import audio, frames, sscd, tmk, visual
from ofmhelpers.media.repurpose.checker.normalize import is_flat, normalize
from ofmhelpers.media.repurpose.checker.schema import (
    AudioScore,
    CheckReport,
    HashKind,
)
from ofmhelpers.media.repurpose.probe import probe

_DEFAULT_FPS = 1.0
_SOURCE_FPS = 10.0
_DEFAULT_TIMEOUT_S = 600
CAUGHT_SHARE = 0.5


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
        range(len(images)),
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
            kind,
            stored.kept[kind],
            stored.times,
            copy_frames,
            copy_t,
            1 / fps,
        )
        for kind in HashKind
    ]
    found = (
        [sscd.score(source, source_fps, copy_sampled, 1 / fps, timeout_s)]
        if sscd.available()
        else []
    )
    whole = tmk.compare(source, copy, timeout_s) if tmk.available() else None
    sound = _sound(source, copy, timeout_s)
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
        sounds_like_source=_sounds_like_source(sound),
    )


def _sound(source: Path, copy: Path, timeout_s: int) -> AudioScore | None:
    if not probe(source, timeout_s).has_audio:
        return None
    if not probe(copy, timeout_s).has_audio:
        return None
    return audio.compare(
        _source_sound(str(source), source.stat().st_mtime, timeout_s),
        audio.fingerprint(copy, timeout_s),
    )


@lru_cache(maxsize=32)
def _source_sound(path: str, mtime: float, timeout_s: int) -> list[int]:
    del mtime
    return audio.fingerprint(Path(path), timeout_s)


def _sounds_like_source(sound: AudioScore | None) -> bool | None:
    return sound.matched_share >= CAUGHT_SHARE if sound else None


def check_sound_again(
    picture: CheckReport,
    *,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> CheckReport:
    sound = _sound(picture.source_file, picture.copy_file, timeout_s)
    return CheckReport(
        source_file=picture.source_file,
        copy_file=picture.copy_file,
        source_frames=picture.source_frames,
        copy_frames=picture.copy_frames,
        flat_copy_frames=picture.flat_copy_frames,
        hashes=picture.hashes,
        descriptors=picture.descriptors,
        tmk=picture.tmk,
        audio=sound,
        looks_like_source=picture.looks_like_source,
        sounds_like_source=_sounds_like_source(sound),
    )
