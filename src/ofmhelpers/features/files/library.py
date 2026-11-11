"""The file manager's user view (`/file-manager`): every user's files, by
user then section, newest first. Only files that exist: a section of runs =
done runs' results still on disk; `uploads` = the user's part of the asset
store (their reuse picker)."""

from pathlib import Path
from urllib.parse import urlencode
lazy from collections.abc import Sequence

from pydantic import BaseModel, ConfigDict
lazy from starlette.datastructures import UploadFile

from ofmhelpers.db.stores.jobs import get_job, job_counts, list_jobs_page
from ofmhelpers.db.stores.users import list_users
from ofmhelpers.features.assets.kinds import classify_kind
from ofmhelpers.features.assets.refs import StoredFile, stored_files
from ofmhelpers.features.assets.store import (
    assets_root_for_user,
    in_asset_root,
    save_asset,
    strip_asset_hash_prefix,
)
from ofmhelpers.features.previews.render import PREVIEW_ERRORS, file_thumb
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.log import get_logger
from ofmhelpers.schemas import FileTile, Job, UserFiles

logger = get_logger(__name__)

# The section that is not runs: the user's part of the asset store.
UPLOADS = "uploads"
TILES_PER_PAGE = 60


class Section(BaseModel):
    """A tab of a user's files: its label and the tasks filed under it."""

    model_config = ConfigDict(frozen=True)

    label: str
    tasks: frozenset[str] = frozenset()


class Tiles(BaseModel):
    """One page of a section, newest first; `more`: whether more follow."""

    tiles: list[FileTile] = []
    more: bool = False


class UserView(Tiles):
    """/file-manager: each field is a name `file_manager.html` reads."""

    users: list[UserFiles]
    picked: UserFiles | None
    sections: dict[str, str]
    section: str
    page: int


class Deleted(BaseModel):
    """Whose section a deleted run's file was in (None: nobody's to show)."""

    user_id: int | None = None
    section: str = ""


def _tile(name: str, label: str, mtime: float, url: str, query: str = "") -> FileTile:
    """`url` serves the file (`?download=1` as an attachment), `url/thumb`
    its thumbnail, `url/delete` deletes it; `query` is added to all three."""
    return FileTile(
        name=name,
        label=label,
        mtime=mtime,
        open_url=f"{url}{query}",
        delete_url=f"{url}/delete{query}",
        thumb_url=(
            f"{url}/thumb{query}" if classify_kind(name) in ("image", "video") else None
        ),
    )


def _uploads(user_id: int) -> list[StoredFile]:
    """The user's stored files (`refs.stored_files`)."""
    root = assets_root_for_user(user_id)
    return stored_files(root, None) if root.is_dir() else []


def _upload_tiles(user_id: int, start: int) -> Tiles:
    stored = sorted(_uploads(user_id), key=lambda f: f.mtime, reverse=True)
    tiles = [
        _tile(
            strip_asset_hash_prefix(Path(f.path).name),
            "Upload",
            f.mtime,
            f"/file-manager/assets/{user_id}",
            "?" + urlencode({"path": f.path}),
        )
        for f in stored[start : start + TILES_PER_PAGE]
    ]
    return Tiles(tiles=tiles, more=start + TILES_PER_PAGE < len(stored))


