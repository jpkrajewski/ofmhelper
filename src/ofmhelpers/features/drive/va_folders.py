"""Each VA's own folder in their owner's root (owner 2026-11-15: "there
should be automatic folder for VA created when there is a new VA, and show
in /drive just their name ... whole idea is cutting VAs from direct google
drive access").

Named after the VA (`User.label`) in the owner's "ALL ACCOUNTS", next to the
account folders; the VA's handmade reels land in it (/handmade,
`features/handmade/`), so the VA never opens Drive. Kept by id
(`drive_va_folders`): a renamed VA renames the folder. A VA's employer is
never changed after the add (owner 2026-11-18), so one root holds it.

Made by the Drive sweep (`folders.sync`: every `check_interval_s`, the Sync
button, a VA added on /users), which calls Drive only for a VA
with no folder in the owner's current root or one under an old name. A
folder deleted by hand is found missing by the next upload into it
(`folders.va_place`) and made again. Nothing is ever deleted or moved."""

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
    """The VAs working for `owner_id` now, by name."""
    found = [va for va in users.vas_of(owner_id) if va.active]
    return sorted(found, key=lambda va: va.label.lower())


def owners(owner: int | None = None) -> list[int]:
    """The people with an active VA (only `owner`, when given and they have
    one): whose roots get VA folders."""
    found = {
        u.owner_id
        for u in users.list_users()
        if u.role == Role.VA and u.active and u.owner_id is not None
    }
    return sorted(found if owner is None else found & {owner})


def of_owner(owner_id: int) -> list[VaFolder]:
    """The folders of `owner_id`'s VAs in their current root (dev reading
    the live folders: in its own DEV root), by name."""
    folders = store.va_folders(active_vas(owner_id), any_root=read_live_mode())
    return sorted(folders, key=lambda f: f.name.lower())


def ensure(
    drive: DriveApi, root: str, va: User, stored: VaFolder | None
) -> VaFolder | None:
    """`va`'s folder in `root`: the stored one, renamed when the VA's name
    changed; one of that name already there (made by hand); else a new one.
    A stored folder gone from Drive is made again. None: the VA was deleted
    while this ran (a folder made for them stays: nothing in Drive is
    deleted). Raises `DriveError`."""
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
    """The folders `owner_id`'s VAs lack in `root`, or carry under an old
    name; Drive is not called for the rest. One VA failing is reported."""
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
