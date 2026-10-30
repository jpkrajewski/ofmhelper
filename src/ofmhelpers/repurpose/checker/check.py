"""`check(source, copy)`: how much a copy still looks and sounds like its
source, tier 1 (hashes). A measurement only: nothing is redone."""

from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image
from pydantic import BaseModel

from ofmhelpers.repurpose.checker import audio, frames, sscd, tmk, visual
from ofmhelpers.repurpose.checker.audio import AudioScore
from ofmhelpers.repurpose.checker.normalize import is_flat, normalize
from ofmhelpers.repurpose.checker.sscd import DescriptorScore
from ofmhelpers.repurpose.checker.tmk import TmkScore
from ofmhelpers.repurpose.checker.visual import HashKind, HashScore
from ofmhelpers.repurpose.probe import probe

_DEFAULT_FPS = 1.0
# The source (the stored side) is sampled denser than the copy: a copy
# trimmed or sped up by a fraction of a second still finds a source frame
# within 1/this s, as a real detector's dense index would (2026-10-26: both
# at 1/s, a 0.1 s trim alone dropped PDQ / dHash to 0-17%).
_SOURCE_FPS = 10.0
_DEFAULT_TIMEOUT_S = 600
# A method "catches" the copy when this share of it finds the source:
# frames in order (`sequence.best_line`) for a picture hash, ~2 s windows
# for Chromaprint. Looks like the source = any hash catches it.
CAUGHT_SHARE = 0.5


class CheckReport(BaseModel, frozen=True):
    source_file: Path
    copy_file: Path
    source_frames: int
    copy_frames: int
    # Sampled copy frames left out: no picture (`normalize.is_flat`).
    flat_copy_frames: int
    hashes: list[HashScore]
    # Neural descriptors (SSCD), where the model is installed (the lab).
    descriptors: list[DescriptorScore] = []
    # The whole-video TMK+PDQF score, where its tools are installed (the lab).
    tmk: TmkScore | None = None
    # None: one of the two has no sound.
    audio: AudioScore | None
    looks_like_source: bool
    sounds_like_source: bool | None

    @property
    def unique(self) -> bool:
        """Neither the picture nor the sound gives the source away."""
        return not self.looks_like_source and not self.sounds_like_source


def _usable(sampled: frames.Sampled) -> tuple[list[Image.Image], np.ndarray]:
    """The frames with a picture, normalized, and their times (s). All of
    them when none has one."""
    images = sampled.images
    picked = [i for i, f in enumerate(images) if not is_flat(f)] or list(
        range(len(images))
    )
    times = np.array([sampled.times[i] for i in picked])
    return [normalize(images[i]) for i in picked], times


@lru_cache(maxsize=32)
def _source(
    path: str, mtime: float, fps: float, timeout_s: int
) -> tuple[dict[HashKind, np.ndarray], np.ndarray, int]:
    """A source's stored hashes per kind, frame times and sampled count, kept
    per process: one source is checked against many copies (the lab, a
    job's N copies). `mtime` is part of the key: an edited file is new."""
    del mtime
    sampled = frames.sample(Path(path), fps, timeout_s)
    images, times = _usable(sampled)
    return (
        {kind: visual.stored(kind, images) for kind in HashKind},
        times,
        len(sampled.images),
    )


def check(
    source: str | Path,
    copy: str | Path,
    *,
    fps: float = _DEFAULT_FPS,
    source_fps: float = _SOURCE_FPS,
    timeout_s: int = _DEFAULT_TIMEOUT_S,
) -> CheckReport:
    """`fps`: copy frames per second checked; `source_fps`: source frames
    per second looked up among."""
    source, copy = Path(source), Path(copy)
    kept, source_t, source_count = _source(
        str(source), source.stat().st_mtime, source_fps, timeout_s
    )
    copy_sampled = frames.sample(copy, fps, timeout_s)
    copy_frames, copy_t = _usable(copy_sampled)
    hashes = [
        visual.score(kind, kept[kind], source_t, copy_frames, copy_t, 1 / fps)
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
        source_frames=source_count,
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
