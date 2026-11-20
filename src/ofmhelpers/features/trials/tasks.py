import random
import zlib
from functools import partial
from pathlib import Path
from typing import Any
lazy from collections.abc import Callable

import logfire
from pydantic import BaseModel, SkipValidation

from ofmhelpers.cache import JobQueue, QueueName
from ofmhelpers.features.drive.folders import (
    ReviewPlace,
    account_subfolder,
    review_target,
)
from ofmhelpers.features.jobs.runner import Requeued
from ofmhelpers.features.previews.render import PREVIEW_ERRORS
from ofmhelpers.features.trials import media
from ofmhelpers.features.trials.captions import captions_for, understand
from ofmhelpers.features.trials.state import (
    FolderState,
    Recipe,
    ReelState,
    at,
    change,
    change_reel,
    load,
    locked,
    work_dir,
)
from ofmhelpers.integrations.gdrive import (
    DriveApi,
    DriveError,
    DriveFile,
    FolderGoneError,
)
from ofmhelpers.log import get_logger
from ofmhelpers.media.repurpose.pipelines.snapchat_iphone16_usa import (
    run as pipeline_run,
)
from ofmhelpers.schemas.gdrive import AccountFolder
lazy from ofmhelpers.media.repurpose.repurpose import RunResult

logger = get_logger(__name__)

DRIVE_PROBLEM = "Google Drive did not answer. Connect it again."
DOWNLOAD_FAILED = "Could not download it from Drive"
MAKE_FAILED = "Could not make it"
CAPTIONS_FAILED = "Could not write captions"
UPLOAD_FAILED = "Could not upload it"
NOTHING_NEW = "No new reels in POSTED"
SOURCE_GONE = "Source video is gone from Drive"
PIPELINE = "snapchat_iphone16_usa"


class Made(BaseModel):
    generated: int
    failed: int


class Captioned(BaseModel):
    captions: int


class Uploaded(BaseModel):
    uploaded: int


def _someone_waits() -> bool:
    return bool(JobQueue(name=QueueName.MEDIA).line().waiting)


def _not_made(count: int) -> str:
    reels = "1 new reel" if count == 1 else f"{count} new reels"
    return f"Could not make {reels}. Click Generate trials to try again."


def _seed(key: str, made: int) -> int:
    return zlib.crc32(f"{key}:{made}".encode()) & 0x7FFFFFFF


def _page_media(copy: Path) -> None:
    try:
        media.preview(copy)
        media.poster(copy)
    except PREVIEW_ERRORS:
        logger.warning("trials: preview of %s failed", copy, exc_info=True)


def merge_posted(state: FolderState, videos: list[DriveFile]) -> None:
    known = {r.key: r for r in state.reels}
    listed = {f.id for f in videos}
    for reel in state.reels:
        reel.posted = reel.key in listed
    new = [
        ReelState(key=f.id, name=f.name, posted_at=f.created_time, posted=True)
        for f in videos
        if f.id not in known
    ]
    for f in videos:
        if f.id in known and f.created_time:
            known[f.id].posted_at = f.created_time
    kept = [r for r in state.reels if r.out or r.key in listed]
    state.reels = sorted([*new, *kept], key=ReelState.posted_or_never, reverse=True)


def _why(exc: Exception, had_source: bool) -> str:
    if isinstance(exc, FolderGoneError):
        return SOURCE_GONE
    if isinstance(exc, DriveError) or not had_source:
        return DOWNLOAD_FAILED
    return MAKE_FAILED


def _draws(ran: RunResult | None) -> dict[str, Any] | None:
    if ran is None or ran.step is None:
        return None
    return {
        **ran.step.model_dump(mode="json", exclude={"command"}),
        "seconds": ran.seconds,
    }


def _recipe(reel: ReelState) -> Recipe:
    return reel.recipe or Recipe(
        seed=_seed(reel.key, reel.made - 1),
        caption=reel.caption or "",
        pipeline=PIPELINE,
    )


def _written(src: Path) -> list[str]:
    return captions_for(understand(src))


_FOLDER_LOCK_S = 60


def _camera_name(source: str, taken: set[str], rng: random.Random) -> str:
    while True:
        name = f"VID_{rng.randint(1000, 9999)}.mp4"
        if name != source and name not in taken:
            return name


def _trash(drive: DriveApi, file_ids: list[str]) -> bool:
    fine = True
    for file_id in file_ids:
        try:
            drive.trash_file(file_id)
        except FolderGoneError:  # silent: gone already, what was wanted
            continue
        except DriveError:
            logger.warning("trials: trashing %s failed", file_id, exc_info=True)
            fine = False
    return fine


class _Trials(BaseModel, frozen=True, arbitrary_types_allowed=True):
    drive: SkipValidation[DriveApi]
    folder_id: str
    files: list[DriveFile]


