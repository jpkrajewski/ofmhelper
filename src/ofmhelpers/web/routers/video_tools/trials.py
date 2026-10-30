"""/trials: trial reels from a model's POSTED folder. The same phone cards
as /drive; Generate trials downloads her POSTED reels and runs the snapchat
pipeline on each with an AI-written caption (watch reel -> transcribe/
describe -> one viral caption). The phone opens the folder: the copies as
they are made, fix a caption, Regenerate (pipeline only), select the good
ones, Upload files them into a TRIALS folder next to POSTED in her Drive
folder.

The folder page is driven by trials.js: every action is a fetch answered
at once (JSON), the work is a job, the page polls `/live` (one small JSON:
each card's stamp) and swaps in only the cards that changed (`/cards`).
No request here waits on Drive or ffmpeg except the media files. Jobs run
side by side; a reel being made or uploaded is the only thing blocked.
Both roles; a user only ever sees their own phones' folders (the same scope
as /drive)."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse

from ofmhelpers.cache import enqueue
from ofmhelpers.log import get_logger
from ofmhelpers.trials import media as trial_media
from ofmhelpers.trials import state as trial_state
from ofmhelpers.trials import tasks
from ofmhelpers.web import drive_folders
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.previews import PREVIEW_ERRORS
from ofmhelpers.web.schemas.drive import DriveCard
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job
from ofmhelpers.web.stores.users import refresh_token
from ofmhelpers.web.templates_config import render

logger = get_logger(__name__)

router = APIRouter(prefix="/trials", tags=["trials"])

TASK = "trials"

# Seconds per reel a job may take (pipeline encode + the LLM, an upload).
_PER_REEL_S = 240
_JOB_GRACE_S = 300


def _actor(request: Request) -> tuple[int, int | None]:
    """(user id, /drive scope) of the session."""
    user = AuthMiddleware.user(request)
    return user.id, AuthMiddleware.scope(request)


def _card(scope: int | None, viewer: int, login_id: int) -> DriveCard:
    """The login's /drive card from the last sweep's read (no Drive call:
    the page answers at once; Generate trials lists POSTED itself). 404
    when the login is not one of the viewer's."""
    board = drive_folders.board(owner=scope, viewer=viewer, live=False)
    card = next((c for c in board.cards if c.target.login_id == login_id), None)
    if card is None:
        raise HTTPException(404, "No such account folder")
    return card


def _synced(card: DriveCard, state: trial_state.FolderState) -> None:
    """`state` matched to the card's POSTED videos: new ones added, ones
    gone (or never videos) dropped unless already made."""
    state.account_folder_id = card.account_folder_id or ""
    state.posted_folder_id = card.posted_folder_id or ""
    if not card.posted_folder_id or card.error or card.unread:
        return
    videos = {f.id: f.name for f in card.scan.posted if f.kind == "video"}
    tasks.merge_posted(state, videos)


def _sync_state(
    scope: int | None, viewer: int, login_id: int
) -> tuple[DriveCard, trial_state.FolderState]:
    """The login's card and its state matched to POSTED (written only when
    that changed something)."""
    card = _card(scope, viewer, login_id)
    state = trial_state.load(viewer, login_id)
    before = state.model_dump_json()
    _synced(card, state)
    if state.model_dump_json() != before:
        state = trial_state.change(viewer, login_id, lambda s: _synced(card, s))
    return card, state


def _at_work(uid: int, job_id: str | None) -> bool:
    if not job_id:
        return False
    job = get_job(job_id, str(uid))
    return job is not None and job.status == "running"


class _Jobs:
    """Which of a folder's jobs are at work: Generate trials and each reel's
    own (Apply caption / Regenerate / its upload)."""

    def __init__(self, uid: int, state: trial_state.FolderState) -> None:
        ids = {state.job_id, *(r.job_id for r in state.reels)}
        self.active = {i for i in ids if i and _at_work(uid, i)}
        self.making = state.job_id in self.active
        self.busy = {r.key for r in state.reels if r.job_id in self.active}
        self.uploading = {
            r.key for r in state.reels if r.key in self.busy and r.status == "uploading"
        }


def _pending(
    state: trial_state.FolderState, jobs: _Jobs
) -> list[trial_state.ReelState]:
    """The reels Generate trials is still making (shown as generating)."""
    if not jobs.making:
        return []
    return [r for r in state.todo if r.status != "failed"]


