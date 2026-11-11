"""What /drive shows behind its routes (web/routers/inventory/drive.py): the
board with the /clone video waiting for its folder, and one file's preview."""

from pydantic import BaseModel

from ofmhelpers.db.stores import drive_uploads as upload_store
from ofmhelpers.db.stores.jobs import get_job
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.drive import folders
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.integrations.gdrive import DriveError
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import UploadPick
from ofmhelpers.schemas.drive import DriveBoard

logger = get_logger(__name__)


class Source(BaseModel):
    """The tool whose finished video `/drive?upload=<job>` may name: its task
    and where its files are served (the routers know both)."""

    task: str
    files_prefix: str


class PageView(BaseModel):
    """/drive: each field is a name `drive.html` reads."""

    board: DriveBoard
    pick: UploadPick | None


class Thumb(BaseModel):
    content: bytes
    media_type: str


def _upload_pick(actor: str, job_id: str, source: Source) -> UploadPick | None:
    """The video `?upload=` names, when it is the session's own finished
    run."""
    job = get_job(job_id, actor)
    if job is None or job.task != source.task or job.status != "done":
        return None
    assets = job_assets(job, source.files_prefix).assets
    if not assets:
        return None
    first = assets[0]
    return UploadPick(
        job_id=job.id,
        name=first.download_name,
        preview_url=first.preview_url or first.view_url,
        poster_url=first.poster_url,
        already=[
            u.login_id
            for u in upload_store.for_jobs([job.id])
            if u.status in ("done", "queued")
        ],
    )


def page_view(
    scope: int, viewer: int, actor: str, upload: str, source: Source
) -> PageView:
    """At once, from each folder's last read: no Drive call. `upload`: a run
    whose video goes into the account card clicked."""
    return PageView(
        board=folders.board(owner=scope, viewer=viewer, live=False),
        pick=_upload_pick(actor, upload, source) if upload else None,
    )


def preview(login_id: int, file_id: str, scope: int) -> Thumb:
    """A file's preview in `login_id`'s folder; none to show is
    `NotFoundError`."""
    try:
        found = folders.thumbnail(login_id, file_id, scope)
    except (DriveError, SignInError) as exc:
        logger.warning("drive: preview of %s failed: %s", file_id, exc, exc_info=True)
        msg = "No preview"
        raise NotFoundError(msg) from exc
    if found is None:
        msg = "No preview yet"
        raise NotFoundError(msg)
    return Thumb(content=found.content, media_type=found.media_type)
