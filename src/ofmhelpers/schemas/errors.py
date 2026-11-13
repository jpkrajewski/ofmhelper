"""A failed job's error as a card shows it: who refused, the short reason
and what they said, all on the card itself -- "APIYI: Flagged content,
[DataInspectionFailed] Green net check rejected image (input)"
(`error_line`). Not behind a hover: Safari shows no `title` tooltip, and a
bare "Flagged content" or "No credits" sent the owner looking in the wrong
place (2026-11-11). `error_reason` is the short reason alone, for code that
branches on the kind of failure.

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
# "<who>: <what they said>": every provider error starts with its label
# ("APIYI: ...", "kie.ai: ...", `integrations/ai_providers/errors.py`).
_WHO = re.compile(r"^([A-Za-z][\w.\- ]{1,23}): (.+)$", re.DOTALL)
# The most of a provider's own words a card carries.
_SAID_MAX = 240


class ErrorReason(BaseModel):
    label: str
    # Matched case-insensitively against the raw error text.
    pattern: str


REASONS = (
    # /clone-links runs (features/clone_links/run.py), before anything is paid.
    ErrorReason(
        label="Could not download",
        pattern=r"^(could not download|no video at this link|the video could not"
        r"|the video cannot)",
    ),
    ErrorReason(label="Needs login", pattern=r"^needs login"),
    # A downloader's own words (yt-dlp, gallery-dl; prod 2026-11-12, job
    # 65a6bb46). They cannot tell a deleted post from a banned or private
    # profile, so the label names them together. Before "Rate limited":
    # Instagram's login redirect speaks of its "rate-limit".
    ErrorReason(
        label="Post deleted or profile banned",
        pattern=r"empty media response|video unavailable"
        r"|content isn.t available|has been (removed|deleted|terminated)",
    ),
    ErrorReason(label="Needs login", pattern=r"redirect(ed)? to (the )?login page"),
    ErrorReason(label="Too long", pattern=r"^too long"),
    ErrorReason(label="Stopped", pattern=r"^stopped\b"),
    ErrorReason(label="Export failed", pattern=r"^export failed"),
    # /viral searches (ofmhelpers.features.viral).
    ErrorReason(label="Profile not found", pattern=r"^profile not found"),
    ErrorReason(label="No reels", pattern=r"^no reels"),
    ErrorReason(label="No Apify credit", pattern=r"^no apify credit"),
    # The provider could not fetch our reference link (APIYI: nothing made,
    # nothing charged, already sent again a few times by the runner).
    ErrorReason(label="Chinese server is slow", pattern=r"chinese server is slow"),
    # Paid and still being made: recovery finishes it (integrations/ai_providers/runner).
    ErrorReason(label="Still generating", pattern=r"still generating"),
    ErrorReason(
        label="No credits",
        pattern=r"credits? insufficient|insufficient (credit|balance)"
        r"|credit balance is too low|not.enough.wallet|insufficient.funds|\b402\b",
    ),
    ErrorReason(
        label="Wrong API key",
        pattern=r"\b401\b|unauthori[sz]ed|invalid (api )?key|api key",
    ),
    ErrorReason(
        label="Flagged content",
        pattern=r"nsfw|sensitive|flagged|content polic|moderat|prohibited"
        r"|content verification"
        # Alibaba's moderation codes as APIYI cites them in a reason
        # ("[DataInspectionFailed] ..."): what jobs stored before the
        # APIYI reader read them (prod 2026-11-07, job 1881dd9b).
        r"|\[datainspectionfailed\]|\[ipinfringementsuspect\]",
    ),
    ErrorReason(
        label="Rate limited",
        pattern=r"\b429\b|rate.?limit|too many requests|queue.maxed|instance.maxed"
        r"|concurrency limit",
    ),
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


def error_line(error: str | None) -> str:
    """The failure as a card shows it: "<Provider>: <Reason>, <what the
    provider said>". One of our own messages already opens with its reason
    ("Too long: ...", "Could not download ..."): shown as it is."""
    if not error:
        return FALLBACK
    reason = error_reason(error)
    text = " ".join(error.split())
    found = _WHO.match(text)
    who, said = (found.group(1), found.group(2)) if found else ("", text)
    if who.lower() == reason.lower():
        # Ours, "Too long: ...": the reason is its own first words.
        who, said = "", text
    line = _reasoned(reason, said)
    return _capped(f"{who}: {line}" if who else line)


def _reasoned(reason: str, said: str) -> str:
    """`said` led by `reason`, unless it already says it: at its start
    (spelled as the reason then), or somewhere in it (left as it is)."""
    if said.lower().startswith(reason.lower()):
        return reason + said[len(reason) :]
    if reason.lower() in said.lower():
        return said
    return f"{reason}, {said}"


def _capped(text: str) -> str:
    return text if len(text) <= _SAID_MAX else f"{text[: _SAID_MAX - 1].rstrip()}…"
