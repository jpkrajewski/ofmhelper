from __future__ import annotations

import random
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from ofmhelpers.config import settings
from ofmhelpers.downloaders import fake
from ofmhelpers.downloaders.cookies import cookie_file
from ofmhelpers.downloaders.models import (
    DownloadResult,
    ExtractorArgs,
    YtDlpInfo,
    YtDlpOptions,
)
from ofmhelpers.log import get_logger
from ofmhelpers.utils.proxy_pool import ProxyPool

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)


class _ProxySettings(Protocol):
    @property
    def proxy_urls(self) -> list[str]: ...

    proxy_retries: int


# Sites that refuse a datacenter IP's logged-out requests (Instagram sends the
# server's IP to its login page; an uploaded login that went bad answers 400,
# verified 2026-10-21). Their metadata goes logged out through the site's
# proxies first; the media file itself comes from the CDN directly (signed
# URLs, any IP), so a proxy carries a few KB, not the video. The login + no
# proxy try stays last. A new site = one entry: domain -> its settings group.
_PROXIED_SITES: dict[str, Callable[[], _ProxySettings]] = {
    "instagram.com": lambda: settings.instagram,
}


def _proxies_for(url: str) -> list[str]:
    """The proxies a URL is tried through before the plain try: up to
    1 + proxy_retries, starting at a random one so parallel downloads spread
    over the pool. [] for a site not listed, or with no proxies set."""
    host = (urlsplit(url).hostname or "").lower()
    for domain, group in _PROXIED_SITES.items():
        if host == domain or host.endswith(f".{domain}"):
            cfg = group()
            pool = ProxyPool(cfg.proxy_urls, start=random.randrange(1 << 16))  # noqa: S311 -- spread, not crypto
            tries = min(len(pool), 1 + cfg.proxy_retries)
            return [p for _ in range(tries) if (p := pool.next())]
    return []


def _default_extractor_args() -> ExtractorArgs:
    """Points yt-dlp's bgutil PO-token plugin at the pot-provider container
    over the Docker network, if BGUTIL_POT_PROVIDER_URL is set. Without this,
    the plugin defaults to 127.0.0.1:4416, which inside Docker means "this
    container," not the separate pot-provider service."""
    pot_url = settings.downloaders.bgutil_pot_provider_url
    if not pot_url:
        return {}
    return {"youtubepot-bgutilhttp": {"base_url": [pot_url]}}


class DownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: Path = Path("downloads")
    # "best" alone caps out at whatever pre-muxed format the site offers
    # (often 360-480p on YouTube/Shorts). This asks for the best video-only
    # + best audio-only streams and lets ffmpeg merge them -- that's where
    # the actual high-res formats live.
    format: str = "bestvideo*+bestaudio/best"
    merge_output_format: str = "mp4"
    write_thumbnail: bool = False
    download_playlist: bool = True
    filename_template: str = "%(uploader)s_%(id)s%(playlist_index)s.%(ext)s"

    # Cookie source for authenticated sites (Instagram etc). Only used as a
    # fallback when the download runs without the user's own cookies.txt.
    # Defaults to None -- "firefox" only makes sense on a machine that
    # actually has a real Firefox profile, i.e. NOT inside Docker. Set via
    # env var if you need it for local/non-container use.
    cookies_from_browser: str | None = Field(
        default_factory=lambda: settings.downloaders.cookies_from_browser
    )

    # TikTok downloads only — re-encode to H.264/AAC (fixes tools that
    # misreport HEVC as "0x0" resolution). Other platforms are untouched.
    reencode_tiktok_to_h264: bool = True
    delete_original_after_reencode: bool = True
    crf: int = 18
    preset: str = "medium"
    audio_bitrate: str = "192k"

    # Workaround for YouTube's SABR rollout, ONLY needed if you do NOT have
    # a PO Token provider (e.g. bgutil-ytdlp-pot-provider) running. If you
    # do have one running, leave this as None -- yt-dlp's own default
    # client selection (which may pick android_vr, ios, etc depending on
    # what actually works) is smarter than hardcoding a client list here.
    # Forcing a list overrides that and can make things WORSE.
    # https://github.com/yt-dlp/yt-dlp/issues/12482
    youtube_player_clients: list[str] | None = None

    # Extra per-extractor arguments, merged over youtube_player_clients'.
    extractor_args: ExtractorArgs = Field(default_factory=_default_extractor_args)

    def ydl_opts(self, cookiefile: str | None = None) -> YtDlpOptions:
        """The yt-dlp options this config means, logged in as `cookiefile`
        (the downloading user's own cookies.txt, `cookies.cookie_file`)."""
        browser = (
            None
            if cookiefile or not self.cookies_from_browser
            else (self.cookies_from_browser, None, None, None)
        )
        extractor_args: ExtractorArgs = {}
        if self.youtube_player_clients:
            extractor_args["youtube"] = {"player_client": self.youtube_player_clients}
        extractor_args.update(self.extractor_args)
        return YtDlpOptions(
            format=self.format,
            merge_output_format=self.merge_output_format,
            outtmpl=str(self.output_dir / self.filename_template),
            writethumbnail=self.write_thumbnail,
            noplaylist=not self.download_playlist,
            cookiefile=cookiefile,
            cookiesfrombrowser=browser,
            extractor_args=extractor_args,
        )

    def ffmpeg_cmd(self, src: Path, dst: Path) -> list[str]:
        return [
            "ffmpeg",
            "-y",
            "-i",
            str(src),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            "-crf",
            str(self.crf),
            "-preset",
            self.preset,
            "-c:a",
            "aac",
            "-b:a",
            self.audio_bitrate,
            str(dst),
        ]


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def is_video_file(path: Path) -> bool:
    return path.suffix.lower() in VIDEO_EXTENSIONS


