from pydantic import BaseModel

from ofmhelpers.db.drive_folders.schema import UploadPick
from ofmhelpers.db.drive_uploads import store as upload_store
from ofmhelpers.db.jobs.store import Readers, get_job
from ofmhelpers.features.assets.cards import job_assets
from ofmhelpers.features.refusals import NotFoundError
from ofmhelpers.features.storage.folders import StorageServiceFolders
from ofmhelpers.features.storage.models import PageView
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.integrations.storage import DriveError, DriveThumb
from ofmhelpers.log import get_logger
lazy from ofmhelpers.features.storage.schema import Source

logger = get_logger(__name__)


def _upload_pick(
    readers: Readers, job_id: str, sources: list[Source]
) -> UploadPick | None:
    job = get_job(job_id, readers)
    source = next((s for s in sources if job and s.task == job.task), None)
    if job is None or source is None or job.status != "done":
        return None
    assets = job_assets(job, source.files_prefix).assets
    if not assets:
        return None
    first = assets[0]
    uploads = [
        u for u in upload_store.for_jobs([job.id]) if u.status in ("done", "queued")
    ]
    return UploadPick(
        job_id=job.id,
        name=first.download_name,
        preview_url=first.preview_url or first.view_url,
        poster_url=first.poster_url,
        back_url=f"{source.page}#job-{job.id}",
        already=[u.login_id for u in uploads if u.login_id is not None],
        already_vas=[u.va_id for u in uploads if u.va_id is not None],
    )


class StorageServicePage(BaseModel, frozen=True):
    folders: StorageServiceFolders

    def view(
        self, viewer: int, readers: Readers, upload: str, sources: list[Source]
    ) -> PageView:
        return PageView(
            board=self.folders.saved_board(viewer=viewer),
            pick=_upload_pick(readers, upload, sources) if upload else None,
        )

    def preview(self, login_id: int, file_id: str) -> DriveThumb:
        try:
            found = self.folders.thumbnail(login_id, file_id)
        except (DriveError, SignInError) as exc:
            logger.warning(
                "drive: preview of %s failed: %s", file_id, exc, exc_info=True
            )
            msg = "No preview"
            raise NotFoundError(msg) from exc
        if found is None:
            msg = "No preview yet"
            raise NotFoundError(msg)
        return found
