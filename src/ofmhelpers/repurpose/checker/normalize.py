"""Frames made comparable before hashing (the notes' "normalize frames, so
edits get undone"), then the views every frame is hashed in.

`normalize`: borders and padding cropped (edge rows / columns of almost one
grey level), grey + histogram equalization (cancels most colour,
brightness, contrast, gamma and tint edits), a light Gaussian blur (washes
out noise, grain and sharpening), one small square size (stretches and
aspect changes vanish too).

`is_flat`: a frame with no picture to compare (left out on both sides).

`views`: the normalized frame and its centre crop (zoom and shift: a
zoomed-in copy still shares its middle with the source). Stored and queried
alike. `dihedral`: the 8 rotations / flips, the query side only (store one,
query with all 8).
"""

import numpy as np
from PIL import Image, ImageFilter, ImageOps

SIZE = 256
# The centre crop's share of each side (the notes: 70-80%).
CENTRE = 0.75
# An edge row / column whose grey levels spread less than this is padding.
_FLAT_STD = 6.0
# Never more than this share off one side: a dark scene is not a border.
_MAX_CROP = 0.25
# ~3-5 px kernel on a 320 px frame.
_BLUR_RADIUS = 1.5
# A whole frame spread less than this (a fade to black, a plain title card)
# shows no picture: equalizing it would only blow up noise.
_FLAT_FRAME_STD = 8.0

_DIHEDRAL = (
    None,
    Image.Transpose.ROTATE_90,
    Image.Transpose.ROTATE_180,
    Image.Transpose.ROTATE_270,
    Image.Transpose.FLIP_LEFT_RIGHT,
    Image.Transpose.FLIP_TOP_BOTTOM,
    Image.Transpose.TRANSPOSE,
    Image.Transpose.TRANSVERSE,
)


def _flat_run(flat: np.ndarray, limit: int) -> int:
    """How many of the leading entries are flat, at most `limit`."""
    run = 0
    for is_flat in flat[:limit]:
        if not is_flat:
            break
        run += 1
    return run


def content_box(grey: Image.Image) -> tuple[int, int, int, int]:
    """(left, top, right, bottom) inside the borders / padding."""
    pixels = np.asarray(grey, dtype=np.float32)
    height, width = pixels.shape
    rows = pixels.std(axis=1) < _FLAT_STD
    cols = pixels.std(axis=0) < _FLAT_STD
    top = _flat_run(rows, int(height * _MAX_CROP))
    bottom = _flat_run(rows[::-1], int(height * _MAX_CROP))
    left = _flat_run(cols, int(width * _MAX_CROP))
    right = _flat_run(cols[::-1], int(width * _MAX_CROP))
    return left, top, width - right, height - bottom


def is_flat(frame: Image.Image) -> bool:
    pixels = np.asarray(ImageOps.grayscale(frame), dtype=np.float32)
    return float(pixels.std()) < _FLAT_FRAME_STD


def normalize(frame: Image.Image) -> Image.Image:
    grey = ImageOps.grayscale(frame)
    grey = ImageOps.equalize(grey.crop(content_box(grey)))
    grey = grey.filter(ImageFilter.GaussianBlur(_BLUR_RADIUS))
    return grey.resize((SIZE, SIZE), Image.Resampling.BILINEAR)


def centre(frame: Image.Image) -> Image.Image:
    margin = round(SIZE * (1 - CENTRE) / 2)
    box = (margin, margin, SIZE - margin, SIZE - margin)
    return frame.crop(box).resize((SIZE, SIZE), Image.Resampling.BILINEAR)


def views(normalized: Image.Image) -> list[Image.Image]:
    return [normalized, centre(normalized)]


def dihedral(image: Image.Image) -> list[Image.Image]:
    return [image if t is None else image.transpose(t) for t in _DIHEDRAL]
