"""SSCD -- Meta AI's Self-Supervised Copy Detection
(github.com/facebookresearch/sscd-copy-detection; first on the DISC2021
benchmark): a neural descriptor per frame (512 numbers, unit length); two
frames are copies when their cosine similarity is at least the threshold.
Like the hashes: the copy's frames are matched to the source's, then fit to
one time line (`sequence.best_line`).

Optional: needs torch and the TorchScript model in `OFM_SSCD_MODEL` (e.g.
`sscd_disc_mixup.torchscript.pt`); `available()` says whether it runs here.
Not in the app image (torch is ~1 GB); the lab pod installs it.

Frames go in as sampled, in colour (no grey / equalize: the model was
trained to see through edits), the copy's also mirrored. A source's
descriptors are kept per process (`_source`): the lab checks hundreds of
copies of the same few sources.
"""

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image, ImageOps
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.repurpose.checker import frames
from ofmhelpers.repurpose.checker.sequence import SequenceMatch, best_line

# The model's evaluation size (short side) and ImageNet normalization.
_SIZE = 288
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_BATCH = 16


class DescriptorScore(BaseModel, frozen=True):
    kind: str = "sscd"
    threshold: float
    # Per copy frame: cosine to the closest source frame (mirror included).
    nearest: list[float]
    median: float
    matched_share: float
    sequence: SequenceMatch


def threshold() -> float:
    """Cosine at or above which two frames are the same picture."""
    return settings.repurpose.sscd_threshold


def available() -> bool:
    path = settings.repurpose.sscd_model
    if not path or not Path(path).is_file():
        return False
    try:
        import torch  # noqa: F401 - optional, lab only
    except ImportError:
        return False
    return True


@lru_cache(maxsize=1)
def _model() -> Any:
    import torch

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
    """(frames, 512), unit length."""
    import torch

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
    del mtime  # part of the key: an edited file is sampled again
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
