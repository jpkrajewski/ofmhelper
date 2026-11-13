"""What the download tests share: links that "download" to small files of
their own (no network), a poster made without ffmpeg, and the `downloads`
rows read and aged straight in the database."""

import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path

from PIL import Image
from pydantic import BaseModel
from sqlalchemy import select, update

from ofmhelpers.db.models import DownloadRow
from ofmhelpers.db.session import session_scope
from ofmhelpers.db.stores import downloads as store
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.features.downloads import media
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.previews import render as previews
from ofmhelpers.integrations.downloaders.models import DownloadResult
from ofmhelpers.schemas.downloads import Download, DownloadedFile

HOUR = 3600
DAY = 24 * HOUR


GONE = (
    "ERROR: [Instagram] DdolTu_JpJ0: Instagram sent an empty media response."
    " Check if this post is accessible in your browser without being logged-in."
)


class FakeDownloads:
    """`downloaders.media.download_all`, offline: each link gets a folder of
    its own under `root` with one `clip.mp4` (a link with "photo" in it: one
    `photo.jpg`; with "fail": no file, an error). `fetched`: every link
    asked for, in order -- a link downloaded twice is in it twice."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.fetched: list[str] = []

    def __call__(
        self,
        urls: list[str],
        cookies: str | None = None,  # noqa: ARG002 -- the real one's argument
    ) -> list[DownloadResult]:
        return [self._one(url) for url in urls]

    def failing(self, url: str) -> DownloadResult:
        """What `_one` answers once the site no longer has the link: yt-dlp's
        own words for a reel of a banned profile (prod 2026-11-12, job
        65a6bb46)."""
        self.fetched.append(url)
        return DownloadResult(url=url, success=False, error=GONE)

    def _one(self, url: str) -> DownloadResult:
        if "fail" in url:
            return self.failing(url)
        self.fetched.append(url)
        folder = self.root / secrets.token_hex(4)
        folder.mkdir(parents=True)
        path = folder / ("photo.jpg" if "photo" in url else "clip.mp4")
        # Its own bytes each time: a video fetched again hashes differently.
        path.write_bytes(f"{url} #{len(self.fetched)}".encode())
        return DownloadResult(url=url, success=True, output_paths=[path])


def fake_downloads(monkeypatch, tmp_path: Path) -> FakeDownloads:
    fake = FakeDownloads(tmp_path / "downloads")
    monkeypatch.setattr(media, "download_all", fake)
    return fake


def fake_posters(monkeypatch) -> None:
    """A poster and an image's preview are files written at once: no ffmpeg,
    no Pillow on bytes that are no picture."""

    def thumb(source: Path, dest: Path, size: int, **_: object) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"RIFF")

    monkeypatch.setattr(previews, "write_video_thumb", thumb)
    monkeypatch.setattr(previews, "write_image_thumb", thumb)


class Removed(BaseModel):
    """A download whose video the sweep removed, as `removed_download` made
    it: its job and where its copy in the asset store was."""

    job_id: str
    asset_path: str


def removed_download(folder: Path, uploads: Path, user_id: int, link: str) -> Removed:
    """A finished download of `link` by `user_id` whose video is gone and
    whose poster is kept under `uploads` (the app's uploads root): what the
    retention sweep leaves. Written straight to the database and the disk,
    for a browser test whose app is another process."""
    video = folder / "gone" / "clip.mp4"
    result = [DownloadResult(url=link, success=True, output_paths=[video])]
    job_id = create_job("download_media", {"urls": [link]}, actor=str(user_id))
    run_job(job_id, lambda: result, {})
    asset = uploads / "assets" / "users" / str(user_id) / f"{'ab' * 32}__clip.mp4"
    now = datetime.now(UTC)
    file = DownloadedFile(
        url=link, position=0, file_path=str(video), asset_path=str(asset)
    )
    store.record(user_id, job_id, [file], now)
    assert store.mark_removed(row(job_id).id, now, now)
    poster = uploads / ".previews" / previews.poster_path(job_id, 0).name
    poster.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (48, 64), "navy").save(poster, "WEBP")
    return Removed(job_id=job_id, asset_path=str(asset))


def rows(job_id: str) -> list[Download]:
    with session_scope() as s:
        found = s.execute(
            select(DownloadRow)
            .where(DownloadRow.job_id == job_id)
            .order_by(DownloadRow.position)
        ).scalars()
        return [Download.model_validate(row) for row in found]


def row(job_id: str) -> Download:
    (only,) = rows(job_id)
    return only


def backdate(job_id: str, seconds: float, *, asked_too: bool = True) -> None:
    """The job's files downloaded `seconds` earlier than they were; with
    `asked_too`, last asked for that much earlier as well."""
    back = timedelta(seconds=seconds)
    values = {"created_at": DownloadRow.created_at - back}
    if asked_too:
        values["last_used_at"] = DownloadRow.last_used_at - back
    with session_scope() as s:
        s.execute(
            update(DownloadRow).where(DownloadRow.job_id == job_id).values(values)
        )