NON_MEDIA_SUFFIXES = {
    ".json",
    ".description",
    ".vtt",
    ".srt",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
}


def is_media_file(path: Path) -> bool:
    """True for the actual video/audio yt-dlp downloaded; False for the
    side files it also writes (info.json, thumbnail, subtitles, etc)."""
    return path.suffix.lower() not in NON_MEDIA_SUFFIXES


def is_tiktok_url(url: str) -> bool:
    return "tiktok.com" in url.lower()


def reencode_to_h264(path: Path, config: DownloadConfig) -> Path:
    """Re-encode a video to H.264/AAC. Returns original path unchanged on failure."""
    if not is_video_file(path):
        return path

    tmp_dst = path.with_name(f"{path.stem}_h264.mp4")
    cmd = config.ffmpeg_cmd(path, tmp_dst)

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return path

    if config.delete_original_after_reencode and tmp_dst.exists():
        path.unlink(missing_ok=True)

    return tmp_dst


def download(
    url: str, config: DownloadConfig | None = None, cookiefile: str | None = None
) -> DownloadResult:
    """Download a single URL, logged in as `cookiefile` (None: logged out).
    TikTok URLs get re-encoded to H.264; everything else doesn't."""
    config = config or DownloadConfig()
    if settings.infra.fake_externals:
        return fake.video(url, config.output_dir)

    try:
        import yt_dlp
    except ImportError:
        return DownloadResult(
            url=url,
            success=False,
            error="yt-dlp not installed. Run: pip install yt-dlp",
        )

    config.output_dir.mkdir(parents=True, exist_ok=True)
    opts = config.ydl_opts(cookiefile)
    attempts: list[str | None] = [*_proxies_for(url), None]
    errors: list[str] = []
    for proxy in attempts:
        try:
            info = _fetch(yt_dlp, url, opts, proxy)
            break
        except Exception as exc:
            # Never the proxy URL: it carries the proxy's credentials.
            logger.warning(
                "download %s failed (%s)",
                url,
                "via proxy" if proxy else "direct",
                exc_info=True,
            )
            errors.append(str(exc))
    else:
        return DownloadResult(url=url, success=False, error=errors[0])

    try:
        paths = YtDlpInfo.model_validate(info).output_paths()
        if not paths:
            return DownloadResult(
                url=url,
                success=False,
                error="Download finished but yt-dlp reported no output file",
            )

        paths = [p for p in paths if is_media_file(p)] or paths

        if config.reencode_tiktok_to_h264 and is_tiktok_url(url):
            paths = [reencode_to_h264(p, config) for p in paths]

        return DownloadResult(url=url, success=True, output_paths=paths)
    except Exception as exc:
        return DownloadResult(url=url, success=False, error=str(exc))


def _fetch(yt_dlp: Any, url: str, opts: YtDlpOptions, proxy: str | None) -> Any:
    """yt-dlp's info for `url`, files written. Through `proxy`: metadata
    logged out via the proxy, then the files direct (see _PROXIED_SITES)."""
    if proxy is None:
        with yt_dlp.YoutubeDL(opts.to_ydl()) as ydl:
            return ydl.extract_info(url, download=True)
    anon = opts.model_copy(update={"cookiefile": None, "cookiesfrombrowser": None})
    with yt_dlp.YoutubeDL(anon.model_copy(update={"proxy": proxy}).to_ydl()) as ydl:
        info = ydl.sanitize_info(ydl.extract_info(url, download=False))
    with yt_dlp.YoutubeDL(anon.to_ydl()) as ydl:
        return ydl.process_ie_result(info, download=True)


def download_all(
    urls: list[str], config: DownloadConfig | None = None, cookies: str | None = None
) -> list[DownloadResult]:
    """Every URL, logged in with `cookies` (the user's cookies.txt text)."""
    config = config or DownloadConfig()
    with cookie_file(cookies) as cookiefile:
        return [download(url, config, cookiefile) for url in urls]
