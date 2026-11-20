import datetime

from pydantic import BaseModel, ConfigDict

from ofmhelpers.config.types import AccountFolder
from ofmhelpers.db.distribution.schema import NewPost, PostKind, PostSource
from ofmhelpers.db.tracked_accounts.store import list_accounts
from ofmhelpers.db.users.store import refresh_token
from ofmhelpers.features.drive import folders as drive_folders
from ofmhelpers.features.drive.folders import ReviewPlace, review_target
from ofmhelpers.integrations.gdrive import read_live_mode
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.distribution.schema import ModelPreset
lazy from ofmhelpers.db.drive_folders.schema import DriveCard, DriveFile

logger = get_logger(__name__)

_KINDS = frozendict({"image": PostKind.image, "video": PostKind.video})
_EPOCH = datetime.datetime.fromtimestamp(0, datetime.UTC)
_INSTAGRAM = "instagram"


def _made(f: DriveFile) -> datetime.datetime:
    return f.created_time or _EPOCH


def _posts(files: list[DriveFile], source: PostSource, preset: str) -> list[NewPost]:
    return [
        NewPost(
            file_id=f.id, name=f.name, kind=_KINDS[f.kind], source=source, preset=preset
        )
        for f in sorted(files, key=_made)
        if f.kind in _KINDS
    ]


class Sources(BaseModel):
    model_config = ConfigDict(frozen=True)

    user_id: int

    def cards(self) -> list[DriveCard]:
        board = drive_folders.board(owner=self.user_id, viewer=self.user_id, live=False)
        return board.cards

    def of_model(self, model: ModelPreset, cards: list[DriveCard]) -> list[NewPost]:
        handles = {
            a.handle.casefold()
            for a in list_accounts(self.user_id)
            if a.platform == _INSTAGRAM and a.creator == model.creator
        }
        found: dict[str, NewPost] = {}
        for card in cards:
            if card.target.handle.casefold() not in handles:
                continue
            try:
                posts = self.found(card, model.key)
            except Exception:
                logger.exception(
                    "distribution: reading @%s's folders for %s",
                    card.target.handle,
                    model.key,
                )
                continue
            for post in posts:
                found.setdefault(post.file_id, post)
        return list(found.values())

    def found(self, card: DriveCard, preset: str) -> list[NewPost]:
        posted = _posts(card.scan.posted, PostSource.posted, preset)
        if not card.account_folder_id:
            return posted
        return posted + self._threads(card.account_folder_id, preset)

    def _threads(self, account_folder_id: str, preset: str) -> list[NewPost]:
        token = refresh_token(self.user_id)
        if not token:
            logger.warning(
                "distribution: user %s has no Google Drive connected: no THREADS"
                " pictures are queued (connect it on /account)",
                self.user_id,
            )
            return []
        place = review_target(token, self.user_id)
        threads = _threads_folder(place, account_folder_id)
        if threads is None:
            return []
        sets = [f.id for f in place.drive.children(threads, folders_only=True)]
        if not sets:
            return []
        listing = place.drive.children_of(sets)
        pictures = [f for folder in sets for f in listing.of(folder)]
        return _posts(pictures, PostSource.threads, preset)


def _threads_folder(place: ReviewPlace, account_folder_id: str) -> str | None:
    drive = place.drive
    parent: str | None = account_folder_id
    if read_live_mode():
        account = drive.folder(account_folder_id)
        twin = drive.find_folder(place.folder_id, account.name)
        parent = twin.id if twin is not None else None
    if parent is None:
        return None
    found = drive.find_folder(parent, AccountFolder.THREADS)
    return found.id if found is not None else None
