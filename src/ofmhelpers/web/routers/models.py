"""
ofmhelpers/web/routers/models.py

Admin-only roster of Models: name + profile picture + one OnlyFans link +
many Instagram account links. Gated via require_admin like file_manager.py
and action_log.py -- nobody but an admin needs to touch this roster.
"""

import shutil
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse

from ofmhelpers.web import models as models_store
from ofmhelpers.web.auth import require_admin
from ofmhelpers.web.templates_config import templates

router = APIRouter(
    prefix="/models", tags=["models"], dependencies=[Depends(require_admin)]
)

# Where profile pictures live, one subdirectory per model.
PICTURE_ROOT = Path("uploads") / "model_pictures"


def _save_picture(model_id: str, file: UploadFile) -> str:
    picture_dir = PICTURE_ROOT / model_id
    if picture_dir.is_dir():
        shutil.rmtree(picture_dir)
    picture_dir.mkdir(parents=True, exist_ok=True)
    dest = picture_dir / file.filename
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)
    return str(dest)


@router.get("")
def list_page(request: Request):
    return templates.TemplateResponse(
        request, "models_list.html", {"models": models_store.list_models()}
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

    model = models_store.add_model(name.strip(), onlyfans_url.strip())
    if profile_picture is not None and profile_picture.filename:
        path = _save_picture(model["id"], profile_picture)
        models_store.set_profile_picture(model["id"], path, profile_picture.filename)

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
    models_store.set_profile_picture(model_id, path, profile_picture.filename)
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.get("/{model_id}/picture")
def view_picture(model_id: str):
    model = models_store.get_model(model_id)
    if model is None or not model.get("profile_picture_path"):
        raise HTTPException(status_code=404, detail="No picture attached")
    path = Path(model["profile_picture_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Picture file no longer exists")
    return FileResponse(path)


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
def update_instagram(model_id: str, account_id: str, url: Annotated[str, Form()]):
    if not url.strip():
        raise HTTPException(status_code=400, detail="URL is required")
    if not models_store.update_instagram_account(account_id, url.strip()):
        raise HTTPException(status_code=404, detail="Instagram account not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)


@router.post("/{model_id}/instagram/{account_id}/delete")
def delete_instagram(model_id: str, account_id: str):
    if not models_store.delete_instagram_account(account_id):
        raise HTTPException(status_code=404, detail="Instagram account not found")
    return RedirectResponse(url=f"/models/{model_id}/edit", status_code=303)
