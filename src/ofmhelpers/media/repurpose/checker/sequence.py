import numpy as np
from pydantic import BaseModel

_SCALES = sorted(np.round(np.arange(0.80, 1.2501, 0.01), 2), key=lambda s: abs(s - 1))


class SequenceMatch(BaseModel, frozen=True):
    share: float
    scale: float
    offset_s: float


def best_line(
    matched: np.ndarray, copy_t: np.ndarray, source_t: np.ndarray, sample_s: float
) -> SequenceMatch:
    copies = matched.shape[0]
    rows, cols = np.nonzero(matched)
    if not copies or not len(rows):
        return SequenceMatch(share=0.0, scale=1.0, offset_s=0.0)
    t, s = copy_t[rows], source_t[cols]
    best, best_scale, best_offset = 0, 1.0, 0.0
    for scale in _SCALES:
        offsets = (s - scale * t) / sample_s
        for shift in (0.0, 0.5):
            bins = np.floor(offsets + shift).astype(np.int64)
            votes = np.unique(bins * copies + rows) // copies
            values, counts = np.unique(votes, return_counts=True)
            top = int(counts.argmax())
            if counts[top] > best:
                best = int(counts[top])
                best_scale = float(scale)
                best_offset = float(np.median(offsets[bins == values[top]]) * sample_s)
    return SequenceMatch(
        share=round(best / copies, 3), scale=best_scale, offset_s=round(best_offset, 2)
    )
