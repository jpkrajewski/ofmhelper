"""Matching on sequences, not single frames (the notes): speed changes and
trims break frame-by-frame alignment, so the copy's matched frames are fit
to one line `source_t = scale x copy_t + offset`, scale 0.8..1.25. The copy
matches when enough of its frames sit on that line in order, not all.

A Hough vote: per scale, every matched (copy frame, source frame) pair
votes for its offset, binned one sample wide (twice, half a bin apart, so
a line on a bin edge still collects its votes); a copy frame votes once
per bin. The bin with the most copy frames wins.
"""

import numpy as np
from pydantic import BaseModel

# Source seconds per copy second: speed changes up to 25% either way, the
# ones nearest 1 first (a tie keeps the plainer line).
_SCALES = sorted(np.round(np.arange(0.80, 1.2501, 0.01), 2), key=lambda s: abs(s - 1))


class SequenceMatch(BaseModel, frozen=True):
    # Share of the copy's frames on the best line.
    share: float
    scale: float
    offset_s: float


def best_line(
    matched: np.ndarray, copy_t: np.ndarray, source_t: np.ndarray, sample_s: float
) -> SequenceMatch:
    """`matched[i, j]`: copy frame i (at `copy_t[i]` s) shows source frame
    j (at `source_t[j]` s)."""
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
            # One vote per copy frame per bin (rows < copies, so // undoes it).
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
