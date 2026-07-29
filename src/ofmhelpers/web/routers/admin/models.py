"""
ofmhelpers/web/routers/admin/models.py

Admin-only roster of Models: name + profile picture + one OnlyFans link +
many Instagram account links (each carrying optional owner/phone/SIM/
password/email details) + many free-form contacts (type + value). Gated via
require_admin like file_manager.py and action_log.py -- nobody but an admin
needs to touch this roster.
"""

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import FileResponse, RedirectResponse
from PIL import Image

from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
from ofmhelpers.scraping.instagram_stats_job import (
    active_sweep_id,
    collect_all_instagram_stats,
)
from ofmhelpers.web.auth import require_admin
from ofmhelpers.web.queue import enqueue, get_queue
from ofmhelpers.web.routers.refs import write_image_thumb
from ofmhelpers.web.routers.task_helpers import (
    IMAGE_KINDS,
    media_response,
    require_upload_kind,
)
from ofmhelpers.web.stores import instagram_stats
from ofmhelpers.web.stores import models as models_store
from ofmhelpers.web.templates_config import templates

logger = get_logger(__name__)

router = APIRouter(
    prefix="/models", tags=["models"], dependencies=[Depends(require_admin)]
)

# Where profile pictures live, one subdirectory per model.
PICTURE_ROOT = Path("uploads") / "model_pictures"


def _save_picture(model_id: str, file: UploadFile) -> str:
    name = require_upload_kind(file.filename, IMAGE_KINDS)
    picture_dir = PICTURE_ROOT / model_id
    if picture_dir.is_dir():
        shutil.rmtree(picture_dir)
    picture_dir.mkdir(parents=True, exist_ok=True)
    dest = picture_dir / name
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return str(dest)


@router.get("")
def list_page(request: Request):
    models = models_store.list_models()
    account_ids = [a["id"] for m in models for a in m["instagram_accounts"]]
    stats = instagram_stats.get_stats_many(account_ids)
    return templates.TemplateResponse(
        request, "models_list.html", {"models": models, "instagram_stats": stats}
    )


@router.post("/refresh-stats")
def refresh_stats():
    """Enqueues the same sweep the nightly scheduler runs (worker.py), for
    an immediate refresh instead of waiting for the next run.

    Answers JSON, not a redirect: the page polls `/refresh-stats/{job_id}`
    and swaps in `/stats-html` when the sweep lands, so the admin gets
    progress instead of a reloaded page that still shows yesterday's
    numbers. `job_id` is null in synchronous mode (the test suite), where
    enqueue() already ran the sweep inline and there is nothing to poll.

    A sweep that's already queued or running is reused rather than doubled:
    two browsers scraping the same accounts at once only buys a soft block
    from Instagram."""
    if settings.infra.rq_async:
        running = active_sweep_id()
        if running is not None:
            return {"job_id": running}
    job = enqueue(collect_all_instagram_stats)
    return {"job_id": getattr(job, "id", None)}


@router.get("/refresh-stats")
def refresh_stats_active():
    """The sweep runs on the worker, so it survives the admin closing the
    tab -- but the "scraping..." indicator doesn't. The page asks this on
    load and picks the polling back up, instead of looking idle while a
    sweep is still writing rows."""
    if not settings.infra.rq_async:
        return {"job_id": None}
    return {"job_id": active_sweep_id()}


@router.get("/refresh-stats/{job_id}")
def refresh_stats_status(job_id: str):
    """Progress for one enqueued sweep. An unknown id reads as "finished":
    RQ drops finished jobs after its result TTL, and a job that has aged out
    is done by definition -- reporting "failed" there would cry wolf on
    every slow page."""
    job = get_queue().fetch_job(job_id)
    if job is None:
        return {"status": "finished", "error": None}

    status = job.get_status()
    status = getattr(status, "value", status)  # JobStatus enum -> "finished"
    # Only the exception's last line: the whole traceback is in the worker
    # log, and this string ends up in a one-line status banner.
    error = None
    if status == "failed" and job.exc_info:
        error = job.exc_info.strip().splitlines()[-1]
    return {"status": status, "error": error}


@router.get("/stats-html")
def stats_html(request: Request):
    """The stats blocks alone, same markup the full page renders (shared
    macro), for the refresh button to swap in place."""
    models = models_store.list_models()
    account_ids = [a["id"] for m in models for a in m["instagram_accounts"]]
    stats = instagram_stats.get_stats_many(account_ids)
    return templates.TemplateResponse(
        request,
        "models_stats_fragment.html",
        {"models": models, "instagram_stats": stats},
    )


@router.get("/new")
def new_page(request: Request):
    return templates.TemplateResponse(request, "models_new.html", {})


