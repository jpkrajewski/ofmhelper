import json
import re
from pathlib import Path
from urllib.parse import unquote, urlsplit

import requests

from ofmhelpers.config import settings
from ofmhelpers.integrations import outbound
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.integrations.downloaders.sites import saver_supported
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_BROWSER = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0 Safari/537.36"
)
_CHUNK = 1 << 20

_SAVERS = (
    ("saveinta", "https://saveinta.com"),
    ("snapreels", "https://snapreels.net"),
)

_DL_HOST = re.compile(r"^https://dl\.snapcdn\.app/", re.IGNORECASE)
_TOKEN = re.compile(r'k_exp="(\d+)".*?k_token="([a-f0-9]+)"', re.DOTALL)
_CONTENT_EXT = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "audio/mpeg": ".mp3",
}


def download(url: str, output_dir: Path, *, single: bool = False) -> DownloadResult:
    if not saver_supported(url):
        return DownloadResult(
            url=url, success=False, error="Unsupported URL: not an Instagram post"
        )
    limit = settings.downloaders.single_post_items if single else None
    errors: list[str] = []
    for name, base in _SAVERS:
        try:
            paths = _fetch(url, base, output_dir, limit)
        except Exception as exc:
            logger.warning("saver %s failed for %s", name, url, exc_info=True)
            errors.append(f"{name}: {exc}")
            continue
        if paths:
            logger.info("saver %s: %s -> %d file(s)", name, url, len(paths))
            return DownloadResult(url=url, success=True, output_paths=paths)
        errors.append(f"{name}: no files")
    return DownloadResult(
        url=url, success=False, error="saver sites: " + " | ".join(errors)
    )


def _fetch(url: str, base: str, out: Path, limit: int | None) -> list[Path]:
    timeout = settings.downloaders.http_timeout_s
    headers = {"User-Agent": _BROWSER}
    page = outbound.get(f"{base}/en", headers=headers, timeout=timeout)
    page.raise_for_status()
    token = _TOKEN.search(page.text)
    if token is None:
        return []
    answer = outbound.post(
        f"{base}/api/ajaxSearch",
        data={
            "k_exp": token.group(1),
            "k_token": token.group(2),
            "q": url,
            "t": "media",
            "lang": "en",
            "v": "v2",
            "cftoken": "",
            "html": "",
        },
        headers={**headers, "Referer": f"{base}/en"},
        timeout=timeout,
    )
    answer.raise_for_status()
    body = json.loads(answer.text)
    if body.get("status") != "ok":
        return []
    links = _links(body.get("data", ""))
    if not links:
        return []
    out.mkdir(parents=True, exist_ok=True)
    stem = _stem(url)
    paths: list[Path] = []
    for i, link in enumerate(links, start=1):
        if limit is not None and len(paths) >= limit:
            break
        path = _save(link, out / f"{stem}_{i}", headers, timeout)
        if path is not None:
            paths.append(path)
    return paths


def _links(data: str) -> list[str]:
    if "dl.snapcdn.app" not in data:
        data = _unpack(data)
    found = re.findall(r'href=\\?"([^"\\]+)', data)
    links = []
    for raw in found:
        link = raw.replace("\\/", "/")
        if _DL_HOST.match(link) and link not in links:
            links.append(link)
    return links


def _stem(url: str) -> str:
    segments = [s for s in urlsplit(url).path.split("/") if s]
    return segments[-1] if segments else "post"


def _save(url: str, dest: Path, headers: dict[str, str], timeout: int) -> Path | None:
    tmp = dest.with_suffix(".part")
    try:
        with outbound.get(url, headers=headers, timeout=timeout, stream=True) as r:
            r.raise_for_status()
            kind = (r.headers.get("content-type") or "").split(";")[0].strip().lower()
            if kind.startswith("text/") or kind == "application/json":
                logger.warning("saver link %s answered %s, not media", url, kind)
                return None
            ext = _CONTENT_EXT.get(kind) or Path(urlsplit(url).path).suffix or ".bin"
            with tmp.open("wb") as fh:
                for chunk in r.iter_content(_CHUNK):
                    fh.write(chunk)
    except requests.RequestException:
        tmp.unlink(missing_ok=True)
        raise
    final = dest.with_suffix(ext)
    tmp.replace(final)
    return final


_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ+/"


def _unpack(data: str) -> str:
    match = re.search(r"\}\((.*)\)\);?\s*$", data, re.DOTALL)
    if match is None:
        return data
    args, current, quote = [], "", None
    for ch in match.group(1):
        if quote:
            current += ch
            if ch == quote and not current.endswith("\\" + ch):
                quote = None
        elif ch in "\"'":
            quote = ch
            current += ch
        elif ch == ",":
            args.append(current.strip())
            current = ""
        else:
            current += ch
    args.append(current.strip())
    args = [a[1:-1] if a[:1] in "\"'" and a[-1:] == a[:1] else a for a in args]
    packed_arg_count = 5
    if len(args) < packed_arg_count:
        return data
    packed, _unused, charset, offset_s, radix_s = args[:packed_arg_count]
    try:
        offset, radix = int(offset_s), int(radix_s)
        delimiter = charset[radix]
    except ValueError, IndexError:  # silent: not packed: the page as it is
        return data
    out: list[str] = []
    for block in packed.split(delimiter):
        if not block:
            continue
        try:
            value = 0
            for ch in block:
                value = value * radix + charset.index(ch)
            out.append(chr(value - offset))
        except ValueError, OverflowError:  # silent: not packed: the page as it is
            return data
    return unquote("".join(out))
