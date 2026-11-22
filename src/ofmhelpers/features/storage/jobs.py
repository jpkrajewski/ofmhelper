from collections import defaultdict
lazy from collections.abc import Callable

from ofmhelpers.db.drive_folders import store as drive_folders
from ofmhelpers.db.drive_folders.schema import SyncReport
from ofmhelpers.db.drive_uploads import store as uploads
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role
from ofmhelpers.features.storage.client import read_live, storage_of
from ofmhelpers.features.storage.folders import enabled
from ofmhelpers.features.storage.models import (
    BinMove,
    Moved,
    StorageUsage,
    VaTarget,
    _OwnerTargets,
)
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.drive_folders.models import DriveTarget
lazy from ofmhelpers.features.storage.uploads import Folder

__all__ = ["enabled", "run_bin", "run_scan", "sync", "upload_files"]

logger = get_logger(__name__)


def _by_owner(targets: list[DriveTarget]) -> list[_OwnerTargets]:
    grouped: dict[int, list[DriveTarget]] = defaultdict(list)
    for target in targets:
        grouped[target.device.user_id].append(target)
    return [_OwnerTargets(user_id=u, targets=t) for u, t in grouped.items()]


def _va_owners(owner: int | None) -> list[int]:
    found = {
        u.owner_id
        for u in users.list_users()
        if u.role == Role.VA and u.active and u.owner_id is not None
    }
    return sorted(found if owner is None else found & {owner})


def _owners(owner: int | None) -> list[_OwnerTargets]:
    found = _by_owner(drive_folders.targets(owner))
    seen = {o.user_id for o in found}
    return found + [
        _OwnerTargets(user_id=user_id, targets=[])
        for user_id in _va_owners(owner)
        if user_id not in seen
    ]


def _synced(work: Callable[[SyncReport], None]) -> SyncReport:
    report = SyncReport()
    if read_live():
        logger.info("drive: live-read mode, sweep skipped (no writes to live)")
        return report
    with drive_folders.sync_lock() as held:
        if not held:
            report.busy = True
            return report
        work(report)
    logger.info(
        "drive: synced -- %d created, %d found, %d renamed, %d POSTED folders,"
        " %d newly posted, %d not connected, %d failed",
        len(report.created),
        len(report.adopted),
        len(report.renamed),
        len(report.posted_folders),
        report.newly_posted,
        len(report.not_connected),
        len(report.failed),
    )
    return report


def sync(owner: int | None = None) -> SyncReport:
    def each_owner(report: SyncReport) -> None:
        for found in _owners(owner):
            storage = storage_of(found.user_id, interactive=False)
            storage.folders.sync_owner(found.targets, report)

    return _synced(each_owner)


def run_scan(user_id: int, job_id: str) -> StorageUsage:
    usage = storage_of(user_id, interactive=False).bin.scan()
    logger.info(
        "storage scan %s of %s: %s account folders", job_id, user_id, len(usage.folders)
    )
    return usage


def run_bin(
    user_id: int, job_id: str, part: str, folder_id: str | None = None
) -> Moved:
    move = BinMove.model_validate({"part": part, "folder_id": folder_id})
    moved = storage_of(user_id, interactive=False).bin.move(move.part, move.folder_id)
    logger.info(
        "storage move %s of %s: %s items, %s bytes of %s to the bin",
        job_id,
        user_id,
        moved.items,
        moved.bytes,
        moved.part,
    )
    return moved


def _folder_owner(login_id: int) -> int | None:
    found = next((t for t in drive_folders.targets() if t.login_id == login_id), None)
    return found.device.user_id if found else None


def _va_target(va_id: int) -> VaTarget | None:
    va = users.get_user(va_id)
    if va is None or va.owner_id is None:
        return None
    folder = next(iter(drive_folders.va_folders([va])), None)
    return VaTarget(owner_id=va.owner_id, folder=folder) if folder else None


def upload_files(upload_ids: list[int]) -> None:
    folders = drive_folders.account_folders()
    for upload_id in upload_ids:
        found = uploads.get(upload_id)
        if found is None or found.status != "queued":
            continue
        upload = found
        owner_id: int | None = None
        folder: Folder | None = None
        if upload.va_id is not None:
            if (target := _va_target(upload.va_id)) is not None:
                owner_id, folder = target.owner_id, target.folder
        elif upload.login_id is not None:
            owner_id = _folder_owner(upload.login_id)
            folder = folders.get(upload.login_id)
        if folder is None or owner_id is None:
            uploads.finish(upload_id, error="The Drive folder is gone: Sync on /drive")
            continue
        try:
            storage_of(owner_id, interactive=False).uploads.upload(upload, folder)
        except Exception as exc:
            logger.warning("drive upload %s failed", upload_id, exc_info=True)
            uploads.finish(upload_id, error=f"Upload failed: {exc}")
