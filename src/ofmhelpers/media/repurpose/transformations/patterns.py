"""Full-frame pattern images for Special's overlay: Duplyfi's stripes /
grid / check / spiral / particles / psyche SVGs (`generatePatternImage`),
drawn with Pillow at the copy's exact output size. Laid over the video at a
few percent opacity (`video.VideoTransformer._overlay`)."""

import random
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from PIL import Image, ImageDraw


class Pattern(StrEnum):
    STRIPES = "stripes"
    GRID = "grid"
    CHECK = "check"
    SPIRAL = "spiral"
    PARTICLES = "particles"
    PSYCHE = "psyche"


_STRIPE_PERIOD = 12
_STRIPE_WIDTH = 6
_GRID_SPACING = 10
# Duplyfi's grid lines: white at 70% over black.
_GRID_GREY = (179, 179, 179)
_CHECK_SIZE = 16
_SPIRAL_SECTORS = 8
_PARTICLE_COUNT = 300
_PARTICLE_RADIUS = (1.0, 4.0)
_PARTICLE_BACKGROUND = "#050505"
_PARTICLE_COLOURS = ("#4DA3FF", "#FE1648", "#FFB020", "#42A6FE", "#F13BDE", "#880DFB")
_PSYCHE_COLOURS = ("#FE1648", "#F13BDE", "#880DFB", "#42A6FE", "#FFB020")


@dataclass(frozen=True)
class _Canvas:
    """One pattern image being drawn: Pillow's pen and the frame's size."""

    pen: ImageDraw.ImageDraw
    w: int
    h: int

    def stripes(self) -> None:
        w, h = self.w, self.h
        for offset in range(-h, w + h, _STRIPE_PERIOD):
            self.pen.line(
                [(offset, 0), (offset + h, h)], fill="white", width=_STRIPE_WIDTH
            )

    def grid(self) -> None:
        for x in range(0, self.w + 1, _GRID_SPACING):
            self.pen.line([(x, 0), (x, self.h)], fill=_GRID_GREY)
        for y in range(0, self.h + 1, _GRID_SPACING):
            self.pen.line([(0, y), (self.w, y)], fill=_GRID_GREY)

    def check(self) -> None:
        for y in range(0, self.h, _CHECK_SIZE):
            for x in range(0, self.w, _CHECK_SIZE):
                if (x // _CHECK_SIZE + y // _CHECK_SIZE) % 2 == 0:
                    self.pen.rectangle(
                        [x, y, x + _CHECK_SIZE - 1, y + _CHECK_SIZE - 1], fill="white"
                    )

    def sectors(self, fills: list[str | None]) -> None:
        """Pie sectors from the centre past the corners, one per fill (None: skip)."""
        w, h = self.w, self.h
        r = max(w, h)
        box = [w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r]
        step = 360 / len(fills)
        for i, fill in enumerate(fills):
            if fill is not None:
                self.pen.pieslice(box, i * step, (i + 1) * step, fill=fill)

    def particles(self, rng: random.Random) -> None:
        for _ in range(_PARTICLE_COUNT):
            x, y = rng.uniform(0, self.w), rng.uniform(0, self.h)
            r = rng.uniform(*_PARTICLE_RADIUS)
            self.pen.ellipse(
                [x - r, y - r, x + r, y + r], fill=rng.choice(_PARTICLE_COLOURS)
            )


def draw(pattern: Pattern, size: tuple[int, int], rng: random.Random) -> Image.Image:
    w, h = size
    background = _PARTICLE_BACKGROUND if pattern is Pattern.PARTICLES else "black"
    image = Image.new("RGB", size, background)
    canvas = _Canvas(ImageDraw.Draw(image), w, h)
    if pattern is Pattern.STRIPES:
        canvas.stripes()
    elif pattern is Pattern.GRID:
        canvas.grid()
    elif pattern is Pattern.CHECK:
        canvas.check()
    elif pattern is Pattern.SPIRAL:
        canvas.sectors(["white", None] * (_SPIRAL_SECTORS // 2))
    elif pattern is Pattern.PARTICLES:
        canvas.particles(rng)
    else:
        canvas.sectors(list(_PSYCHE_COLOURS))
    return image


def write(pattern: Pattern, size: tuple[int, int], seed: int, dest: Path) -> Path:
    draw(pattern, size, random.Random(seed)).save(dest, "PNG")  # noqa: S311 - art, not crypto
    return dest
