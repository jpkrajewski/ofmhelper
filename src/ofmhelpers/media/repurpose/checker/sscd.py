from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel
lazy import torch

from ofmhelpers.config import settings
from ofmhelpers.media.repurpose.checker import frames
from ofmhelpers.media.repurpose.checker.sequence import SequenceMatch, best_line

_SIZE = 288
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_BATCH = 16


class DescriptorScore(BaseModel, frozen=True):
    kind: str = "sscd"
    threshold: float
    nearest: list[float]
    median: float
    matched_share: float
    sequence: SequenceMatch


def threshold() -> float:
    return settings.repurpose.sscd_threshold


def available() -> bool:
    path = settings.repurpose.sscd_model
    if not path or not Path(path).is_file():
        return False
    try:
        import torch  # noqa: F401 - optional, lab only
    except ImportError:  # silent: optional, lab only
        return False
    return True


@lru_cache(maxsize=1)
def _model() -> Any:
    torch.set_num_threads(settings.repurpose.sscd_threads)
    return torch.jit.load(settings.repurpose.sscd_model).eval()


def _batch(images: list[Image.Image]) -> np.ndarray:
    width, height = images[0].size
    scale = _SIZE / min(width, height)
    size = (round(width * scale), round(height * scale))
    pixels = np.stack(
        [
            np.asarray(i.resize(size, Image.Resampling.BICUBIC), dtype=np.float32) / 255
            for i in images
        ]
    )
    return ((pixels - _MEAN) / _STD).transpose(0, 3, 1, 2)


def descriptors(images: list[Image.Image]) -> np.ndarray:
    model = _model()
    out = []
    with torch.no_grad():
        for start in range(0, len(images), _BATCH):
            batch = torch.from_numpy(_batch(images[start : start + _BATCH]))
            out.append(model(batch).numpy())
    found = np.concatenate(out)
    return found / np.linalg.norm(found, axis=1, keepdims=True)


@lru_cache(maxsize=32)
def _source(
    path: str, mtime: float, fps: float, timeout_s: int
) -> tuple[np.ndarray, np.ndarray]:
    del mtime
    sampled = frames.sample(Path(path), fps, timeout_s)
    return descriptors(sampled.images), np.array(sampled.times)


def score(
    source: Path,
    source_fps: float,
    copy: frames.Sampled,
    sample_s: float,
    timeout_s: int,
) -> DescriptorScore:
    stored, stored_t = _source(
        str(source), source.stat().st_mtime, source_fps, timeout_s
    )
    plain = descriptors(copy.images)
    mirrored = descriptors([ImageOps.mirror(i) for i in copy.images])
    similarity = np.maximum(plain @ stored.T, mirrored @ stored.T)
    cut = threshold()
    nearest = [round(float(s), 3) for s in similarity.max(axis=1)]
    return DescriptorScore(
        threshold=cut,
        nearest=nearest,
        median=round(float(np.median(nearest)), 3),
        matched_share=round(sum(s >= cut for s in nearest) / len(nearest), 3),
        sequence=best_line(similarity >= cut, np.array(copy.times), stored_t, sample_s),
    )