class Library(BaseModel):
    """The user view over the app's tools: `sections` in tab order (which
    tasks each holds) and every task's `labels`, both from the routers'
    registry."""

    model_config = ConfigDict(frozen=True)

    sections: dict[str, Section]
    labels: dict[str, str]

    def section_of(self, task: str) -> str:
        return next(key for key, s in self.sections.items() if task in s.tasks)

    def _users(self) -> list[UserFiles]:
        counts = job_counts(set(self.labels))
        found = []
        for user in list_users():
            per_section = dict.fromkeys(self.sections, 0)
            for count in counts:
                if count.actor == str(user.id):
                    per_section[self.section_of(count.task)] += count.count
            per_section[UPLOADS] = len(_uploads(user.id))
            found.append(
                UserFiles(id=user.id, label=user.name or user.email, counts=per_section)
            )
        return found

    def _run_tiles(self, job: Job) -> list[FileTile]:
        """A done run's files still on disk."""
        label = job.params.get("model") or self.labels[job.task]
        return [
            _tile(
                Path(path).name,
                label,
                job.finished_at or job.created_at,
                f"/file-manager/runs/{job.id}/{i}",
            )
            for i, path in enumerate(job.local_paths())
            if path and Path(path).is_file()
        ]

    def _section_tiles(self, user_id: int, section: str, page: int) -> Tiles:
        start = page * TILES_PER_PAGE
        if section == UPLOADS:
            return _upload_tiles(user_id, start)
        found = list_jobs_page(
            set(self.sections[section].tasks),
            start,
            TILES_PER_PAGE,
            actor=str(user_id),
            status="done",
        )
        return Tiles(
            tiles=[t for j in found.jobs for t in self._run_tiles(j)],
            more=start + TILES_PER_PAGE < found.total,
        )

    def view(self, user_id: int | None, section: str, page: int) -> UserView:
        """Users, then one user's files of one section."""
        if section not in self.sections:
            msg = "Unknown section"
            raise RefusedError(msg)
        users = self._users()
        picked = next((u for u in users if u.id == user_id), None)
        page = max(page, 0)
        found = self._section_tiles(picked.id, section, page) if picked else Tiles()
        return UserView(
            users=users,
            picked=picked,
            sections={key: s.label for key, s in self.sections.items()},
            section=section,
            tiles=found.tiles,
            page=page,
            more=found.more,
        )

    def delete_run_file(self, job_id: str, index: int) -> Deleted:
        """The file off the disk; its card (and the run, when nothing is
        left) goes at the next listing (`stores.jobs._heal`)."""
        found = run_file(job_id, index)
        found.unlink()
        logger.info("file manager: deleted %s of job %s", found.name, job_id)
        job = get_job(job_id)
        if job is None or job.actor is None or not job.actor.isdigit():
            return Deleted()
        return Deleted(user_id=int(job.actor), section=self.section_of(job.task))


def asset(user_id: int, path: str) -> Path:
    """A file of `user_id`'s part of the asset store, never outside it."""
    found = Path(path)
    if not in_asset_root(assets_root_for_user(user_id), found) or not found.is_file():
        msg = "File not found"
        raise NotFoundError(msg)
    return found


def delete_asset(found: Path, user_id: int) -> None:
    """`found`: a file of `user_id`'s (`asset`)."""
    found.unlink()
    logger.info("file manager: deleted upload %s of user %s", found.name, user_id)


def add_assets(user_id: int, files: Sequence[UploadFile]) -> None:
    """Into the user's reference store (their reuse picker), deduped by
    content like any upload; media only (`save_asset` refuses anything
    else)."""
    root = assets_root_for_user(user_id)
    for upload in files:
        save_asset(upload, root)
    logger.info("file manager: %d upload(s) for user %s", len(files), user_id)


def run_file(job_id: str, index: int) -> Path:
    job = get_job(job_id)
    paths = job.local_paths() if job else []
    path = paths[index] if 0 <= index < len(paths) else None
    if not path or not Path(path).is_file():
        msg = "File not found"
        raise NotFoundError(msg)
    return Path(path)


def thumb(file_path: Path) -> Path:
    """An image's or video's small webp, made once and kept."""
    if classify_kind(file_path.name) not in ("image", "video"):
        msg = "No thumbnail"
        raise NotFoundError(msg)
    try:
        return file_thumb(file_path)
    except PREVIEW_ERRORS:
        logger.warning("file manager: no thumbnail of %s", file_path, exc_info=True)
        msg = "No thumbnail"
        raise NotFoundError(msg) from None