@router.post("/add")
def add(
    name: Annotated[str, Form()],
    onlyfans_url: Annotated[str, Form()] = "",
    profile_picture: Annotated[UploadFile | None, File()] = None,
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    has_picture = profile_picture is not None and profile_picture.filename
    if has_picture:
        # Reject an unusable picture before the row exists -- validating after
        # add_model leaves a pictureless orphan behind on the 400.
        require_upload_kind(profile_picture.filename, IMAGE_KINDS)

    model = models_store.add_model(name.strip(), onlyfans_url.strip())
    if has_picture:
        path = _save_picture(model["id"], profile_picture)
        models_store.set_profile_picture(model["id"], path, Path(path).name)

    return RedirectResponse(url="/models", status_code=303)


@router.get("/{model_id}/edit")
def edit_page(request: Request, model_id: str):
    model = models_store.get_model(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return templates.TemplateResponse(request, "models_edit.html", {"model": model})


@router.post("/{model_id}/update")
def update(
    model_id: str,
    name: Annotated[str, Form()],
    onlyfans_url: Annotated[str, Form()] = "",
):
    if not name.strip():
        raise HTTPException(status_code=400, detail="Name is required")
    if not models_store.update_model(model_id, name.strip(), onlyfans_url.strip()):
        raise HTTPException(status_code=404, detail="Model not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id}/picture")
def upload_picture(model_id: str, profile_picture: Annotated[UploadFile, File()]):
    if models_store.get_model(model_id) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    if not profile_picture.filename:
        raise HTTPException(status_code=400, detail="A file is required")

    path = _save_picture(model_id, profile_picture)
    models_store.set_profile_picture(model_id, path, Path(path).name)
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


def _picture_path(model_id: str) -> Path:
    model = models_store.get_model(model_id)
    if model is None or not model.get("profile_picture_path"):
        raise HTTPException(status_code=404, detail="No picture attached")
    path = Path(model["profile_picture_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Picture file no longer exists")
    return path


@router.get("/{model_id}/picture")
def view_picture(model_id: str):
    return media_response(_picture_path(model_id))


@router.get("/{model_id}/picture/thumb")
def view_picture_thumb(model_id: str, size: Annotated[int, Query(ge=16, le=512)] = 96):
    """The roster paints a grid of ~48px avatars; serving each model's full
    upload (multi-MB straight off a phone camera) just to fill that was the
    whole page's load time. Cached next to the original, which _save_picture
    rmtree's on replacement -- so there is nothing to invalidate by hand."""
    path = _picture_path(model_id)
    thumb_path = path.parent / f".thumb_{size}.webp"
    if not thumb_path.is_file():
        try:
            write_image_thumb(path, thumb_path, size)
        except (OSError, ValueError, Image.UnidentifiedImageError):
            # An upload Pillow can't read still has to show *something* --
            # fall back to the original rather than a broken tile.
            logger.warning("could not thumbnail %s", path, exc_info=True)
            return media_response(path)

    # No max-age: the URL is stable across picture replacements, so let
    # FileResponse's etag/last-modified revalidation decide instead.
    return FileResponse(thumb_path, media_type="image/webp")


@router.post("/{model_id}/delete")
def delete(model_id: str):
    if not models_store.delete_model(model_id):
        raise HTTPException(status_code=404, detail="Model not found")
    picture_dir = PICTURE_ROOT / model_id
    if picture_dir.is_dir():
        shutil.rmtree(picture_dir)
    return RedirectResponse(url="/models", status_code=303)


@router.post("/{model_id}/instagram/add")
def add_instagram(model_id: str, urls: Annotated[str, Form()]):
    """One Instagram URL per line -- also covers the single-URL case."""
    lines = [u.strip() for u in urls.splitlines() if u.strip()]
    if not lines:
        raise HTTPException(status_code=400, detail="At least one URL is required")
    if models_store.add_instagram_accounts_bulk(model_id, lines) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id}/instagram/{account_id}/update")
def update_instagram(
    model_id: str,
    account_id: str,
    url: Annotated[str, Form()],
    owner: Annotated[str, Form()] = "",
    phone: Annotated[str, Form()] = "",
    sim_number: Annotated[str, Form()] = "",
    password: Annotated[str, Form()] = "",
    email: Annotated[str, Form()] = "",
):
    """One form per account: the URL plus the optional details the team needs
    to actually get into it (owner/phone/SIM/password/email)."""
    if not url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    if not models_store.update_instagram_account(
        account_id,
        url.strip(),
        owner=owner.strip(),
        phone=phone.strip(),
        sim_number=sim_number.strip(),
        password=password.strip(),
        email=email.strip(),
    ):
        raise HTTPException(status_code=404, detail="Instagram account not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id}/instagram/{account_id}/delete")
def delete_instagram(model_id: str, account_id: str):
    if not models_store.delete_instagram_account(account_id):
        raise HTTPException(status_code=404, detail="Instagram account not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id}/contacts/add")
def add_contact(
    model_id: str,
    contact_type: Annotated[str, Form()],
    value: Annotated[str, Form()],
):
    if not contact_type.strip() or not value.strip():
        raise HTTPException(status_code=400, detail="Type and value are required")
    if models_store.add_contact(model_id, contact_type.strip(), value.strip()) is None:
        raise HTTPException(status_code=404, detail="Model not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id}/contacts/{contact_id}/update")
def update_contact(
    model_id: str,
    contact_id: str,
    contact_type: Annotated[str, Form()],
    value: Annotated[str, Form()],
):
    if not contact_type.strip() or not value.strip():
        raise HTTPException(status_code=400, detail="Type and value are required")
    if not models_store.update_contact(contact_id, contact_type.strip(), value.strip()):
        raise HTTPException(status_code=404, detail="Contact not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id}/contacts/{contact_id}/delete")
def delete_contact(model_id: str, contact_id: str):
    if not models_store.delete_contact(contact_id):
        raise HTTPException(status_code=404, detail="Contact not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)
