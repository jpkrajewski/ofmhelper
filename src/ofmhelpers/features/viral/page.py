import zipfile
from pathlib import Path
lazy from collections.abc import Callable

from pydantic import BaseModel, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import get_job, list_jobs_page, merge_params
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.store import make_job_dir, uploads_root
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.jobs.schema import JobQueued
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.viral.models import ViralFile, ViralSearch
from ofmhelpers.features.viral.service import find_viral
from ofmhelpers.integrations.instagram import ProfileHandle
lazy from ofmhelpers.db.jobs.schema import Job

TASK = "viral_reels"
_HISTORY = 20


class PageView(BaseModel):
    current: ViralSearch | None
    searches: list[ViralSearch]
    top: int
    scan: int
    max_usd: float


class Archive(BaseModel):
    path: Path
    folder: Path


def _shown(actor: str, job_id: str | None, newest: list[Job]) -> Job | None:
    if not job_id:
        return newest[0] if newest else None
    picked = get_job(job_id, actor)
    return picked if picked and picked.task == TASK else None


def page_view(actor: str, job_id: str | None, files_prefix: str) -> PageView:
    searches = list_jobs_page({TASK}, 0, _HISTORY, actor).jobs
    current = _shown(actor, job_id, searches)
    return PageView(
        current=ViralSearch.of(current, job_assets(current, files_prefix).assets)
        if current
        else None,
        searches=[ViralSearch.of(j, []) for j in searches],
        top=settings.viral.top,
        scan=settings.viral.scan,
        max_usd=settings.viral.max_usd,
    )


def start(profile: str, actor: str, run: Callable[..., list[ViralFile]]) -> JobQueued:
    try:
        handle = ProfileHandle(username=profile).username
    except ValidationError as exc:
        msg = "Paste an Instagram profile: @name or its link"
        raise RefusedError(msg) from exc
    job_id = start_job(TASK, {"profile": handle}, run, {"profile": handle}, actor=actor)
    return JobQueued(job_id=job_id)


def found(job_id: str, profile: str, folder: Path) -> list[ViralFile]:
    search = find_viral(profile, folder)
    merge_params(job_id, {"scanned": search.scanned, "cost_usd": search.cost_usd})
    return [
        ViralFile(
            name=r.path.name,
            path=str(r.path),
            code=r.reel.code,
            url=r.reel.url,
            plays=r.reel.plays,
            likes=r.reel.likes,
            comments=r.reel.comments,
            posted_at=r.reel.posted_at,
            seconds=r.reel.seconds,
        )
        for r in search.reels
    ]


def archive(job_id: str, actor: str, zips_root: Path) -> Archive:
    job = get_job(job_id, actor)
    if job is None or job.task != TASK:
        msg = "No such search"
        raise NotFoundError(msg)
    files = [Path(p) for p in job.local_paths() if p and Path(p).is_file()]
    if not files:
        msg = "This search has no reels"
        raise NotFoundError(msg)
    profile = job.params.get("profile", "reels")
    folder = make_job_dir(zips_root)
    path = folder / f"{profile}-top-{len(files)}.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        for rank, file in enumerate(files, start=1):
            zf.write(file, f"{rank:02d}-{file.name}")
    return Archive(path=path, folder=folder)


def run_viral(job_id: str, profile: str) -> list[ViralFile]:
    folder = make_job_dir(uploads_root() / "viral")
    return found(job_id, profile, folder)
