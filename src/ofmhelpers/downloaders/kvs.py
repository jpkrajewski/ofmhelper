"""Videos from KVS (Kernel Video Sharing) sites -- gayhub.com and the many
tube sites built on it.

Their player data (`flashvars`) lists the video in several qualities
(`video_url`, `video_alt_url`, `video_alt_url2`, ... with `*_text` = "480p",
"720p", "1080p"), each address scrambled ("function/0/https://.../get_file/
1/<hash>/...") against the page's `license_code`. yt-dlp can unscramble it
(`GenericIE._kvs_get_real_url`) but its generic extractor takes the page's
plain `<source>` tag first and saves the HTML it gets back as an .mp4
(2026-10-23: a 268 KB "video" from gayhub.com). This reads the player data,
unscrambles the best quality with yt-dlp's own code and downloads it with
the page as Referer.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import requests
from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.downloaders.models import DownloadResult
from ofmhelpers.log import get_logger
from ofmhelpers.utils import outbound

logger = get_logger(__name__)

_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_VAR = re.compile(r"""\b(\w+)\s*:\s*['"]([^'"]*)['"]""")
_URL_KEY = re.compile(r"^video_(?:alt_)?url\d*$")
_HEIGHT = re.compile(r"(\d{3,4})p")
_CHUNK = 1 << 20


class KvsSource(BaseModel):
    """One quality the player offers."""

    url: str
    height: int


class KvsPage(BaseModel):
    """What a KVS page's player data says."""

    video_id: str
    license_code: str
    sources: list[KvsSource]

    @classmethod
    def parse(cls, html: str) -> KvsPage | None:
        """None when the page has no KVS player."""
        if "license_code" not in html or "flashvars" not in html:
            return None
        found = dict(_VAR.findall(html))
        license_code = found.get("license_code", "")
        sources = []
        for key, value in found.items():
            if not _URL_KEY.match(key) or not value:
                continue
            height = _HEIGHT.search(found.get(f"{key}_text", "") or value)
            sources.append(
                KvsSource(url=value, height=int(height.group(1)) if height else 0)
            )
        if not license_code or not sources:
            return None
        return cls(
            video_id=found.get("video_id", "video"),
            license_code=license_code,
            sources=sources,
        )

    def best(self) -> str:
        """The highest quality's real address."""
        from yt_dlp.extractor.generic import GenericIE

        source = max(self.sources, key=lambda s: s.height)
        if not source.url.startswith("function/"):
            return source.url
        return GenericIE._kvs_get_real_url(source.url, self.license_code)  # noqa: SLF001 -- yt-dlp's own unscrambler


def download(url: str, output_dir: Path | None = None) -> DownloadResult:
    """`url`'s video at its best quality, or why not."""
    output_dir = output_dir or Path("downloads")
    timeout = settings.downloaders.http_timeout_s
    headers = {"User-Agent": _BROWSER}
    try:
        page = outbound.get(url, headers=headers, timeout=timeout)
        page.raise_for_status()
    except requests.RequestException as exc:
        return DownloadResult(url=url, success=False, error=f"KVS page: {exc}")
    kvs = KvsPage.parse(page.text)
    if kvs is None:
        return DownloadResult(
            url=url, success=False, error="Unsupported URL: no KVS player"
        )
    try:
        real = kvs.best()
    except Exception as exc:  # yt-dlp's private helper changed or refused
        logger.warning("kvs: could not unscramble %s", url, exc_info=True)
        return DownloadResult(url=url, success=False, error=f"KVS address: {exc}")
    site = (urlparse(url).hostname or "site").removeprefix("www.").split(".")[0]
    dest = output_dir / f"{site}_{kvs.video_id}.mp4"
    try:
        _save(real, dest, {**headers, "Referer": url}, timeout)
    except requests.RequestException as exc:
        return DownloadResult(url=url, success=False, error=f"KVS video: {exc}")
    logger.info("kvs: %s -> %s (%d bytes)", url, dest.name, dest.stat().st_size)
    return DownloadResult(url=url, success=True, output_paths=[dest])


def _save(url: str, dest: Path, headers: dict[str, str], timeout: int) -> None:
    """Streamed to a temp name, renamed when complete."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with outbound.get(url, headers=headers, timeout=timeout, stream=True) as r:
            r.raise_for_status()
            with tmp.open("wb") as out:
                for chunk in r.iter_content(_CHUNK):
                    out.write(chunk)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
