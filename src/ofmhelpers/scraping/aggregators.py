"""The one networked step of the OnlyFans check: fetch a link-aggregator
page (linktr.ee, beacons.ai, ...) and see whether it points at OnlyFans.

Why it is worth a request at all: Instagram has long throttled reach on
bios that link onlyfans.com directly, so most creators in this niche put
an aggregator in the link field instead. Skipping this hop drops the
majority of real leads to a LIKELY guess.

Reads only the public page any visitor gets -- no login, no API, no
private endpoint. Bounded on every axis (timeout, response size, one
attempt, a pause between calls) because these are small third-party sites
being walked once per candidate, not an API with a quota to spend.

Fault-isolated like the rest of scraping/: an aggregator that is down,
slow, JS-only or has changed its markup returns None, and the caller
records the profile as LIKELY rather than losing the run.
"""

import time

import requests

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.scraping.of_links import find_direct_of_url

logger = get_logger(__name__)

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def _fetch_page(url: str, timeout_s: float, max_bytes: int) -> str | None:
    """The page body, truncated to max_bytes. None on any failure.

    Streamed rather than read whole: an aggregator page is normally a few
    hundred KB, but nothing stops one embedding a video, and a lead hunt
    must not stall pulling something it will only regex."""
    try:
        with requests.get(
            url,
            timeout=timeout_s,
            headers={"User-Agent": _USER_AGENT},
            stream=True,
        ) as response:
            response.raise_for_status()
            return response.raw.read(max_bytes, decode_content=True).decode(
                response.encoding or "utf-8", errors="replace"
            )
    except Exception:
        logger.info("aggregator fetch failed url=%s", url, exc_info=True)
        return None


def resolve_onlyfans_url(url: str) -> str | None:
    """The OnlyFans URL an aggregator page links to, or None if the page
    could not be read or does not mention one."""
    cfg = settings.lead_hunt
    body = _fetch_page(url, cfg.aggregator_timeout_s, cfg.aggregator_max_bytes)
    if body is None:
        return None
    return find_direct_of_url(body)


def resolve_first(urls: list[str]) -> tuple[str | None, str | None]:
    """Walks the candidate aggregator links until one yields an OnlyFans URL.

    Returns `(onlyfans_url, aggregator_url)` -- the aggregator that produced
    it is kept so the exported row can say where the answer came from.
    `(None, None)` when nothing resolved."""
    delay_s = settings.lead_hunt.aggregator_delay_s
    for index, url in enumerate(urls):
        if index:
            time.sleep(delay_s)
        found = resolve_onlyfans_url(url)
        if found:
            return found, url
    return None, None
