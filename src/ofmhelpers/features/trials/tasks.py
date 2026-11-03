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

import contextvars
import random
import zlib
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

from ofmhelpers.config import settings
from ofmhelpers.features.drive.folders import review_target
from ofmhelpers.features.previews.render import PREVIEW_ERRORS
from ofmhelpers.features.trials import media
from ofmhelpers.features.trials.captions import captions_for, understand
from ofmhelpers.features.trials.state import (
    FolderState,
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

logger = get_logger(__name__)

DRIVE_PROBLEM = "Google Drive did not answer. Connect it again."
DOWNLOAD_FAILED = "Could not download it from Drive"
MAKE_FAILED = "Could not make it"
CAPTIONS_FAILED = "Could not write captions"
UPLOAD_FAILED = "Could not upload it"
NOTHING_NEW = "No new reels in POSTED"


def _not_made(count: int) -> str:
    reels = "1 new reel" if count == 1 else f"{count} new reels"
    return f"Could not make {reels}. Click Generate trials to try again."


def _seed(key: str, made: int) -> int:
    """A reel's seed per make: Regenerate draws a new variant, a rerun of
    the same make draws the same one."""
    return zlib.crc32(f"{key}:{made}".encode()) & 0x7FFFFFFF


def _on_disk(user_id: int, login_id: int, reel: ReelState) -> bool:
    path = at(user_id, login_id, reel.local)
    return path is not None and path.is_file()


def _page_media(copy: Path) -> None:
    """The page's preview + poster now; a failure leaves them to the page's
    first request (`media` makes them on demand)."""
    try:
        media.preview(copy)
        media.poster(copy)
    except PREVIEW_ERRORS:
        logger.warning("trials: preview of %s failed", copy, exc_info=True)


def _drive(
    refresh_token: str, user_id: int, login_id: int
) -> tuple[DriveApi, str] | None:
    """The owner's Drive and the root it writes in (`review_target`: dev
    reading the live folders writes only in its own DEV root), or None
    with `state.problem` set."""
    try:
        found = review_target(refresh_token, user_id)
    except DriveError:
        logger.warning("trials: Drive of user %s unreachable", user_id, exc_info=True)
        change(user_id, login_id, lambda s: s.report(DRIVE_PROBLEM))
        return None
    change(user_id, login_id, lambda s: s.report(None))
    return found


def _download(drive: DriveApi, user_id: int, login_id: int) -> None:
    """Every reel not on disk yet, onto disk."""
    root = work_dir(user_id, login_id)
    for reel in load(user_id, login_id).reels:
        if _on_disk(user_id, login_id, reel):
            continue
        try:
            content, name = drive.download(reel.key)
            rel = f"src/{reel.key}-{Path(name).name}"
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_bytes(content)
        except (DriveError, OSError):
            logger.warning("trials: download of %s failed", reel.key, exc_info=True)
            failed = partial(ReelState.mark_failed, error=DOWNLOAD_FAILED)
            change_reel(user_id, login_id, reel.key, failed)
            continue
        change_reel(
            user_id, login_id, reel.key, partial(ReelState.mark_downloaded, local=rel)
        )


def merge_posted(state: FolderState, videos: list[DriveFile]) -> None:
    """`state` matched to POSTED's videos, newest first as Drive lists them:
    new ones added, ones gone dropped unless their copy was made, and all in
    the page's order, newest in POSTED first. The list may be the last sweep's,
    older than the state: a reel it does not name keeps its place."""
    known = {r.key: r for r in state.reels}
    listed = {f.id for f in videos}
    new = [
        ReelState(key=f.id, name=f.name, posted_at=f.created_time)
        for f in videos
        if f.id not in known
    ]
    for f in videos:
        if f.id in known and f.created_time:
            known[f.id].posted_at = f.created_time
    kept = [r for r in state.reels if r.out or r.key in listed]
    state.reels = sorted([*new, *kept], key=ReelState.posted_or_never, reverse=True)


def _list_posted(drive: DriveApi, user_id: int, login_id: int) -> bool:
    """POSTED as it is now into the state (the page shows the last sweep's
    read; a video posted since is picked up here). False when it was not read:
    nobody can say what is new in it."""
    posted_id = load(user_id, login_id).posted_folder_id
    if not posted_id:
        return False
    try:
        files = drive.children(posted_id)
    except DriveError:
        logger.warning("trials: POSTED of %s unreadable", login_id, exc_info=True)
        change(user_id, login_id, lambda s: s.report(DRIVE_PROBLEM))
        return False
    videos = [f for f in files if f.kind == "video"]
    change(user_id, login_id, lambda s: merge_posted(s, videos))
    return True


def make_trials(*, user_id: int, refresh_token: str, login_id: int) -> dict:
    """Generate trials: POSTED read again, the reels not on disk downloaded,
    then every reel with no copy yet is made; a copy already made is left
    alone. Drive unreachable: what is on disk is still made, and the page says
    to connect Drive again. The status line says what it left: nothing new in
    POSTED, or how many it could not make (the next click tries them again)."""
    found = _drive(refresh_token, user_id, login_id)
    read = found is not None and _list_posted(found[0], user_id, login_id)
    if found is not None:
        _download(found[0], user_id, login_id)
    state = load(user_id, login_id)
    keys = [r.key for r in state.todo if _on_disk(user_id, login_id, r)]
    if read and not state.todo:
        change(user_id, login_id, lambda s: s.tell(NOTHING_NEW))
    done = generate_reels(user_id=user_id, login_id=login_id, keys=keys)
    if left := len(load(user_id, login_id).todo):
        change(user_id, login_id, lambda s: s.tell(_not_made(left)))
    return done


def generate_reels(
    *, user_id: int, login_id: int, keys: list[str] | None = None
) -> dict:
    """The snapchat pipeline on each downloaded reel (`keys`: only those),
    the AI writing its captions the first time and the copy made with the
    first; then with the caption it has (one chosen, the user's own: Regenerate
    never asks the AI again); `settings.trials.parallel` reels at once, each
    saved as it lands."""
    state = load(user_id, login_id)
    wanted = [r.key for r in state.reels if keys is None or r.key in keys]
    workers = min(settings.trials.parallel, len(wanted)) or 1
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [
            pool.submit(contextvars.copy_context().run, _make, user_id, login_id, key)
            for key in wanted
        ]
        made = [f.result() for f in futures]
    return {"generated": made.count(True), "failed": made.count(False)}


def _make(user_id: int, login_id: int, key: str) -> bool:
    """One reel's copy (and the page's preview of it), from the reel as it
    is now (a caption applied meanwhile counts), into a temp file then over
    the old copy, so the page never plays a half-written one. True when
    made; the reel is recorded either way."""
    reel = load(user_id, login_id).reel(key)
    if reel is None:
        return False
    root = work_dir(user_id, login_id)
    out_rel = f"out/{key}.mp4"
    out = root / out_rel
    making = out.with_name(f".{key}.making.mp4")
    try:
        if not _on_disk(user_id, login_id, reel):
            raise FileNotFoundError(reel.local)  # noqa: TRY301 - same handling
        src = root / str(reel.local)
        choices = reel.captions or ([] if reel.caption else _written(src))
        caption = reel.caption or choices[0]
        making.parent.mkdir(parents=True, exist_ok=True)
        pipeline_run(src, making, caption=caption, seed=_seed(key, reel.made))
        making.replace(out)
        _page_media(out)
    except Exception:  # one bad reel must not sink the batch
        logger.warning("trials: making %s failed", key, exc_info=True)
        making.unlink(missing_ok=True)
        error = MAKE_FAILED if reel.local else DOWNLOAD_FAILED
        change_reel(user_id, login_id, key, lambda r: r.mark_failed(error))
        return False
    change_reel(
        user_id, login_id, key, lambda r: r.mark_made(caption, out_rel, choices)
    )
    return True


def _written(src: Path) -> list[str]:
    """The AI watches the reel and writes its captions."""
    return captions_for(understand(src))


def write_captions(*, user_id: int, login_id: int, key: str) -> dict:
    """Captions to choose from for a reel made before there were any; its copy
    stays as it is until one is chosen."""
    reel = load(user_id, login_id).reel(key)
    try:
        if reel is None or not _on_disk(user_id, login_id, reel):
            raise FileNotFoundError(key)  # noqa: TRY301 - same handling
        choices = _written(work_dir(user_id, login_id) / str(reel.local))
    except Exception:  # the page says it short; the copy is untouched
        logger.warning("trials: captions of %s failed", key, exc_info=True)
        change_reel(
            user_id, login_id, key, lambda r: r.captions_failed(CAPTIONS_FAILED)
        )
        return {"captions": 0}
    change_reel(user_id, login_id, key, lambda r: r.mark_captions(choices))
    return {"captions": len(choices)}


# Long enough for three Drive calls; two uploads starting together must
# not each make a TRIALS folder.
_FOLDER_LOCK_S = 60


def _trials_folder(
    drive: DriveApi, root: str, user_id: int, login_id: int, account_folder_id: str
) -> str:
    """TRIALS next to POSTED in her folder. Dev reading the live folders
    (`review_target`'s root is its DEV root, her folder is not in it): in a
    folder of the same name in the DEV root instead, so dev tests the whole
    loop and never writes a live one. Found again every run: the scoped
    Drive may write only into folders it found or made itself."""
    with locked(user_id, login_id, "trials-folder", _FOLDER_LOCK_S):
        account = drive.folder(account_folder_id)
        parent = (
            account.id
            if root in account.parents
            else drive.ensure_folder(root, account.name).id
        )
        return drive.ensure_folder(parent, "TRIALS").id


def _camera_name(source: str, taken: set[str], rng: random.Random) -> str:
    """A camera-roll name, VID_XXXX.mp4: never the source's own number,
    never one already in TRIALS."""
    while True:
        name = f"VID_{rng.randint(1000, 9999)}.mp4"
        if name != source and name not in taken:
            return name


def _open_trials(
    refresh_token: str, user_id: int, login_id: int, account_folder_id: str
) -> tuple[DriveApi, str, list[DriveFile]] | None:
    """The owner's Drive, TRIALS' id (remembered in the state) and what is
    already in it, or None with `state.problem` set."""
    found = _drive(refresh_token, user_id, login_id)
    if found is None:
        return None
    drive, root = found
    try:
        trials_id = _trials_folder(drive, root, user_id, login_id, account_folder_id)
        change(user_id, login_id, lambda s: s.remember_trials_folder(trials_id))
        return drive, trials_id, drive.children(trials_id)
    except DriveError:
        logger.warning("trials: TRIALS folder for %s failed", login_id, exc_info=True)
        change(user_id, login_id, lambda s: s.report(DRIVE_PROBLEM))
        return None


def _batch_failed(user_id: int, login_id: int, keys: list[str]) -> None:
    def back(state: FolderState) -> None:
        for reel in state.reels:
            if reel.key in keys:
                reel.upload_failed(UPLOAD_FAILED)

    change(user_id, login_id, back)


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


def upload_trials(
    *,
    user_id: int,
    refresh_token: str,
    login_id: int,
    account_folder_id: str,
    keys: list[str],
) -> dict:
    """The reels `keys` (claimed for this job by the page) replace the videos
    in TRIALS, each under a fresh VID_XXXX.mp4. All or nothing, new ones first:
    one that fails takes back those it went up with, what was in TRIALS stays,
    and the batch is selected again. Only then the old ones go to Drive's trash,
    and their reels can be selected again."""
    opened = _open_trials(refresh_token, user_id, login_id, account_folder_id)
    if opened is None:
        _batch_failed(user_id, login_id, keys)
        return {"uploaded": 0}
    drive, trials_id, old = opened
    rng = random.Random()  # noqa: S311 - a file name, not a secret
    taken = {f.name for f in old}
    state = load(user_id, login_id)
    landed: dict[str, DriveFile] = {}
    try:
        for key in keys:
            reel = state.reel(key)
            path = at(user_id, login_id, reel.out) if reel else None
            if reel is None or path is None or not path.is_file():
                raise FileNotFoundError(key)
            name = _camera_name(reel.name, taken, rng)
            landed[key] = drive.upload(trials_id, path, name, "video/mp4")
            taken.add(name)
    except (DriveError, OSError):
        logger.warning("trials: upload to %s failed", login_id, exc_info=True)
        _trash(drive, [f.id for f in landed.values()])
        _batch_failed(user_id, login_id, keys)
        return {"uploaded": 0}
    replaced = _trash(drive, [f.id for f in old if f.kind == "video"])

    def in_drive(now: FolderState) -> None:
        for reel in now.reels:
            if reel.key in landed:
                reel.mark_uploaded(landed[reel.key].name)
            else:
                reel.left_drive()
        now.report(None if replaced else DRIVE_PROBLEM)

    change(user_id, login_id, in_drive)
    return {"uploaded": len(landed)}
