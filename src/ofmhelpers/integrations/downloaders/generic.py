import random
import subprocess
from functools import lru_cache
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit
lazy from collections.abc import Callable

from pydantic import BaseModel, ConfigDict, Field
lazy from yt_dlp.extractor import gen_extractor_classes

from ofmhelpers.config import settings
from ofmhelpers.integrations.downloaders import fake
from ofmhelpers.integrations.downloaders.cookies import cookie_file
from ofmhelpers.integrations.downloaders.models import (
    DownloadResult,
    ExtractorArgs,
    VideoInfo,
    YtDlpInfo,
    YtDlpOptions,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media import ffmpeg
from ofmhelpers.media.video_probe import duration_s
from ofmhelpers.utils.proxy_pool import ProxyPool

logger = get_logger(__name__)


class _ProxySettings(Protocol):
    @property
    def proxy_urls(self) -> list[str]: ...

    proxy_retries: int


_PROXIED_SITES: dict[str, Callable[[], _ProxySettings]] = {
    "instagram.com": lambda: settings.instagram,
}


def _proxies_for(url: str) -> list[str]:
    host = (urlsplit(url).hostname or "").lower()
    for domain, group in _PROXIED_SITES.items():
        if host == domain or host.endswith(f".{domain}"):
            cfg = group()
            pool = ProxyPool(cfg.proxy_urls, start=random.randrange(1 << 16))  # noqa: S311
            tries = min(len(pool), 1 + cfg.proxy_retries)
            return [p for _ in range(tries) if (p := pool.next())]
    return []


def _default_extractor_args() -> ExtractorArgs:
    pot_url = settings.downloaders.bgutil_pot_provider_url
    if not pot_url:
        return {}
    return {"youtubepot-bgutilhttp": {"base_url": [pot_url]}}


SITE_EXTRACTORS_ONLY = ("default", "-generic")


class DownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: Path = Path("downloads")
    format: str = "bestvideo*+bestaudio/best"
    merge_output_format: str = "mp4"
    write_thumbnail: bool = False
    download_playlist: bool = True
    playlist_items: str | None = None
    allowed_extractors: list[str] | None = None
    socket_timeout: float | None = None
    filename_template: str = "%(uploader)s_%(id)s%(playlist_index)s.%(ext)s"

    cookies_from_browser: str | None = Field(
        default_factory=lambda: settings.downloaders.cookies_from_browser
    )

    reencode_tiktok_to_h264: bool = True
    delete_original_after_reencode: bool = True
    crf: int = 18
    preset: str = "veryfast"
    audio_bitrate: str = "192k"

    youtube_player_clients: list[str] | None = None

    extractor_args: ExtractorArgs = Field(default_factory=_default_extractor_args)

    @classmethod
    def single_post(cls, **fields: Any) -> DownloadConfig:
        return cls(
            download_playlist=False,
            playlist_items=f"1:{settings.downloaders.single_post_items}",
            allowed_extractors=list(SITE_EXTRACTORS_ONLY),
            **fields,
        )

    def ydl_opts(self, cookiefile: str | None = None) -> YtDlpOptions:
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
            playlist_items=self.playlist_items,
            allowed_extractors=self.allowed_extractors,
            socket_timeout=self.socket_timeout,
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
    return path.suffix.lower() not in NON_MEDIA_SUFFIXES


def is_tiktok_url(url: str) -> bool:
    return "tiktok.com" in url.lower()


@lru_cache(maxsize=1)
def _site_extractors() -> tuple[Any, ...]:
    return tuple(ie for ie in gen_extractor_classes() if ie.ie_key() != "Generic")


def site_of(url: str) -> str | None:
    return next((ie.IE_NAME for ie in _site_extractors() if ie.suitable(url)), None)


def reencode_to_h264(path: Path, config: DownloadConfig) -> Path:
    if not is_video_file(path):
        return path

    tmp_dst = path.with_name(f"{path.stem}_h264.mp4")
    cmd = config.ffmpeg_cmd(path, tmp_dst)

    try:
        ffmpeg.run_bytes(cmd, settings.downloaders.reencode_timeout_s)
    except RuntimeError, FileNotFoundError:
        logger.warning(
            "re-encode of %s failed, original kept", path.name, exc_info=True
        )
        tmp_dst.unlink(missing_ok=True)
        return path

    if config.delete_original_after_reencode and tmp_dst.exists():
        path.unlink(missing_ok=True)

    return tmp_dst


def download(
    url: str, config: DownloadConfig | None = None, cookiefile: str | None = None
) -> DownloadResult:
    config = config or DownloadConfig()
    if settings.infra.fake_externals:
        return fake.video(url, config.output_dir)

    try:
        import yt_dlp
    except ImportError:
        logger.warning("yt-dlp: not installed", exc_info=True)
        return DownloadResult(
            url=url,
            success=False,
            error="yt-dlp not installed. Run: pip install yt-dlp",
        )

    config.output_dir.mkdir(parents=True, exist_ok=True)
    reading = _Reading(yt_dlp=yt_dlp, url=url, opts=config.ydl_opts(cookiefile))
    attempts: list[str | None] = [*_proxies_for(url), None]
    errors: list[str] = []
    for proxy in attempts:
        try:
            info = reading.fetch(proxy)
            break
        except Exception as exc:
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
        logger.warning("download %s: its files could not be read", url, exc_info=True)
        return DownloadResult(url=url, success=False, error=str(exc))


def video_info(
    url: str, cookiefile: str | None = None, *, socket_timeout: float | None = None
) -> VideoInfo:
    if settings.infra.fake_externals:
        return fake.info(url)
    import yt_dlp

    config = DownloadConfig.single_post(socket_timeout=socket_timeout)
    reading = _Reading(yt_dlp=yt_dlp, url=url, opts=config.ydl_opts(cookiefile))
    errors: list[str] = []
    info = None
    for proxy in _proxies_for(url):
        info = reading.try_peek(proxy, errors)
        if info is not None or _hidden_logged_out(errors[-1]):
            break
    if info is None:
        info = reading.try_peek(None, errors)
    if info is None:
        return VideoInfo(
            url=url,
            error=errors[-1],
            logged_out_error=errors[0] if len(errors) > 1 else None,
        )
    entry = YtDlpInfo.model_validate(info).first_video()
    if entry is None:
        return VideoInfo(url=url, error="No video at this link (a photo post?)")
    seconds = entry.duration
    stream = entry.video_url()
    if seconds is None and stream:
        try:
            seconds = duration_s(stream, settings.downloaders.probe_timeout_s)
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            logger.warning(
                "video info %s: length not probed: %s", url, exc, exc_info=True
            )
            return VideoInfo(url=url, error=f"The video's length is unreadable: {exc}")
    return VideoInfo(url=url, seconds=seconds, thumbnail=entry.thumbnail)


_LOGGED_OUT_HIDDEN = ("empty media response", "restricted video", "not available")


def _hidden_logged_out(error: str) -> bool:
    lowered = error.lower()
    return any(sign in lowered for sign in _LOGGED_OUT_HIDDEN)


class _Reading(BaseModel, frozen=True):
    yt_dlp: Any
    url: str
    opts: YtDlpOptions

    def _ydl(self, opts: YtDlpOptions) -> Any:
        return self.yt_dlp.YoutubeDL(opts.model_dump(exclude_none=True))

    def try_peek(self, proxy: str | None, errors: list[str]) -> Any | None:
        try:
            return self.peek(proxy)
        except Exception as exc:
            logger.warning(
                "video info %s failed (%s)",
                self.url,
                "via proxy" if proxy else "direct",
                exc_info=True,
            )
            errors.append(str(exc))
            return None

    def peek(self, proxy: str | None) -> Any:
        opts = self.opts
        if proxy is not None:
            opts = opts.model_copy(
                update={"cookiefile": None, "cookiesfrombrowser": None, "proxy": proxy}
            )
        with self._ydl(opts) as ydl:
            return ydl.extract_info(self.url, download=False)

    def fetch(self, proxy: str | None) -> Any:
        if proxy is None:
            with self._ydl(self.opts) as ydl:
                return ydl.extract_info(self.url, download=True)
        anon = self.opts.model_copy(
            update={"cookiefile": None, "cookiesfrombrowser": None}
        )
        with self._ydl(anon.model_copy(update={"proxy": proxy})) as ydl:
            info = ydl.sanitize_info(ydl.extract_info(self.url, download=False))
        with self._ydl(anon) as ydl:
            return ydl.process_ie_result(info, download=True)


def download_all(
    urls: list[str], config: DownloadConfig | None = None, cookies: str | None = None
) -> list[DownloadResult]:
    config = config or DownloadConfig()
    with cookie_file(cookies) as cookiefile:
        return [download(url, config, cookiefile) for url in urls]
