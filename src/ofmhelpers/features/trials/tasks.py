"""The RQ jobs behind /trials (module-level: the worker imports them by
reference). Each takes reels of the folder's state through one step --
download the POSTED reels and run the snapchat pipeline on them with AI
captions, make one reel again, write one reel's captions, replace what is in
the TRIALS folder next to POSTED with the approved copies -- and records each
reel as it lands, so the page can show each copy as soon as it is made. Jobs
run side by side (a caption applied while Generate trials runs): every write
goes through `state.change`, never a whole state held across a pipeline run.
The page shows the short `reel.error`/`state.problem` texts; the detail goes to
the log."""

import random
import zlib
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any
lazy from collections.abc import Callable

from pydantic import BaseModel, SkipValidation

from ofmhelpers.features.drive.folders import ReviewPlace, review_target
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
lazy from ofmhelpers.media.repurpose.repurpose import RunResult

logger = get_logger(__name__)

DRIVE_PROBLEM = "Google Drive did not answer. Connect it again."
DOWNLOAD_FAILED = "Could not download it from Drive"
MAKE_FAILED = "Could not make it"
CAPTIONS_FAILED = "Could not write captions"
UPLOAD_FAILED = "Could not upload it"
NOTHING_NEW = "No new reels in POSTED"
SOURCE_GONE = "Source video is gone from Drive"
# The pipeline a copy is made with, kept in its recipe.
PIPELINE = "snapchat_iphone16_usa"


class Made(BaseModel):
    """What a making job did: its result in the jobs table."""

    generated: int
    failed: int


class Captioned(BaseModel):
    captions: int


class Uploaded(BaseModel):
    uploaded: int


def _not_made(count: int) -> str:
    reels = "1 new reel" if count == 1 else f"{count} new reels"
    return f"Could not make {reels}. Click Generate trials to try again."


def _seed(key: str, made: int) -> int:
    """A reel's seed per make: Regenerate draws a new variant, a rerun of
    the same make draws the same one."""
    return zlib.crc32(f"{key}:{made}".encode()) & 0x7FFFFFFF


def _page_media(copy: Path) -> None:
    """The page's preview + poster now; a failure leaves them to the page's
    first request (`media` makes them on demand)."""
    try:
        media.preview(copy)
        media.poster(copy)
    except PREVIEW_ERRORS:
        logger.warning("trials: preview of %s failed", copy, exc_info=True)


def merge_posted(state: FolderState, videos: list[DriveFile]) -> None:
    """`state` matched to POSTED's videos, newest first as Drive lists them:
    new ones added, ones gone dropped unless their copy was made, and all in
    the page's order, newest in POSTED first. The list may be the last sweep's,
    older than the state: a reel it does not name keeps its place."""
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
    """Everything the pipeline drew for the copy, without its ffmpeg command
    (paths of a temp folder)."""
    if ran is None or ran.step is None:
        return None
    return {
        **ran.step.model_dump(mode="json", exclude={"command"}),
        "seconds": ran.seconds,
    }


def _recipe(reel: ReelState) -> Recipe:
    """What made the copy the reel has (one from before recipes were kept:
    its seed worked out again)."""
    return reel.recipe or Recipe(
        seed=_seed(reel.key, reel.made - 1),
        caption=reel.caption or "",
        pipeline=PIPELINE,
    )


def _written(src: Path) -> list[str]:
    """The AI watches the reel and writes its captions."""
    return captions_for(understand(src))


# Long enough for three Drive calls; two uploads starting together must
# not each make a TRIALS folder.
_FOLDER_LOCK_S = 60


def _camera_name(source: str, taken: set[str], rng: random.Random) -> str:
    """A camera-roll name, VID_XXXX.mp4: never the source's own number,
    never one already in TRIALS."""
    while True:
        name = f"VID_{rng.randint(1000, 9999)}.mp4"
        if name != source and name not in taken:
            return name


def _trash(drive: DriveApi, file_ids: list[str]) -> bool:
    """Into Drive's trash (restorable there for 30 days); True when all went."""
    fine = True
    for file_id in file_ids:
        try:
            drive.trash_file(file_id)
        except FolderGoneError:
            continue  # gone already: what was wanted
        except DriveError:
            logger.warning("trials: trashing %s failed", file_id, exc_info=True)
            fine = False
    return fine


class _Trials(BaseModel, frozen=True, arbitrary_types_allowed=True):
    """The TRIALS folder in Drive, opened: its id and what is already in it."""

    drive: SkipValidation[DriveApi]
    folder_id: str
    files: list[DriveFile]


