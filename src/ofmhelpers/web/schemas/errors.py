"""A failed job's error as one short reason a card can show ("No credits",
"Flagged content", ...). The provider is already on the card, and the full
message stays available as the card's hover text, so the reason is all the
card itself needs.

A new kind of failure = one `ErrorReason` in `REASONS`; first match wins."""

import re
from functools import lru_cache

from pydantic import BaseModel

FALLBACK = "Failed"


class ErrorReason(BaseModel):
    label: str
    # Matched case-insensitively against the raw error text.
    pattern: str


REASONS = (
    ErrorReason(
        label="No credits",
        pattern=r"credits? insufficient|insufficient (credit|balance)|\b402\b",
    ),
    ErrorReason(
        label="Wrong API key",
        pattern=r"\b401\b|unauthori[sz]ed|invalid (api )?key|api key",
    ),
    ErrorReason(
        label="Flagged content", pattern=r"nsfw|sensitive|flagged|content polic|moderat"
    ),
    ErrorReason(label="Rate limited", pattern=r"\b429\b|rate.?limit|too many requests"),
    ErrorReason(label="Timed out", pattern=r"time ?out|timed out"),
    ErrorReason(
        label="Input out of range", pattern=r"out of range|must be between|\b422\b"
    ),
    ErrorReason(
        label="Bad input file",
        pattern=r"cannot identify|not supported|unsupported|invalid (input|image|video)",
    ),
    ErrorReason(
        label="Provider error",
        pattern=r"internal (server )?error|try again later|\b50[0-4]\b",
    ),
)


@lru_cache(maxsize=1)
def _compiled() -> tuple[tuple[re.Pattern[str], str], ...]:
    return tuple((re.compile(r.pattern, re.IGNORECASE), r.label) for r in REASONS)


def error_reason(error: str | None) -> str:
    """The short reason for `error`, `FALLBACK` when none matches."""
    if error:
        for pattern, label in _compiled():
            if pattern.search(error):
                return label
    return FALLBACK
