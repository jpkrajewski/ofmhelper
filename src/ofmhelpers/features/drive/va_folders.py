from ofmhelpers.db.stores import drive_folders as store
from ofmhelpers.db.stores import users
from ofmhelpers.integrations.gdrive import (
    DriveApi,
    DriveError,
    FolderGoneError,
    read_live_mode,
)
from ofmhelpers.integrations.signin import SignInError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.drive import SyncReport, VaFolder
from ofmhelpers.schemas.users import Role, User

logger = get_logger(__name__)


def active_vas(owner_id: int) -> list[User]:
    found = [va for va in users.vas_of(owner_id) if va.active]
    return sorted(found, key=lambda va: va.label.lower())


def owners(owner: int | None = None) -> list[int]:
    found = {
        u.owner_id
        for u in users.list_users()
        if u.role == Role.VA and u.active and u.owner_id is not None
    }
    return sorted(found if owner is None else found & {owner})


def of_owner(owner_id: int) -> list[VaFolder]:
    folders = store.va_folders(active_vas(owner_id), any_root=read_live_mode())
    return sorted(folders, key=lambda f: f.name.lower())


def ensure(
    drive: DriveApi, root: str, va: User, stored: VaFolder | None
) -> VaFolder | None:
    name = va.label
    if stored is not None:
        try:
            current = drive.folder(stored.folder_id)
        except FolderGoneError:
            logger.warning(
                "drive: folder of VA %s is gone: made again", va.id, exc_info=True
            )
        else:
            if current.name != name:
                drive.rename(current.id, name)
            folder = VaFolder(
                user_id=va.id, folder_id=current.id, name=name, root_folder_id=root
            )
            if stored != folder and not store.save_va_folder(folder):
                logger.info("drive: VA %s gone meanwhile, skipped", va.id)
                return None
            return folder
    found = drive.find_folder(root, name) or drive.create_folder(root, name)
    folder = VaFolder(user_id=va.id, folder_id=found.id, name=name, root_folder_id=root)
    if not store.save_va_folder(folder):
        logger.info("drive: VA %s gone meanwhile, folder left as it is", va.id)
        return None
    logger.info("drive: folder of VA %s is %s (%s)", va.id, found.id, name)
    return folder


def sync(drive: DriveApi, root: str, owner_id: int, report: SyncReport) -> None:
    vas = active_vas(owner_id)
    stored = {f.user_id: f for f in store.va_folders(vas)}
    for va in vas:
        folder = stored.get(va.id)
        if folder is not None and folder.name == va.label:
            continue
        try:
            kept = ensure(drive, root, va, folder)
        except (DriveError, SignInError) as exc:
            logger.warning("drive: folder of VA %s failed", va.id, exc_info=True)
            report.failed.append(f"{va.label}: {exc}")
            continue
        if kept is not None:
            (report.renamed if folder is not None else report.created).append(va.label)