class Folder(BaseModel, frozen=True):
    user_id: int
    login_id: int
    refresh_token: str = ""

    def _on_disk(self, reel: ReelState) -> bool:
        path = at(self.user_id, self.login_id, reel.local)
        return path is not None and path.is_file()

    def _change(self, fn: Callable[[FolderState], None]) -> None:
        change(self.user_id, self.login_id, fn)

    def _change_reel(self, key: str, fn: Callable[[ReelState], None]) -> None:
        change_reel(self.user_id, self.login_id, key, fn)

    def _load(self) -> FolderState:
        return load(self.user_id, self.login_id)

    def _drive(self) -> ReviewPlace | None:
        try:
            found = review_target(self.refresh_token, self.user_id)
        except DriveError:
            logger.warning(
                "trials: Drive of user %s unreachable", self.user_id, exc_info=True
            )
            self._change(lambda s: s.report(DRIVE_PROBLEM))
            return None
        self._change(lambda s: s.report(None))
        return found

    def _fetch(self, drive: DriveApi, key: str) -> str:
        got = drive.download(key)
        rel = f"src/{key}-{Path(got.name).name}"
        path = work_dir(self.user_id, self.login_id) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(got.content)
        return rel

    def _source(self, reel: ReelState) -> Path:
        root = work_dir(self.user_id, self.login_id)
        if self._on_disk(reel):
            return root / str(reel.local)
        found = self._drive() if self.refresh_token else None
        if found is None:
            raise FileNotFoundError(reel.key)
        rel = self._fetch(found.drive, reel.key)
        self._change_reel(reel.key, partial(ReelState.source_back, local=rel))
        return root / rel

    def _download(self, drive: DriveApi) -> None:
        for reel in self._load().reels:
            if reel.out or self._on_disk(reel):
                continue
            try:
                rel = self._fetch(drive, reel.key)
            except DriveError, OSError:
                logger.warning("trials: download of %s failed", reel.key, exc_info=True)
                failed = partial(ReelState.mark_failed, error=DOWNLOAD_FAILED)
                self._change_reel(reel.key, failed)
                continue
            self._change_reel(reel.key, partial(ReelState.mark_downloaded, local=rel))

    def _list_posted(self, drive: DriveApi) -> bool:
        posted_id = self._load().posted_folder_id
        if not posted_id:
            return False
        try:
            files = drive.children(posted_id)
        except DriveError:
            logger.warning(
                "trials: POSTED of %s unreadable", self.login_id, exc_info=True
            )
            self._change(lambda s: s.report(DRIVE_PROBLEM))
            return False
        videos = [f for f in files if f.kind == "video"]
        self._change(lambda s: merge_posted(s, videos))
        return True

    def make_trials(self) -> Made | Requeued:
        found = self._drive()
        read = found is not None and self._list_posted(found.drive)
        if found is not None:
            self._download(found.drive)
        state = self._load()
        keys = [r.key for r in state.todo if self._on_disk(r)]
        if read and not state.todo:
            self._change(lambda s: s.tell(NOTHING_NEW))
        made: list[bool] = []
        for i, key in enumerate(keys):
            made.append(self.make(key))
            if i + 1 < len(keys) and _someone_waits():
                logger.info(
                    "trials: Generate of %s lets the line in after %d of %d",
                    self.login_id,
                    i + 1,
                    len(keys),
                )
                return Requeued()
        if left := len(self._load().todo):
            self._change(lambda s: s.tell(_not_made(left)))
        return Made(generated=made.count(True), failed=made.count(False))

    def generate_reels(self, keys: list[str] | None = None) -> Made:
        state = self._load()
        wanted = [r.key for r in state.reels if keys is None or r.key in keys]
        made = [self.make(key) for key in wanted]
        return Made(generated=made.count(True), failed=made.count(False))

    def make(self, key: str, *, restore: bool = False) -> bool:
        reel = self._load().reel(key)
        if reel is None:
            return False
        out_rel = f"out/{key}.mp4"
        out = work_dir(self.user_id, self.login_id) / out_rel
        making = out.with_name(f".{key}.making.mp4")
        src: Path | None = None
        try:
            src = self._source(reel)
            if restore:
                choices, recipe = reel.captions, _recipe(reel)
            else:
                choices = reel.captions or ([] if reel.caption else _written(src))
                recipe = Recipe(
                    seed=_seed(key, reel.made),
                    caption=reel.caption or choices[0],
                    pipeline=PIPELINE,
                )
            making.parent.mkdir(parents=True, exist_ok=True)
            with logfire.span("trials pipeline {key}", key=key):
                ran = pipeline_run(
                    src, making, caption=recipe.caption, seed=recipe.seed
                )
            if not restore:
                recipe = recipe.model_copy(update={"draws": _draws(ran)})
            making.replace(out)
            with logfire.span("trials page media {key}", key=key):
                _page_media(out)
        except Exception as exc:
            logger.warning("trials: making %s failed", key, exc_info=True)
            making.unlink(missing_ok=True)
            error = _why(exc, had_source=src is not None)
            failed = ReelState.upload_failed if restore else ReelState.mark_failed
            self._change_reel(key, partial(failed, error=error))
            return False
        if restore:
            self._change_reel(key, ReelState.restored)
        else:
            self._change_reel(key, lambda r: r.mark_made(out_rel, choices, recipe))
        return True

    def write_captions(self, key: str) -> Captioned:
        reel = self._load().reel(key)
        try:
            if reel is None:
                raise FileNotFoundError(key)  # noqa: TRY301 - same handling
            choices = _written(self._source(reel))
        except Exception:
            logger.warning("trials: captions of %s failed", key, exc_info=True)
            self._change_reel(key, lambda r: r.captions_failed(CAPTIONS_FAILED))
            return Captioned(captions=0)
        self._change_reel(key, lambda r: r.mark_captions(choices))
        return Captioned(captions=len(choices))

    def _trials_folder(self, place: ReviewPlace, account_folder_id: str) -> str:
        with locked(self.user_id, self.login_id, "trials-folder", _FOLDER_LOCK_S):
            return account_subfolder(place, account_folder_id, AccountFolder.TRIALS)

    def _open_trials(self, account_folder_id: str) -> _Trials | None:
        found = self._drive()
        if found is None:
            return None
        drive = found.drive
        try:
            trials_id = self._trials_folder(found, account_folder_id)
            self._change(lambda s: s.remember_trials_folder(trials_id))
            return _Trials(
                drive=drive, folder_id=trials_id, files=drive.children(trials_id)
            )
        except DriveError:
            logger.warning(
                "trials: TRIALS folder for %s failed", self.login_id, exc_info=True
            )
            self._change(lambda s: s.report(DRIVE_PROBLEM))
            return None

    def _batch_failed(self, keys: list[str]) -> None:
        def back(state: FolderState) -> None:
            for reel in state.reels:
                if reel.key in keys:
                    reel.upload_failed(UPLOAD_FAILED)

        self._change(back)

    def _restored(self, keys: list[str]) -> list[str]:
        state = self._load()
        ready = []
        for key in keys:
            reel = state.reel(key)
            path = at(self.user_id, self.login_id, reel.out) if reel else None
            if path is not None and path.is_file():
                ready.append(key)
                continue
            with logfire.span("trials remake {key}", key=key, login_id=self.login_id):
                if self.make(key, restore=True):
                    ready.append(key)
        return ready

    def upload_trials(self, account_folder_id: str, keys: list[str]) -> Uploaded:
        keys = self._restored(keys)
        if not keys:
            return Uploaded(uploaded=0)
        opened = self._open_trials(account_folder_id)
        if opened is None:
            self._batch_failed(keys)
            return Uploaded(uploaded=0)
        drive, trials_id, old = opened.drive, opened.folder_id, opened.files
        rng = random.Random()  # noqa: S311 - a file name, not a secret
        taken = {f.name for f in old}
        state = self._load()
        landed: dict[str, DriveFile] = {}
        try:
            for key in keys:
                reel = state.reel(key)
                path = at(self.user_id, self.login_id, reel.out) if reel else None
                if reel is None or path is None or not path.is_file():
                    raise FileNotFoundError(key)
                name = _camera_name(reel.name, taken, rng)
                landed[key] = drive.upload(trials_id, path, name, "video/mp4")
                taken.add(name)
        except DriveError, OSError:
            logger.warning("trials: upload to %s failed", self.login_id, exc_info=True)
            _trash(drive, [f.id for f in landed.values()])
            self._batch_failed(keys)
            return Uploaded(uploaded=0)
        replaced = _trash(drive, [f.id for f in old if f.kind == "video"])

        def in_drive(now: FolderState) -> None:
            for reel in now.reels:
                if reel.key in landed:
                    reel.mark_uploaded(landed[reel.key].name)
                else:
                    reel.left_drive()
            now.report(None if replaced else DRIVE_PROBLEM)

        self._change(in_drive)
        return Uploaded(uploaded=len(landed))


def make_trials(*, user_id: int, refresh_token: str, login_id: int) -> Made | Requeued:
    return Folder(
        user_id=user_id, login_id=login_id, refresh_token=refresh_token
    ).make_trials()


def generate_reels(
    *,
    user_id: int,
    login_id: int,
    keys: list[str] | None = None,
    refresh_token: str = "",
) -> Made:
    return Folder(
        user_id=user_id, login_id=login_id, refresh_token=refresh_token
    ).generate_reels(keys)


def write_captions(
    *, user_id: int, login_id: int, key: str, refresh_token: str = ""
) -> Captioned:
    return Folder(
        user_id=user_id, login_id=login_id, refresh_token=refresh_token
    ).write_captions(key)


def upload_trials(
    *,
    user_id: int,
    refresh_token: str,
    login_id: int,
    account_folder_id: str,
    keys: list[str],
) -> Uploaded:
    return Folder(
        user_id=user_id, login_id=login_id, refresh_token=refresh_token
    ).upload_trials(account_folder_id, keys)
