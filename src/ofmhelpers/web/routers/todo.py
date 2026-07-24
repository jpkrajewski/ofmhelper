"""
ofmhelpers/web/routers/todo.py

Admin-managed task list (model name + link to replicate + comments) that VAs
can see but not modify. Add/toggle/delete/export/import are admin-only --
enforced here, server-side, not just hidden in the template: the page itself
is reachable by both roles like every other page in the app, so a VA could
otherwise still POST straight to these endpoints.
"""

import json
import mimetypes
import os
import shutil
from datetime import datetime
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Request,
    Form,
    HTTPException,
    UploadFile,
    File,
)
from fastapi.responses import RedirectResponse, Response, FileResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.auth import require_admin, ROLE_ADMIN
from ofmhelpers.web import todos, approval_tokens
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import classify_kind
from ofmhelpers.gdrive.client import upload_file as gdrive_upload_file
from ofmhelpers.discord.client import send_webhook

router = APIRouter(prefix="/todo", tags=["todo"])

# Where VA-uploaded "ready asset" files live, one subdirectory per todo.
ASSET_ROOT = Path("uploads") / "todo_assets"


def _decorate_asset(t: dict) -> dict:
    """Adds the two computed fields the Asset column needs: the preview
    (name/kind/view_url) and the live status of t's background Drive-upload
    job (see web/jobs.py), if it has one. Shared by the full-page render and
    the /asset-cell fragment so both render identically."""
    t["asset"] = (
        {
            "name": t["asset_name"],
            "kind": classify_kind(t["asset_name"]),
            "view_url": f"/todo/{t['id']}/asset",
        }
        if t.get("asset_name")
        else None
    )
    job_id = t.get("drive_upload_job_id")
    t["drive_upload_job"] = get_job(job_id) if job_id else None
    return t


@router.get("")
def form(request: Request):
    items = todos.list_todos()
    for t in items:
        t["created_at_display"] = datetime.fromtimestamp(t["created_at"]).strftime(
            "%Y-%m-%d %H:%M"
        )
        _decorate_asset(t)

    return templates.TemplateResponse(
        request,
        "todo_form.html",
        {
            "todos": items,
            "is_admin": request.session.get("role") == ROLE_ADMIN,
        },
    )


@router.post("/add")
def add(
    request: Request,
    model_name: str = Form(...),
    url: str = Form(...),
    comments: str = Form(""),
):
    require_admin(request)
    if not model_name.strip() or not url.strip():
        raise HTTPException(status_code=400, detail="Model name and URL are required")

    todos.add_todo(
        model_name.strip(), url.strip(), comments.strip(), request.session.get("role")
    )
    return RedirectResponse(url="/todo", status_code=303)


@router.get("/export")
def export(request: Request):
    require_admin(request)
    body = json.dumps(todos.list_todos(), indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": 'attachment; filename="todos.json"'},
    )


@router.post("/import")
async def import_(request: Request, file: UploadFile = File(...)):
    require_admin(request)
    raw = await file.read()
    try:
        entries = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Uploaded file is not valid JSON")
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="JSON must be a list of tasks")

    try:
        todos.import_todos(entries, request.session.get("role"))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/toggle")
def toggle(request: Request, todo_id: str):
    require_admin(request)
    if not todos.toggle_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/delete")
def delete(request: Request, todo_id: str):
    require_admin(request)
    if not todos.delete_todo(todo_id):
        raise HTTPException(status_code=404, detail="Todo not found")
    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/asset")
async def upload_asset(request: Request, todo_id: str, file: UploadFile = File(...)):
    """VA (or admin) attaches a ready asset to a task -- no role check, both
    logged-in roles are allowed to do this, unlike add/toggle/delete/approve/
    upload-drive below."""
    if todos.get_todo(todo_id) is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    if not file.filename:
        raise HTTPException(status_code=400, detail="A file is required")

    asset_dir = ASSET_ROOT / todo_id
    if asset_dir.is_dir():
        shutil.rmtree(asset_dir)
    asset_dir.mkdir(parents=True, exist_ok=True)
    dest = asset_dir / file.filename
    with dest.open("wb") as out:
        shutil.copyfileobj(file.file, out)

    todos.attach_asset(todo_id, str(dest), file.filename)

    todo = todos.get_todo(todo_id)
    try:
        _notify_discord_for_review(todo)
    except Exception as exc:
        # The asset is already saved and attached at this point -- a failed
        # notification doesn't undo that (no other write path in this app
        # rolls back on a downstream failure either). It does need to
        # surface loudly though: this notification is the *only* way the
        # reviewer finds out there's something to approve.
        raise HTTPException(
            status_code=502,
            detail=f"Asset saved, but Discord notification failed: {exc}",
        )

    return RedirectResponse(url="/todo", status_code=303)