def _stamp(reel: trial_state.ReelState, jobs: _Jobs) -> str:
    """Changes whenever the reel's card would look different."""
    busy = "uploading" if reel.key in jobs.uploading else reel.key in jobs.busy
    return f"{reel.made}.{reel.status}.{busy}.{reel.approved}.{reel.error or ''}"


def _enqueue(uid: int, login_id: int, job_id: str, fn, reels: int, **kwargs) -> None:
    enqueue(
        run_job,
        job_id,
        fn,
        {"user_id": uid, "login_id": login_id, **kwargs},
        job_timeout=_JOB_GRACE_S + _PER_REEL_S * max(1, reels),
    )


def _new_job(uid: int, login_id: int, label: str) -> str:
    return create_job(TASK, {"label": label, "login": login_id}, actor=str(uid))


@router.get("")
def trials_page(request: Request):
    uid, scope = _actor(request)
    board = drive_folders.board(owner=scope, viewer=uid, live=False)
    made = {
        c.target.login_id: len(trial_state.load(uid, c.target.login_id).ready)
        for c in board.cards
    }
    return render(request, "trials.html", board=board, made=made)


@router.get("/{login_id}")
def folder_page(request: Request, login_id: int):
    uid, scope = _actor(request)
    card, state = _sync_state(scope, uid, login_id)
    jobs = _Jobs(uid, state)
    return render(
        request,
        "trials_folder.html",
        card=card,
        state=state,
        jobs=jobs,
        pending=_pending(state, jobs),
        stamps={r.key: _stamp(r, jobs) for r in state.reels},
    )


@router.get("/{login_id}/live")
def live(request: Request, login_id: int) -> dict:
    """What the page polls: whether anything runs, the status line, and
    each card in page order with its stamp ("pending-<key>" for one still
    being made by Generate trials)."""
    uid, _scope = _actor(request)
    state = trial_state.load(uid, login_id)
    jobs = _Jobs(uid, state)
    cards = [{"key": r.key, "stamp": _stamp(r, jobs)} for r in state.ready]
    cards += [{"key": f"pending-{r.key}", "stamp": ""} for r in _pending(state, jobs)]
    status = ""
    if jobs.making:
        status = f"Making trials… {len(state.ready)} of {len(state.reels)} ready"
    elif jobs.uploading:
        status = f"Uploading {len(jobs.uploading)} to Drive…"
    elif jobs.busy:
        status = f"Making {len(jobs.busy)}…"
    return {
        "running": bool(jobs.active),
        "making": jobs.making,
        "todo": len(state.todo),
        "status": status,
        "problem": state.problem or "",
        "trials_url": state.trials_url or "",
        "cards": cards,
    }


@router.get("/{login_id}/cards")
def cards(
    request: Request, login_id: int, key: Annotated[list[str] | None, Query()] = None
):
    """The cards `key` as the page renders them, for trials.js to swap in."""
    uid, scope = _actor(request)
    card = _card(scope, uid, login_id)
    state = trial_state.load(uid, login_id)
    jobs = _Jobs(uid, state)
    wanted = set(key or [])
    return render(
        request,
        "_trials_cards.html",
        t=card.target,
        reels=[r for r in state.ready if r.key in wanted],
        pending=[r for r in _pending(state, jobs) if f"pending-{r.key}" in wanted],
        jobs=jobs,
        stamps={r.key: _stamp(r, jobs) for r in state.reels},
    )


@router.post("/{login_id}/generate")
def generate(request: Request, login_id: int) -> dict:
    """Download what is not on disk yet, make every copy not made yet; a
    second click while it runs does nothing."""
    uid, scope = _actor(request)
    _sync_state(scope, uid, login_id)
    claimed: list[str] = []

    def claim(s: trial_state.FolderState) -> None:
        if not _at_work(uid, s.job_id):
            s.job_id = _new_job(uid, login_id, "Making trials")
            claimed.append(s.job_id)

    state = trial_state.change(uid, login_id, claim)
    if claimed:
        _enqueue(
            uid,
            login_id,
            claimed[0],
            tasks.make_trials,
            len(state.reels),
            refresh_token=refresh_token(uid) or "",
        )
    return {"started": bool(claimed)}


