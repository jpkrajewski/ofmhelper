"""Values in preset and device JSON, drawn once per run:

- a number: used as is;
- [lo, hi]: uniform between the two (a symmetric range like [-1, 1] covers
  both directions);
- a list of words: one of them, picked;
- a chance: 0..1, true that often.
"""

import random

Span = tuple[float, float]
Value = float | Span


def draw(value: Value | None, rng: random.Random) -> float | None:
    if value is None:
        return None
    if isinstance(value, tuple):
        return rng.uniform(*value)
    return float(value)


def draw_int(value: Value | None, rng: random.Random) -> int | None:
    drawn = draw(value, rng)
    return None if drawn is None else round(drawn)


def pick(options: list[str] | str | None, rng: random.Random) -> str | None:
    if options is None or isinstance(options, str):
        return options
    return rng.choice(options) if options else None


def chance(probability: float | None, rng: random.Random) -> bool:
    return probability is not None and rng.random() < probability


def even(v: float) -> int:
    """Nearest even integer, at least 2 (yuv420p needs even sides)."""
    return max(2, round(v / 2) * 2)