_DIVIDER = "▬" * 30  # or "─" * 40, whichever reads cleaner in your server's font


def _notify_discord_for_review(todo: dict) -> None:
    """..."""
    base_url = os.environ["APP_BASE_URL"].rstrip("/")
    token = approval_tokens.create_token(todo["id"], todo["asset_path"])
    approve_url = f"{base_url}/approve/{token}"
    asset_url = f"{base_url}/approve/{token}/asset"
    preview_url = f"{asset_url}/preview"

    header = "📥 **New asset awaiting approval**"
    approve_line = (
        f"[✅ Approve & Upload to Google Drive (CLICK THIS TO APPROVE)]({approve_url})"
    )

    send_webhook(_DIVIDER)  # separates this group from whatever came before

    if classify_kind(Path(todo["asset_path"]).name) == "image":
        send_webhook(
            header, [{"description": approve_line, "image": {"url": asset_url}}]
        )
    else:
        send_webhook(
            header, [{"description": f"{approve_line}\n\nCheck video below ⬇️⬇️⬇️⬇️⬇️⬇️"}]
        )
        send_webhook(preview_url)


@router.get("/{todo_id}/asset")
def view_asset(todo_id: str):
    todo = todos.get_todo(todo_id)
    if todo is None or not todo.get("asset_path"):
        raise HTTPException(status_code=404, detail="No asset attached")
    path = Path(todo["asset_path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Asset file no longer exists")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.get("/{todo_id}/asset-cell")
def asset_cell(request: Request, todo_id: str):
    """HTML fragment (not a full page) for the Asset column's contents --
    the todo list's JS fetches this after every asset action (upload/
    replace/approve/upload-to-drive/retry) instead of following that
    action's redirect, and every 2s while a Drive-upload job is running.
    Either way, only that one cell's markup gets replaced -- no full-page
    reload for what's otherwise just editing one row (see conversation:
    plain form POSTs, and before that a <meta refresh> for job polling,
    both did a full-page navigation for something that only ever changes
    one cell)."""
    todo = todos.get_todo(todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    _decorate_asset(todo)

    return templates.TemplateResponse(
        request,
        "_todo_asset_cell.html",
        {
            "t": todo,
            "is_admin": request.session.get("role") == ROLE_ADMIN,
        },
    )


@router.post("/{todo_id}/approve")
def approve(request: Request, todo_id: str):
    require_admin(request)
    if not todos.approve_todo(todo_id):
        raise HTTPException(
            status_code=404, detail="Todo not found or has no asset attached"
        )
    return RedirectResponse(url="/todo", status_code=303)


@router.post("/{todo_id}/reject")
def reject(request: Request, todo_id: str, comment: str = Form(...)):
    require_admin(request)
    if not comment.strip():
        raise HTTPException(status_code=400, detail="A comment is required")
    if not todos.reject_todo(todo_id, comment.strip()):
        raise HTTPException(
            status_code=404, detail="Todo not found or has no asset attached"
        )
    return RedirectResponse(url="/todo", status_code=303)


def _upload_to_drive(todo_id: str, asset_path: str) -> str:
    """Runs in the background via BackgroundTasks -- a Drive upload is a
    network call with no bound on how long it takes (large video = long
    upload), so doing it inline would tie up the request, and the browser
    tab, for however long that takes. run_job (see web/jobs.py) records
    success/failure so the todo list can poll and show live status instead.
    """
    drive_file_id = gdrive_upload_file(asset_path)
    todos.mark_uploaded(todo_id, asset_path, drive_file_id)
    return drive_file_id


@router.post("/{todo_id}/upload-drive")
def upload_drive(request: Request, todo_id: str, background_tasks: BackgroundTasks):
    require_admin(request)
    todo = todos.get_todo(todo_id)
    if todo is None:
        raise HTTPException(status_code=404, detail="Todo not found")
    if not todo.get("asset_path"):
        raise HTTPException(status_code=400, detail="No asset attached")
    if not todo.get("approved"):
        raise HTTPException(
            status_code=400, detail="Approve the asset before uploading"
        )

    existing_job_id = todo.get("drive_upload_job_id")
    existing_job = get_job(existing_job_id) if existing_job_id else None
    if existing_job is not None and existing_job["status"] == "running":
        return RedirectResponse(url="/todo", status_code=303)

    asset_path = todo["asset_path"]
    job_id = create_job(
        "todo_drive_upload", {"todo_id": todo_id}, actor=request.session.get("role")
    )
    todos.set_drive_upload_job(todo_id, job_id)
    background_tasks.add_task(
        run_job,
        job_id,
        _upload_to_drive,
        {"todo_id": todo_id, "asset_path": asset_path},
    )
    return RedirectResponse(url="/todo", status_code=303)
