# Copyright (C) 2026 jpkrajewski

import re
from pathlib import Path
from typing import cast
from urllib.parse import urlparse

import requests
from pydantic import BaseModel
lazy from yt_dlp.extractor.generic import GenericIE

from ofmhelpers.config import settings
from ofmhelpers.integrations import egress
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.log import get_logger

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
    url: str
    height: int


class KvsPage(BaseModel):
    video_id: str
    license_code: str
    sources: list[KvsSource]

    @classmethod
    def parse(cls, html: str) -> KvsPage | None:
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
                KvsSource(url=value, height=int(height.group(1)) if height else 0),
            )
        if not license_code or not sources:
            return None
        return cls(
            video_id=found.get("video_id", "video"),
            license_code=license_code,
            sources=sources,
        )

    def best(self) -> str:
        source = max(self.sources, key=lambda s: s.height)
        if not source.url.startswith("function/"):
            return source.url
        return cast(
            "str",
            GenericIE._kvs_get_real_url(source.url, self.license_code),  # noqa: SLF001
        )


def download(url: str, output_dir: Path | None = None) -> DownloadResult:
    output_dir = output_dir or Path("downloads")
    timeout = settings.downloaders.http_timeout_s
    headers = {"User-Agent": _BROWSER}
    try:
        page = egress.get(url, headers=headers, timeout=timeout)
    except requests.RequestException as exc:
        logger.warning("kvs: page of %s not read", url, exc_info=True)
        return DownloadResult(url=url, success=False, error=f"KVS page: {exc}")
    kvs = KvsPage.parse(page.text)
    if kvs is None:
        return DownloadResult(
            url=url,
            success=False,
            error="Unsupported URL: no KVS player",
        )
    try:
        real = kvs.best()
    except Exception as exc:
        logger.warning("kvs: could not unscramble %s", url, exc_info=True)
        return DownloadResult(url=url, success=False, error=f"KVS address: {exc}")
    site = (urlparse(url).hostname or "site").removeprefix("www.").split(".")[0]
    dest = output_dir / f"{site}_{kvs.video_id}.mp4"
    try:
        _save(real, dest, {**headers, "Referer": url}, timeout)
    except requests.RequestException as exc:
        logger.warning("kvs: video of %s not saved", url, exc_info=True)
        return DownloadResult(url=url, success=False, error=f"KVS video: {exc}")
    logger.info("kvs: %s -> %s (%d bytes)", url, dest.name, dest.stat().st_size)
    return DownloadResult(url=url, success=True, output_paths=[dest])


def _save(url: str, dest: Path, headers: dict[str, str], timeout: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".part")
    try:
        with (
            egress.get(url, headers=headers, timeout=timeout, stream=True) as r,
            tmp.open("wb") as out,
        ):
            for chunk in r.iter_content(_CHUNK):
                out.write(chunk)
        tmp.replace(dest)
    finally:
        tmp.unlink(missing_ok=True)
