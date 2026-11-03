"""The RQ jobs behind /trials (module-level: the worker imports them by
reference). Each takes reels of the folder's state through one step --
download the POSTED reels and run the snapchat pipeline on them with AI
captions, make one reel again, upload the approved copies into a TRIALS
folder next to POSTED -- and records each reel as it lands, so the page can
show each copy as soon as it is made. Jobs run side by side (a caption
applied while Generate trials runs): every write goes through
`state.change`, never a whole state held across a pipeline run. The page
shows the short `reel.error`/`state.problem` texts; the detail goes to the
log."""

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
from ofmhelpers.features.trials.captions import caption_for, understand
from ofmhelpers.features.trials.state import (
    CAPTION_MAX,
    FolderState,
    ReelState,
    at,
    change,
    change_reel,
    load,
    locked,
    work_dir,
)
from ofmhelpers.integrations.gdrive import DriveApi, DriveError
from ofmhelpers.log import get_logger
from ofmhelpers.media.repurpose.pipelines.snapchat_iphone16_usa import (
    run as pipeline_run,
)

logger = get_logger(__name__)

DRIVE_PROBLEM = "Google Drive did not answer. Connect it again."
DOWNLOAD_FAILED = "Could not download it from Drive"
MAKE_FAILED = "Could not make it"
UPLOAD_FAILED = "Could not upload it"


def _seed(key: str, made: int) -> int:
    """A reel's seed per make: Regenerate draws a new variant, a rerun of
    the same make draws the same one."""
    return zlib.crc32(f"{key}:{made}".encode()) & 0x7FFFFFFF


def _clean_caption(text: str) -> str:
    one_line = " ".join(text.split())
    return one_line.strip('"').strip()[:CAPTION_MAX]


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


def merge_posted(state: FolderState, videos: dict[str, str]) -> None:
    """`state` matched to POSTED's videos (id -> name): new ones added,
    ones gone dropped unless their copy was made."""
    state.reels = [r for r in state.reels if r.out or r.key in videos]
    known = {r.key for r in state.reels}
    state.reels += [
        ReelState(key=key, name=name)
        for key, name in videos.items()
        if key not in known
    ]


def _list_posted(drive: DriveApi, user_id: int, login_id: int) -> None:
    """POSTED as it is now into the state (the page shows the last sweep's
    read; a video posted since is picked up here)."""
    posted_id = load(user_id, login_id).posted_folder_id
    if not posted_id:
        return
    try:
        files = drive.children(posted_id)
    except DriveError:
        logger.warning("trials: POSTED of %s unreadable", login_id, exc_info=True)
        return
    videos = {f.id: f.name for f in files if f.kind == "video"}
    change(user_id, login_id, lambda s: merge_posted(s, videos))


def make_trials(*, user_id: int, refresh_token: str, login_id: int) -> dict:
    """Generate trials: POSTED read again, the reels not on disk downloaded,
    then every reel with no copy yet is made. Drive unreachable: what is on
    disk is still made, and the page says to connect Drive again."""
    found = _drive(refresh_token, user_id, login_id)
    if found is not None:
        _list_posted(found[0], user_id, login_id)
        _download(found[0], user_id, login_id)
    state = load(user_id, login_id)
    keys = [r.key for r in state.todo if _on_disk(user_id, login_id, r)]
    return generate_reels(user_id=user_id, login_id=login_id, keys=keys)


def generate_reels(
    *, user_id: int, login_id: int, keys: list[str] | None = None
) -> dict:
    """The snapchat pipeline on each downloaded reel (`keys`: only those),
    captioned by the AI the first time, then by the caption it has (the
    user's own, or the AI's: Regenerate never asks the AI again);
    `settings.trials.parallel` reels at once, each saved as it lands."""
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
        caption = reel.caption or _clean_caption(caption_for(understand(src), used=[]))
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
    change_reel(user_id, login_id, key, lambda r: r.mark_made(caption, out_rel))
    return True


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
) -> tuple[DriveApi, str, set[str]] | None:
    """The owner's Drive, TRIALS' id (remembered in the state) and the names
    already in it, or None with `state.problem` set."""
    found = _drive(refresh_token, user_id, login_id)
    if found is None:
        return None
    drive, root = found
    try:
        trials_id = _trials_folder(drive, root, user_id, login_id, account_folder_id)
        change(user_id, login_id, lambda s: s.remember_trials_folder(trials_id))
        taken = {f.name for f in drive.children(trials_id)}
    except DriveError:
        logger.warning("trials: TRIALS folder for %s failed", login_id, exc_info=True)
        change(user_id, login_id, lambda s: s.report(DRIVE_PROBLEM))
        return None
    return drive, trials_id, taken


def _upload_failed(user_id: int, login_id: int, key: str) -> None:
    change_reel(user_id, login_id, key, lambda r: r.upload_failed(UPLOAD_FAILED))


def upload_trials(
    *,
    user_id: int,
    refresh_token: str,
    login_id: int,
    account_folder_id: str,
    keys: list[str],
) -> dict:
    """The reels `keys` (claimed for this job by the page) into TRIALS, each
    under a fresh VID_XXXX.mp4. Uploads run side by side, each over its own
    reels; one that fails goes back to selectable and says so."""
    opened = _open_trials(refresh_token, user_id, login_id, account_folder_id)
    if opened is None:
        for key in keys:
            _upload_failed(user_id, login_id, key)
        return {"uploaded": 0}
    drive, trials_id, taken = opened
    rng = random.Random()  # noqa: S311 - a file name, not a secret
    done = 0
    state = load(user_id, login_id)
    for key in keys:
        reel = state.reel(key)
        path = at(user_id, login_id, reel.out) if reel else None
        if reel is None or path is None or not path.is_file():
            continue
        name = _camera_name(reel.name, taken, rng)
        try:
            drive.upload(trials_id, path, name, "video/mp4")
        except DriveError:
            logger.warning("trials: upload of %s failed", key, exc_info=True)
            _upload_failed(user_id, login_id, key)
            continue
        taken.add(name)
        change_reel(user_id, login_id, key, partial(ReelState.mark_uploaded, name=name))
        done += 1
    return {"uploaded": done}
