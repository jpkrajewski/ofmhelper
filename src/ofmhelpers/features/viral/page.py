"""What /viral shows and does behind its routes
(web/routers/downloads/viral.py): the search picked and the earlier ones, a
search started, the worker's half of it, and a finished search as one zip."""

import zipfile
from collections.abc import Callable
from pathlib import Path

from pydantic import BaseModel, ValidationError

from ofmhelpers.config import settings
from ofmhelpers.db.stores.jobs import get_job, list_jobs_page, merge_params
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.assets.store import make_job_dir, uploads_root
from ofmhelpers.features.jobs.runner import start_job
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.features.viral.service import find_viral
from ofmhelpers.integrations.instagram import ProfileHandle
from ofmhelpers.schemas import Job, JobQueued, ViralFile, ViralSearch

TASK = "viral_reels"
# The earlier searches the page lists.
_HISTORY = 20


class PageView(BaseModel):
    """/viral: each field is a name `viral.html` reads."""

    current: ViralSearch | None
    searches: list[ViralSearch]
    top: int
    scan: int
    max_usd: float


class Archive(BaseModel):
    """A search's reels as one zip, in a `folder` of its own (the route
    deletes it once sent)."""

    path: Path
    folder: Path


def _shown(actor: str, job_id: str | None, newest: list[Job]) -> Job | None:
    """The search picked (`?job=`), else the newest; a `job_id` that is not a
    search of theirs shows none."""
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
    """`run`: the worker's half, as RQ stores it (the router's `run_viral`)."""
    try:
        handle = ProfileHandle(username=profile).username
    except ValidationError as exc:
        msg = "Paste an Instagram profile: @name or its link"
        raise RefusedError(msg) from exc
    job_id = start_job(TASK, {"profile": handle}, run, {"profile": handle}, actor=actor)
    return JobQueued(job_id=job_id)


def found(job_id: str, profile: str, folder: Path) -> list[ViralFile]:
    """The worker's half: the profile's top reels saved in `folder`; what
    Apify read and charged goes on the job's params."""
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
    """Every reel of a finished search as one zip (stored: H.264 does not
    compress further), named after the profile."""
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
    """The worker's half, in a fresh folder."""
    folder = make_job_dir(uploads_root() / "viral")
    return found(job_id, profile, folder)