@dataclass(frozen=True)
class Folder:
    """One login's folder as its owner works on it: every step of the jobs
    below, on that folder (`user_id`'s, of `login_id`), through the owner's
    Drive (`refresh_token`; "" = no Drive, what is on disk only)."""

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
        """The owner's Drive and the root it writes in (`review_target`: dev
        reading the live folders writes only in its own DEV root), or None
        with `state.problem` set."""
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
        """A reel's source from Drive onto disk: its path in the work dir."""
        got = drive.download(key)
        rel = f"src/{key}-{Path(got.name).name}"
        path = work_dir(self.user_id, self.login_id) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(got.content)
        return rel

    def _source(self, reel: ReelState) -> Path:
        """The reel's source on disk; one deleted since (`cleanup`: a day old)
        is downloaded again, its Drive id being the reel's key."""
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
        """Every reel not on disk yet, onto disk; not one whose copy is made
        (its source comes back only to make it again)."""
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
        """POSTED as it is now into the state (the page shows the last sweep's
        read; a video posted since is picked up here). False when it was not
        read: nobody can say what is new in it."""
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

    def make_trials(self) -> Made:
        """Generate trials: POSTED read again, the reels not on disk downloaded,
        then every reel with no copy yet is made; a copy already made is left
        alone. Drive unreachable: what is on disk is still made, and the page
        says to connect Drive again. The status line says what it left: nothing
        new in POSTED, or how many it could not make (the next click tries them
        again)."""
        found = self._drive()
        read = found is not None and self._list_posted(found.drive)
        if found is not None:
            self._download(found.drive)
        state = self._load()
        keys = [r.key for r in state.todo if self._on_disk(r)]
        if read and not state.todo:
            self._change(lambda s: s.tell(NOTHING_NEW))
        done = self.generate_reels(keys)
        if left := len(self._load().todo):
            self._change(lambda s: s.tell(_not_made(left)))
        return done

    def generate_reels(self, keys: list[str] | None = None) -> Made:
        """The snapchat pipeline on each downloaded reel (`keys`: only those),
        the AI writing its captions the first time and the copy made with the
        first; then with the caption it has (one chosen, the user's own:
        Regenerate never asks the AI again); one after another, each saved as
        it lands."""
        state = self._load()
        wanted = [r.key for r in state.reels if keys is None or r.key in keys]
        made = [self.make(key) for key in wanted]
        return Made(generated=made.count(True), failed=made.count(False))

    def make(self, key: str, *, restore: bool = False) -> bool:
        """One reel's copy (and the page's preview of it), from the reel as it
        is now (a caption applied meanwhile counts), into a temp file then over
        the old copy, so the page never plays a half-written one. `restore`: the
        copy it had, deleted since, again from its recipe (for its upload). True
        when made; the reel is recorded either way."""
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
            ran = pipeline_run(src, making, caption=recipe.caption, seed=recipe.seed)
            if not restore:
                recipe = recipe.model_copy(update={"draws": _draws(ran)})
            making.replace(out)
            _page_media(out)
        except Exception as exc:  # one bad reel must not sink the batch
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
        """Captions to choose from for a reel made before there were any; its
        copy stays as it is until one is chosen."""
        reel = self._load().reel(key)
        try:
            if reel is None:
                raise FileNotFoundError(key)  # noqa: TRY301 - same handling
            choices = _written(self._source(reel))
        except Exception:  # the page says it short; the copy is untouched
            logger.warning("trials: captions of %s failed", key, exc_info=True)
            self._change_reel(key, lambda r: r.captions_failed(CAPTIONS_FAILED))
            return Captioned(captions=0)
        self._change_reel(key, lambda r: r.mark_captions(choices))
        return Captioned(captions=len(choices))

    def _trials_folder(self, place: ReviewPlace, account_folder_id: str) -> str:
        """TRIALS next to POSTED in her folder. Dev reading the live folders
        (`review_target`'s root is its DEV root, her folder is not in it): in a
        folder of the same name in the DEV root instead, so dev tests the whole
        loop and never writes a live one. Found again every run: the scoped
        Drive may write only into folders it found or made itself."""
        drive, root = place.drive, place.folder_id
        with locked(self.user_id, self.login_id, "trials-folder", _FOLDER_LOCK_S):
            account = drive.folder(account_folder_id)
            parent = (
                account.id
                if root in account.parents
                else drive.ensure_folder(root, account.name).id
            )
            return drive.ensure_folder(parent, "TRIALS").id

    def _open_trials(self, account_folder_id: str) -> _Trials | None:
        """The owner's Drive, TRIALS' id (remembered in the state) and what is
        already in it, or None with `state.problem` set."""
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
        """Of `keys`, the reels whose copy is on disk: one deleted since
        (`cleanup`) is made again from its recipe first; one that cannot be is
        left out, saying why."""
        state = self._load()
        ready = []
        for key in keys:
            reel = state.reel(key)
            path = at(self.user_id, self.login_id, reel.out) if reel else None
            if (path is not None and path.is_file()) or self.make(key, restore=True):
                ready.append(key)
        return ready

    def upload_trials(self, account_folder_id: str, keys: list[str]) -> Uploaded:
        """The reels `keys` (claimed for this job by the page) replace the
        videos in TRIALS, each under a fresh VID_XXXX.mp4. All or nothing, new
        ones first: one that fails takes back those it went up with, what was
        in TRIALS stays, and the batch is selected again. Only then the old
        ones go to Drive's trash, and their reels can be selected again."""
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


# --- the jobs: what RQ imports by name, each one a step of `Folder` -----------


def make_trials(*, user_id: int, refresh_token: str, login_id: int) -> Made:
    """Generate trials (`Folder.make_trials`)."""
    return Folder(user_id, login_id, refresh_token).make_trials()


def generate_reels(
    *,
    user_id: int,
    login_id: int,
    keys: list[str] | None = None,
    refresh_token: str = "",
) -> Made:
    """Make these reels' copies (`Folder.generate_reels`)."""
    return Folder(user_id, login_id, refresh_token).generate_reels(keys)


def write_captions(
    *, user_id: int, login_id: int, key: str, refresh_token: str = ""
) -> Captioned:
    """Captions for one reel (`Folder.write_captions`)."""
    return Folder(user_id, login_id, refresh_token).write_captions(key)


def upload_trials(
    *,
    user_id: int,
    refresh_token: str,
    login_id: int,
    account_folder_id: str,
    keys: list[str],
) -> Uploaded:
    """Replace TRIALS with these reels (`Folder.upload_trials`)."""
    return Folder(user_id, login_id, refresh_token).upload_trials(
        account_folder_id, keys
    )
