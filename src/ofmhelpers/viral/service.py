"""One profile's most played reels: Apify reads its newest reels with their
play counts, the top ones are downloaded from Instagram's CDN."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import requests
from pydantic import ValidationError

from ofmhelpers.apify import ApifyError, RunStatus, richest
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound
from ofmhelpers.viral.media import add_audio, has_audio
from ofmhelpers.viral.models import ApifyItemError, ApifyReel, SavedReel, ViralFind

if TYPE_CHECKING:
    from pathlib import Path

logger = get_logger(__name__)

# The actor's error code for a profile that does not exist; anything else
# ("no_items") is a private, empty or age-restricted one.
_NOT_FOUND = "not_found"
# What the actor charges for: one event per run, one per reel it stores.
_START_EVENT = "actor-start"
_REEL_EVENT = "reel"


class ViralError(Exception):
    """A search that cannot give reels; the message is the card's text
    (`web/schemas/errors.py` matches its start)."""


def reel_input(username: str, scan: int) -> dict[str, Any]:
    """The reel scraper's input: `username`'s newest `scan` reels (pinned
    ones too: often the most played), every paid add-on off. Always with a
    limit: without one the actor reads the whole profile."""
    return {
        "username": [username],
        "resultsLimit": scan,
        "skipPinnedPosts": False,
        "includeSharesCount": False,
        "includeTranscript": False,
        "includeDownloadedVideo": False,
    }


def most_played(reels: list[ApifyReel], top: int) -> list[ApifyReel]:
    """The `top` most played reels that have a video, each once."""
    seen: set[str] = set()
    unique = []
    for reel in reels:
        if reel.video_url and reel.code not in seen:
            seen.add(reel.code)
            unique.append(reel)
    return sorted(unique, key=lambda r: r.plays, reverse=True)[:top]


def parse(username: str, items: list[dict[str, Any]]) -> list[ApifyReel]:
    """The dataset's reels; a profile the actor could not read is a
    `ViralError` naming why."""
    reels: list[ApifyReel] = []
    errors: list[ApifyItemError] = []
    for item in items:
        try:
            if "error" in item:
                errors.append(ApifyItemError.model_validate(item))
            else:
                reels.append(ApifyReel.model_validate(item))
        except ValidationError:
            logger.warning(
                "viral @%s: unreadable item %s", username, item, exc_info=True
            )
    if reels:
        return reels
    for error in errors:
        logger.info("viral @%s: %s %s", username, error.error, error.description)
    if any(e.error == _NOT_FOUND for e in errors):
        msg = f"Profile not found: @{username}"
        raise ViralError(msg)
    msg = f"No reels on @{username}: private, empty or age-restricted"
    raise ViralError(msg)


def _fetch(url: str, dest: Path) -> None:
    timeout = settings.viral.download_timeout_s
    with outbound.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        with dest.open("wb") as out:
            for chunk in r.iter_content(1 << 20):
                out.write(chunk)


def _download(reel: ApifyReel, folder: Path) -> Path:
    """`reel` as `<code>.mp4` in `folder`, with its sound: a picture-only
    file gets the separately served audio put back in."""
    dest = folder / f"{reel.code}.mp4"
    _fetch(str(reel.video_url), dest)
    if reel.audio_url and not has_audio(dest):
        audio = folder / f"{reel.code}.audio.mp4"
        _fetch(reel.audio_url, audio)
        add_audio(dest, audio)
        audio.unlink()
    return dest


def _save(reels: list[ApifyReel], folder: Path) -> list[SavedReel]:
    """`reels` downloaded into `folder`, in their order; one that fails is
    left out (logged), all failing is an error."""

    def one(reel: ApifyReel) -> SavedReel | None:
        try:
            return SavedReel(reel=reel, path=_download(reel, folder))
        except (OSError, RuntimeError, requests.RequestException):
            logger.warning("viral: reel %s did not download", reel.code, exc_info=True)
            return None

    with ThreadPoolExecutor(settings.viral.download_workers) as pool:
        saved = [s for s in pool.map(one, reels) if s is not None]
    if reels and not saved:
        msg = "Could not download the reels from Instagram: try again"
        raise ViralError(msg)
    return saved


def find_viral(username: str, folder: Path) -> ViralFind:
    """`username`'s most played reels (of its newest `OFM_VIRAL_SCAN`),
    saved in `folder`, most played first."""
    config = settings.viral
    client = richest(config.api_keys)
    run = client.run(
        config.actor, reel_input(username, config.scan), config.run_timeout_s
    )
    if run.status != RunStatus.SUCCEEDED:
        msg = f"Apify run {run.status}: {run.status_message or 'no reason given'}"
        raise ApifyError(msg)
    reels = parse(username, client.items(run))
    # Apify's own counts are still catching up when the run ends: the reels
    # it stored are what it charges for.
    cost = max(run.cost_usd, run.priced({_START_EVENT: 1, _REEL_EVENT: len(reels)}))
    top = most_played(reels, config.top)
    logger.info(
        "viral @%s: %d reels read, top %d from %d to %d plays, $%.4f",
        username,
        len(reels),
        len(top),
        top[0].plays if top else 0,
        top[-1].plays if top else 0,
        cost,
    )
    return ViralFind(reels=_save(top, folder), scanned=len(reels), cost_usd=cost)
