from __future__ import annotations

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
    """Snapshot the output dir before running gallery-dl, diff after --
    any new file is something this call downloaded. Handles multi-image
    posts (carousels) automatically since every new file is captured,
    and avoids depending on gallery-dl's stdout format at all."""
    config = config or ImageDownloadConfig()
    config.output_dir.mkdir(parents=True, exist_ok=True)

    before = {p for p in config.output_dir.rglob("*") if p.is_file()}

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

    after = {p for p in config.output_dir.rglob("*") if p.is_file()}
    new_files = sorted(after - before, key=lambda p: p.stat().st_mtime)

    if not new_files:
        return ImageDownloadResult(
            url=url,
            success=False,
            error=result.stderr.strip()
            or "gallery-dl finished but no new files appeared",
        )

    return ImageDownloadResult(url=url, success=True, output_paths=new_files)


def download_all(
    urls: list[str], config: ImageDownloadConfig | None = None
) -> list[ImageDownloadResult]:
    config = config or ImageDownloadConfig()
    return [download(url, config) for url in urls]
