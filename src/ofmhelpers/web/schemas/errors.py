"""A failed job's error as one short reason a card can show ("No credits",
"Flagged content", ...). The provider is already on the card, and the full
message stays available as the card's hover text, so the reason is all the
card itself needs.

A new kind of failure = one `ErrorReason` in `REASONS`; first match wins.
Our own fixed messages come first, anchored at the start (a /clone-links
run that never reached the provider: "Could not download ...", "Too long
...", "Stopped"), so a downloader's text behind them -- Instagram's
"rate-limit", a site's "401 Unauthorized" -- never reads as the AI
provider's "Rate limited" or "Wrong API key"."""

import re
from functools import lru_cache

from pydantic import BaseModel

FALLBACK = "Failed"


class ErrorReason(BaseModel):
    label: str
    # Matched case-insensitively against the raw error text.
    pattern: str


REASONS = (
    # /clone-links runs (web/clone_links/run.py), before anything is paid.
    ErrorReason(
        label="Could not download",
        pattern=r"^(could not download|no video at this link|the video could not"
        r"|the video cannot)",
    ),
    ErrorReason(label="Needs login", pattern=r"^needs login"),
    ErrorReason(label="Too long", pattern=r"^too long"),
    ErrorReason(label="Stopped", pattern=r"^stopped\b"),
    ErrorReason(label="Export failed", pattern=r"^export failed"),
    # Paid and still being made: recovery finishes it (aigenproviders/runner).
    ErrorReason(label="Still generating", pattern=r"still generating"),
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
