# Copyright (C) 2026 jpkrajewski

from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode

from ofmhelpers.db.jobs.store import get_job
from ofmhelpers.features.assets.store import register_generated_asset
from ofmhelpers.features.kinds import FileKind
from ofmhelpers.features.redirect import Redirect
from ofmhelpers.features.refusals import NotFoundError, RefusedError


class SendTarget(StrEnum):
    CLONE = "clone"
    CLONE_LONG = "clone-long"
    EDIT = "edit"
    SPOOF = "spoof"

    @property
    def page(self) -> str:
        return f"/{self.value}"

    @property
    def one_only(self) -> bool:
        return self is not SendTarget.CLONE


def _kept_videos(
    paths: list[str | None],
    assets_root: Path,
    *,
    one_only: bool,
) -> list[str]:
    videos: list[str] = []
    for path in paths:
        if path is None:
            continue
        src = Path(path)
        if not src.is_file() or FileKind.from_ext(src.name) != FileKind.VIDEO:
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
        redirect=chosen.page + "?" + urlencode([("video", v) for v in videos]),
    )
