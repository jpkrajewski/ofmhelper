from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ofmhelpers.downloaders.cookies import get_cookiefile


def _default_extra_ydl_opts() -> dict:
    """Points yt-dlp's bgutil PO-token plugin at the pot-provider container
    over the Docker network, if BGUTIL_POT_PROVIDER_URL is set. Without this,
    the plugin defaults to 127.0.0.1:4416, which inside Docker means "this
    container," not the separate pot-provider service."""
    pot_url = os.getenv("BGUTIL_POT_PROVIDER_URL")
    if not pot_url:
        return {}
    return {"extractor_args": {"youtubepot-bgutilhttp": {"base_url": [pot_url]}}}


@dataclass
class DownloadConfig:
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
    # fallback if no cookies.txt file is present (see get_cookiefile()).
    # Defaults to None -- "firefox" only makes sense on a machine that
    # actually has a real Firefox profile, i.e. NOT inside Docker. Set via
    # env var if you need it for local/non-container use.
    cookies_from_browser: str | None = field(
        default_factory=lambda: os.getenv("OFM_COOKIES_FROM_BROWSER")
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

    extra_ydl_opts: dict = field(default_factory=_default_extra_ydl_opts)


@dataclass
class DownloadResult:
    url: str
    success: bool
    output_paths: list[Path] = field(default_factory=list)
    error: str | None = None


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


def build_ydl_opts(config: DownloadConfig) -> dict:
    opts: dict = {
        "format": config.format,
        "merge_output_format": config.merge_output_format,
        "outtmpl": str(config.output_dir / config.filename_template),
        "writethumbnail": config.write_thumbnail,
        "quiet": True,
        "no_warnings": False,
        "noplaylist": not config.download_playlist,
        "remote_components": {"ejs:github"},
    }

    cookiefile = get_cookiefile()
    if cookiefile:
        opts["cookiefile"] = cookiefile
    elif config.cookies_from_browser:
        opts["cookiesfrombrowser"] = (config.cookies_from_browser, None, None, None)

    if config.youtube_player_clients:
        opts.setdefault("extractor_args", {})["youtube"] = {
            "player_client": config.youtube_player_clients
        }

    # Merge extra_ydl_opts' extractor_args instead of clobbering whatever
    # was set above (e.g. youtube_player_clients).
    extra = dict(config.extra_ydl_opts)
    extra_extractor_args = extra.pop("extractor_args", {})
    if extra_extractor_args:
        opts.setdefault("extractor_args", {}).update(extra_extractor_args)
    opts.update(extra)

    return opts


def build_ffmpeg_cmd(src: Path, dst: Path, config: DownloadConfig) -> list[str]:
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
        str(config.crf),
        "-preset",
        config.preset,
        "-c:a",
        "aac",
        "-b:a",
        config.audio_bitrate,
        str(dst),
    ]


def reencode_to_h264(path: Path, config: DownloadConfig) -> Path:
    """Re-encode a video to H.264/AAC. Returns original path unchanged on failure."""
    if not is_video_file(path):
        return path

    tmp_dst = path.with_name(f"{path.stem}_h264.mp4")
    cmd = build_ffmpeg_cmd(path, tmp_dst, config)

    try:
        subprocess.run(cmd, capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return path

    if config.delete_original_after_reencode and tmp_dst.exists():
        path.unlink(missing_ok=True)

    return tmp_dst


def _extract_output_paths(info: dict) -> list[Path]:
    """Pull the real output filepath(s) straight from yt-dlp's info dict.
    This is what yt-dlp itself considers the final file after any merging
    or postprocessing -- far more reliable than parsing log text, which
    doesn't always emit the same line for every extractor/format."""
    entries = info.get("entries") if info.get("_type") == "playlist" else [info]
    paths: list[Path] = []
    for entry in entries or []:
        if not entry:
            continue
        requested = entry.get("requested_downloads") or []
        if requested:
            for rd in requested:
                fp = rd.get("filepath") or rd.get("_filename")
                if fp:
                    paths.append(Path(fp))
        elif entry.get("filepath"):
            paths.append(Path(entry["filepath"]))
    return paths


def download(url: str, config: DownloadConfig | None = None) -> DownloadResult:
    """Download a single URL. TikTok URLs get re-encoded to H.264; everything else doesn't."""
    config = config or DownloadConfig()

    try:
        import yt_dlp
    except ImportError:
        return DownloadResult(
            url=url,
            success=False,
            error="yt-dlp not installed. Run: pip install yt-dlp",
        )

    config.output_dir.mkdir(parents=True, exist_ok=True)
    opts = build_ydl_opts(config)

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)

        paths = _extract_output_paths(info)
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


def download_all(
    urls: list[str], config: DownloadConfig | None = None
) -> list[DownloadResult]:
    config = config or DownloadConfig()
    return [download(url, config) for url in urls]
