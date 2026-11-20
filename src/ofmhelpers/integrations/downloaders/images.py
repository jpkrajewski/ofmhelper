import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.integrations.downloaders import fake
from ofmhelpers.integrations.downloaders.cookies import cookie_file
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.log import get_logger

logger = get_logger(__name__)

_LOGIN_WALLS = {
    "redirect to login page": (
        "This post needs a login: upload your fresh cookies.txt at /cookies"
    ),
}


def _error(stderr: str) -> str:
    for marker, message in _LOGIN_WALLS.items():
        if marker in stderr:
            return message
    return stderr.strip() or "gallery-dl finished but no files were written"


class ImageDownloadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_dir: Path = Path("downloads")
    filename_template: str = "{category}_{post_shortcode}_{num}.{extension}"
    extra_args: list[str] = []
    max_items: int | None = None

    def gallery_dl_cmd(self, url: str, cookiefile: str | None = None) -> list[str]:
        cmd = [
            "gallery-dl",
            "-D",
            str(self.output_dir),
            "-f",
            self.filename_template,
            "--no-mtime",
            "--no-skip",
        ]

        if self.max_items is not None:
            cmd += ["--range", f"1-{self.max_items}"]
        if cookiefile:
            cmd += ["--cookies", cookiefile]

        cmd += self.extra_args
        cmd += ["--", url]
        return cmd


def download(
    url: str, config: ImageDownloadConfig | None = None, cookiefile: str | None = None
) -> DownloadResult:
    config = config or ImageDownloadConfig()
    if settings.infra.fake_externals:
        return fake.images(url, config.output_dir)
    config.output_dir.mkdir(parents=True, exist_ok=True)

    before_mtimes = {
        p: p.stat().st_mtime for p in config.output_dir.rglob("*") if p.is_file()
    }
    start_time = time.time()

    cmd = config.gallery_dl_cmd(url, cookiefile)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=settings.downloaders.image_download_timeout_s,
        )
    except subprocess.TimeoutExpired:
        logger.warning("gallery-dl: %s timed out", url, exc_info=True)
        return DownloadResult(url=url, success=False, error="Download timed out")
    except FileNotFoundError:
        logger.warning("gallery-dl: not installed", exc_info=True)
        return DownloadResult(
            url=url,
            success=False,
            error="gallery-dl not installed. Run: pip install gallery-dl",
        )

    changed_files = []
    for p in config.output_dir.rglob("*"):
        if not p.is_file():
            continue
        mtime = p.stat().st_mtime
        if (p not in before_mtimes or mtime > before_mtimes[p]) and (
            mtime >= start_time - 1
        ):
            changed_files.append(p)

    changed_files.sort(key=lambda p: p.stat().st_mtime)

    if not changed_files:
        return DownloadResult(url=url, success=False, error=_error(result.stderr))

    return DownloadResult(url=url, success=True, output_paths=changed_files)


def download_all(
    urls: list[str],
    config: ImageDownloadConfig | None = None,
    cookies: str | None = None,
) -> list[DownloadResult]:
    config = config or ImageDownloadConfig()
    with cookie_file(cookies) as cookiefile:
        return [download(url, config, cookiefile) for url in urls]
