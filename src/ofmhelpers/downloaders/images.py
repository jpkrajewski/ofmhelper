from __future__ import annotations

import time
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from ofmhelpers.downloaders.cookies import get_cookiefile


@dataclass
class ImageDownloadConfig:
    output_dir: Path = Path("downloads")
    # {id} doesn't exist for every extractor and silently becomes "None",
    # causing filename collisions across different posts (gallery-dl then
    # skips "already downloaded" files). post_shortcode is Instagram's real
    # unique identifier; num disambiguates multiple images in one post.
    filename_template: str = "{category}_{post_shortcode}_{num}.{extension}"
    extra_args: list[str] = field(default_factory=list)


@dataclass
class ImageDownloadResult:
    url: str
    success: bool
    output_paths: list[Path] = field(default_factory=list)
    error: str | None = None


def build_gallery_dl_cmd(url: str, config: ImageDownloadConfig) -> list[str]:
    cmd = [
        "gallery-dl",
        "-D",
        str(config.output_dir),
        "-f",
        config.filename_template,
        "--no-mtime",
        "--no-skip",  # overwrite existing files instead of silently skipping them
    ]

    cookiefile = get_cookiefile()
    if cookiefile:
        cmd += ["--cookies", cookiefile]

    cmd += config.extra_args
    cmd.append(url)
    return cmd


def download(
    url: str, config: ImageDownloadConfig | None = None
) -> ImageDownloadResult:
    config = config or ImageDownloadConfig()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    before_mtimes = {
        p: p.stat().st_mtime for p in config.output_dir.rglob("*") if p.is_file()
    }
    start_time = time.time()

    cmd = build_gallery_dl_cmd(url, config)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False, timeout=600
        )
    except subprocess.TimeoutExpired:
        return ImageDownloadResult(url=url, success=False, error="Download timed out")
    except FileNotFoundError:
        return ImageDownloadResult(
            url=url,
            success=False,
            error="gallery-dl not installed. Run: pip install gallery-dl",
        )

    changed_files = []
    for p in config.output_dir.rglob("*"):
        if not p.is_file():
            continue
        mtime = p.stat().st_mtime
        # new file, or existing file that was just overwritten during this run
        if p not in before_mtimes or mtime > before_mtimes[p]:
            if (
                mtime >= start_time - 1
            ):  # small buffer for filesystem timestamp granularity
                changed_files.append(p)

    changed_files.sort(key=lambda p: p.stat().st_mtime)

    if not changed_files:
        return ImageDownloadResult(
            url=url,
            success=False,
            error=result.stderr.strip()
            or "gallery-dl finished but no files were written",
        )

    return ImageDownloadResult(url=url, success=True, output_paths=changed_files)


def download_all(
    urls: list[str], config: ImageDownloadConfig | None = None
) -> list[ImageDownloadResult]:
    config = config or ImageDownloadConfig()
    return [download(url, config) for url in urls]
