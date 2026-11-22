import random
from enum import StrEnum
lazy from pathlib import Path

from PIL import Image, ImageDraw
from pydantic import BaseModel, SkipValidation

from ofmhelpers.media.repurpose.transformations.constants import PatternConstants


class Pattern(StrEnum):
    STRIPES = "stripes"
    GRID = "grid"
    CHECK = "check"
    SPIRAL = "spiral"
    PARTICLES = "particles"
    PSYCHE = "psyche"


class _Canvas(BaseModel, frozen=True, arbitrary_types_allowed=True):
    pen: SkipValidation[ImageDraw.ImageDraw]
    w: int
    h: int
    c: PatternConstants

    def stripes(self) -> None:
        w, h = self.w, self.h
        for offset in range(-h, w + h, self.c.stripe_period):
            self.pen.line(
                [(offset, 0), (offset + h, h)], fill="white", width=self.c.stripe_width
            )

    def grid(self) -> None:
        for x in range(0, self.w + 1, self.c.grid_spacing):
            self.pen.line([(x, 0), (x, self.h)], fill=self.c.grid_grey)
        for y in range(0, self.h + 1, self.c.grid_spacing):
            self.pen.line([(0, y), (self.w, y)], fill=self.c.grid_grey)

    def check(self) -> None:
        size = self.c.check_size
        for y in range(0, self.h, size):
            for x in range(0, self.w, size):
                if (x // size + y // size) % 2 == 0:
                    self.pen.rectangle([x, y, x + size - 1, y + size - 1], fill="white")

    def sectors(self, fills: list[str | None]) -> None:
        w, h = self.w, self.h
        r = max(w, h)
        box = [w / 2 - r, h / 2 - r, w / 2 + r, h / 2 + r]
        step = 360 / len(fills)
        for i, fill in enumerate(fills):
            if fill is not None:
                self.pen.pieslice(box, i * step, (i + 1) * step, fill=fill)

    def particles(self, rng: random.Random) -> None:
        for _ in range(self.c.particle_count):
            x, y = rng.uniform(0, self.w), rng.uniform(0, self.h)
            r = rng.uniform(*self.c.particle_radius)
            self.pen.ellipse(
                [x - r, y - r, x + r, y + r], fill=rng.choice(self.c.particle_colours)
            )


def draw(
    pattern: Pattern, size: tuple[int, int], rng: random.Random, c: PatternConstants
) -> Image.Image:
    w, h = size
    background = c.particle_background if pattern is Pattern.PARTICLES else "black"
    image = Image.new("RGB", size, background)
    canvas = _Canvas(pen=ImageDraw.Draw(image), w=w, h=h, c=c)
    if pattern is Pattern.STRIPES:
        canvas.stripes()
    elif pattern is Pattern.GRID:
        canvas.grid()
    elif pattern is Pattern.CHECK:
        canvas.check()
    elif pattern is Pattern.SPIRAL:
        canvas.sectors(["white", None] * (c.spiral_sectors // 2))
    elif pattern is Pattern.PARTICLES:
        canvas.particles(rng)
    else:
        canvas.sectors(list(c.psyche_colours))
    return image


def write(
    pattern: Pattern,
    size: tuple[int, int],
    seed: int,
    dest: Path,
    c: PatternConstants,
) -> Path:
    draw(pattern, size, random.Random(seed), c).save(dest, "PNG")  # noqa: S311 - art, not crypto
    return dest
