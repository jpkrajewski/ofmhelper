import secrets
import shutil
import subprocess
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path
lazy from collections.abc import Iterator

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.integrations.downloaders import generic, images, kvs, savers
from ofmhelpers.integrations.downloaders.cookies import cookie_file
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.integrations.downloaders.sites import images_first
from ofmhelpers.log import get_logger
from ofmhelpers.media.video_probe import duration_s

logger = get_logger(__name__)

_OUTPUT_ROOT = generic.DownloadConfig().output_dir


class _Tool(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    fetch: Callable[[], DownloadResult]


class _Download(BaseModel):
    model_config = ConfigDict(frozen=True)

    url: str
    cookiefile: str | None
    out: Path
    single: bool = False

    def video(self) -> DownloadResult:
        config = (
            generic.DownloadConfig.single_post(output_dir=self.out)
            if self.single
            else generic.DownloadConfig(output_dir=self.out)
        )
        return generic.download(self.url, config=config, cookiefile=self.cookiefile)

    def images(self) -> DownloadResult:
        most = settings.downloaders.single_post_items if self.single else None
        config = images.ImageDownloadConfig(output_dir=self.out, max_items=most)
        return images.download(self.url, config=config, cookiefile=self.cookiefile)

    def kvs(self) -> DownloadResult:
        return kvs.download(self.url, self.out)

    def savers(self) -> DownloadResult:
        return savers.download(self.url, self.out, single=self.single)

    def order(self) -> list[_Tool]:
        video = _Tool(name="video", fetch=self.video)
        player = _Tool(name="kvs", fetch=self.kvs)
        pictures = _Tool(name="images", fetch=self.images)
        last = _Tool(name="savers", fetch=self.savers)
        if images_first(self.url):
            return [pictures, video, last]
        return [video, player, pictures, last]

    def run(self) -> DownloadResult:
        url = self.url
        errors: list[str] = []
        for tool in self.order():
            result = tool.fetch()
            reason = _broken_video(result) if result.success else None
            if result.success and reason is None:
                return result
            if reason is not None:
                result = DownloadResult(url=url, success=False, error=reason)
            logger.info(
                "download %s: %s gave nothing: %s", url, tool.name, result.error
            )
            errors.append(f"{tool.name}: {result.error or 'no files'}")
        known = [e for e in errors if "unsupported url" not in e.lower()]
        return DownloadResult(url=url, success=False, error=" | ".join(known or errors))


def _broken_video(result: DownloadResult) -> str | None:
    broken = []
    for path in result.output_paths:
        if not generic.is_video_file(path):
            continue
        try:
            duration_s(path, settings.downloaders.probe_timeout_s)
        except OSError, RuntimeError, ValueError, subprocess.SubprocessError:
            logger.warning(
                "download %s: %s is not a video", result.url, path.name, exc_info=True
            )
            path.unlink(missing_ok=True)
            broken.append(path.name)
    if not broken:
        return None
    return "the site sent a web page, not the video"


def download(url: str, cookiefile: str | None = None) -> DownloadResult:
    out = _OUTPUT_ROOT / secrets.token_hex(8)
    return _Download(url=url, cookiefile=cookiefile, out=out).run()


@contextmanager
def downloaded(
    url: str, cookiefile: str | None = None, *, single: bool = False
) -> Iterator[DownloadResult]:
    out = _OUTPUT_ROOT / secrets.token_hex(8)
    try:
        yield _Download(url=url, cookiefile=cookiefile, out=out, single=single).run()
    finally:
        shutil.rmtree(out, ignore_errors=True)


def download_all(urls: list[str], cookies: str | None = None) -> list[DownloadResult]:
    with cookie_file(cookies) as cookiefile:
        return [download(url, cookiefile) for url in urls]