def _remake(uid: int, login_id: int, key: str, caption: str | None = None) -> bool:
    """One reel made again as its own job, alongside whatever else runs
    (with `caption` first, when given). Checked and claimed in one locked
    write: ten clicks at once start one job, the rest do nothing."""
    claimed: list[str] = []

    def claim(reel: trial_state.ReelState) -> None:
        if _at_work(uid, reel.job_id):
            return
        reel.job_id = _new_job(uid, login_id, "Making one trial")
        reel.error = None
        if caption is not None:
            reel.caption = caption
        claimed.append(reel.job_id)

    state = trial_state.change_reel(uid, login_id, key, claim)
    if state.reel(key) is None:
        raise HTTPException(404, "No such reel")
    if claimed:
        _enqueue(uid, login_id, claimed[0], tasks.generate_reels, 1, keys=[key])
    return bool(claimed)


@router.post("/{login_id}/reel/{key}/regenerate")
def regenerate(request: Request, login_id: int, key: str) -> dict:
    """The pipeline again on one reel, a new variant with its caption as it
    is: no AI call (its daily quota is small)."""
    uid, _ = _actor(request)
    return {"started": _remake(uid, login_id, key)}


@router.post("/{login_id}/reel/{key}/caption")
def set_caption(
    request: Request,
    login_id: int,
    key: str,
    caption: Annotated[str, Form()],
) -> dict:
    """The user's own caption: saved, then the reel is made again with it."""
    uid, _ = _actor(request)
    return {"started": _remake(uid, login_id, key, " ".join(caption.split())[:80])}


@router.post("/{login_id}/reel/{key}/select")
def select(
    request: Request,
    login_id: int,
    key: str,
    selected: Annotated[bool, Form()] = False,
) -> dict:
    """Select / unselect, kept at once."""
    uid, _ = _actor(request)

    def pick(reel: trial_state.ReelState) -> None:
        reel.approved = selected

    state = trial_state.change_reel(uid, login_id, key, pick)
    if state.reel(key) is None:
        raise HTTPException(404, "No such reel")
    return {"selected": selected}


@router.post("/{login_id}/upload")
def upload(
    request: Request,
    login_id: int,
    approved: Annotated[list[str] | None, Form()] = None,
) -> dict:
    """The selected copies into TRIALS: each claimed for this upload in one
    locked write (one being made or uploaded already is left out), so a
    second Upload while the first runs takes the other reels."""
    uid, _ = _actor(request)
    wanted = set(approved or [])
    claimed: list[str] = []
    job: list[str] = []

    def claim(s: trial_state.FolderState) -> None:
        if not s.account_folder_id:
            return
        for reel in s.ready:
            if reel.key not in wanted or reel.status == "uploaded":
                continue
            if _at_work(uid, reel.job_id):
                continue
            if not job:
                job.append(_new_job(uid, login_id, "Uploading"))
            reel.job_id, reel.status, reel.error = job[0], "uploading", None
            reel.approved = False
            claimed.append(reel.key)

    state = trial_state.change(uid, login_id, claim)
    if job:
        _enqueue(
            uid,
            login_id,
            job[0],
            tasks.upload_trials,
            len(claimed),
            refresh_token=refresh_token(uid) or "",
            account_folder_id=state.account_folder_id,
            keys=claimed,
        )
    return {"uploading": claimed}


def _copy(uid: int, login_id: int, key: str) -> Path:
    state = trial_state.load(uid, login_id)
    reel = state.reel(key)
    path = trial_state.at(uid, login_id, reel.out) if reel else None
    if path is None or not path.is_file():
        raise HTTPException(404, "Not generated yet")
    return path


@router.get("/files/{login_id}/{key}")
def video(request: Request, login_id: int, key: str):
    """The copy as the page plays it (H.264, `trials.media`); the copy
    itself when no preview can be made."""
    uid, _ = _actor(request)
    copy = _copy(uid, login_id, key)
    try:
        return FileResponse(trial_media.preview(copy), media_type="video/mp4")
    except PREVIEW_ERRORS:
        logger.warning("trials: preview of %s failed", copy, exc_info=True)
        return FileResponse(copy, media_type="video/mp4")


@router.get("/files/{login_id}/{key}/poster")
def poster(request: Request, login_id: int, key: str):
    uid, _ = _actor(request)
    copy = _copy(uid, login_id, key)
    try:
        return FileResponse(trial_media.poster(copy), media_type="image/webp")
    except PREVIEW_ERRORS as exc:
        logger.warning("trials: poster of %s failed", copy, exc_info=True)
        raise HTTPException(404, "No poster") from exc
