# Copyright (C) 2026 jpkrajewski

from concurrent.futures import ThreadPoolExecutor
lazy from pathlib import Path

import requests
from pydantic import ValidationError

from ofmhelpers.config import settings
from ofmhelpers.features.viral.media import add_audio, has_audio
from ofmhelpers.features.viral.models import (
    ApifyItemError,
    ApifyReel,
    SavedReel,
    ViralFind,
)
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.apify import (
    ApifyError,
    DatasetItem,
    RunStatus,
    richest,
)
from ofmhelpers.integrations.apify.instagram import ReelScraperInput
from ofmhelpers.log import get_logger
from ofmhelpers.monitoring.telemetry import in_this_trace

logger = get_logger(__name__)

_NOT_FOUND = "not_found"
_RESTRICTED = "restricted profile"
_START_EVENT = "actor-start"
_REEL_EVENT = "reel"


class ViralError(Exception): ...


def reel_input(username: str, scan: int) -> ReelScraperInput:
    return ReelScraperInput(username=[username], results_limit=scan)


def most_played(reels: list[ApifyReel], top: int) -> list[ApifyReel]:
    seen: set[str] = set()
    unique = []
    for reel in reels:
        if reel.video_url and reel.code not in seen:
            seen.add(reel.code)
            unique.append(reel)
    return sorted(unique, key=lambda r: r.plays, reverse=True)[:top]


def parse(username: str, items: list[DatasetItem]) -> list[ApifyReel]:
    reels: list[ApifyReel] = []
    errors: list[ApifyItemError] = []
    for item in items:
        try:
            if item.failed:
                errors.append(item.read(ApifyItemError))
            else:
                reels.append(item.read(ApifyReel))
        except ValidationError:
            logger.warning(
                "viral @%s: unreadable item %s",
                username,
                item,
                exc_info=True,
            )
    if reels:
        return reels
    for error in errors:
        logger.info("viral @%s: %s %s", username, error.error, error.description)
    if any(e.error == _NOT_FOUND for e in errors):
        msg = f"Profile not found: @{username}"
        raise ViralError(msg)
    if any(e.error.strip().casefold() == _RESTRICTED for e in errors):
        msg = (
            f"Age-restricted profile @{username}: Instagram shows it only to a "
            "signed-in account, and the search reads signed out"
        )
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
    dest = folder / f"{reel.code}.mp4"
    _fetch(str(reel.video_url), dest)
    if reel.audio_url and not has_audio(dest):
        audio = folder / f"{reel.code}.audio.mp4"
        _fetch(reel.audio_url, audio)
        add_audio(dest, audio)
        audio.unlink()
    return dest


def _save(reels: list[ApifyReel], folder: Path) -> list[SavedReel]:

    def one(reel: ApifyReel) -> SavedReel | None:
        try:
            return SavedReel(reel=reel, path=_download(reel, folder))
        except OSError, RuntimeError, requests.RequestException:
            logger.warning("viral: reel %s did not download", reel.code, exc_info=True)
            return None

    with ThreadPoolExecutor(settings.viral.download_workers) as pool:
        saved = [s for s in pool.map(in_this_trace(one), reels) if s is not None]
    if reels and not saved:
        msg = "Could not download the reels from Instagram: try again"
        raise ViralError(msg)
    return saved


def find_viral(username: str, folder: Path) -> ViralFind:
    config = settings.viral
    client = richest(config.api_keys)
    run = client.run(
        config.actor,
        reel_input(username, config.scan),
        config.run_timeout_s,
    )
    if run.status != RunStatus.SUCCEEDED:
        msg = f"Apify run {run.status}: {run.status_message or 'no reason given'}"
        raise ApifyError(msg)
    reels = parse(username, client.items(run))
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
