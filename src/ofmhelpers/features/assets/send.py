"""A finished job's video handed on to another tool's page, already picked
there: a download's to /clone or /clone-long, a clone's to /edit or
/clone-long (owner 2026-11-16: "there is no option to click edit from
clone"). Moved here from `features/downloads/media.py`, which keeps its
route."""

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode

from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.downloads.service import Redirect
from ofmhelpers.features.refusals import NotFoundError, RefusedError


class SendTarget(StrEnum):
    """Where a job's video can be sent on."""

    CLONE = "clone"
    CLONE_LONG = "clone-long"
    EDIT = "edit"

    @property
    def page(self) -> str:
        return f"/{self.value}"

    @property
    def one_only(self) -> bool:
        """/clone takes one run per video; /clone-long and /edit one video."""
        return self is not SendTarget.CLONE


def _kept_videos(
    paths: list[str | None], assets_root: Path, *, one_only: bool
) -> list[str]:
    """A job's files may live outside the asset store the pages pick from
    (a download's area, the provider's output folder); each video is
    registered into this session's store (as /reels does) so the page opens
    with it picked."""
    videos: list[str] = []
    for path in paths:
        if path is None:
            continue
        src = Path(path)
        if not src.is_file() or classify_kind(src.name) != "video":
            continue
        kept = register_generated_asset(src, assets_root)
        if kept is not None:
            videos.append(str(kept.resolve()))
            if one_only:
                break
    return videos


def send(
    job_id: str,
    target: str,
    index: int | None,
    readers: str | frozenset[str],
    assets_root: Path,
) -> Redirect:
    """The URL that opens `target` with the job's video already picked.
    `index`: that file, what a card sends; none: all its videos (/clone) or
    its first. The job must be one `readers` see (the asker's own, or for an
    owner a VA's too); its video files are registered into the asker's store
    (`assets_root`) before being handed over."""
    if target not in SendTarget:
        msg = "Unknown target"
        raise RefusedError(msg)
    chosen = SendTarget(target)
    job = get_job(job_id, actor=readers)
    if job is None:
        msg = "No such run"
        raise NotFoundError(msg)
    paths = job.local_paths()
    if index is not None:
        paths = paths[index : index + 1]
    videos = _kept_videos(paths, assets_root, one_only=chosen.one_only)
    if not videos:
        msg = "This run has no video"
        raise RefusedError(msg)
    return Redirect(
        redirect=chosen.page + "?" + urlencode([("video", v) for v in videos])
    )
