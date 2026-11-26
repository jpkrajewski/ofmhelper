# Copyright (C) 2026 jpkrajewski

import numpy as np
from PIL import Image, ImageFilter, ImageOps
from pydantic import BaseModel

SIZE = 256
CENTRE = 0.75
_FLAT_STD = 6.0
_MAX_CROP = 0.25
_BLUR_RADIUS = 1.5
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
    run = 0
    for is_flat in flat[:limit]:
        if not is_flat:
            break
        run += 1
    return run


class Box(BaseModel, frozen=True):
    left: int
    top: int
    right: int
    bottom: int


def content_box(grey: Image.Image) -> Box:
    pixels = np.asarray(grey, dtype=np.float32)
    height, width = pixels.shape
    rows = pixels.std(axis=1) < _FLAT_STD
    cols = pixels.std(axis=0) < _FLAT_STD
    top = _flat_run(rows, int(height * _MAX_CROP))
    bottom = _flat_run(rows[::-1], int(height * _MAX_CROP))
    left = _flat_run(cols, int(width * _MAX_CROP))
    right = _flat_run(cols[::-1], int(width * _MAX_CROP))
    return Box(left=left, top=top, right=width - right, bottom=height - bottom)


def is_flat(frame: Image.Image) -> bool:
    pixels = np.asarray(ImageOps.grayscale(frame), dtype=np.float32)
    return float(pixels.std()) < _FLAT_FRAME_STD


def normalize(frame: Image.Image) -> Image.Image:
    grey = ImageOps.grayscale(frame)
    box = content_box(grey)
    grey = ImageOps.equalize(grey.crop((box.left, box.top, box.right, box.bottom)))
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
