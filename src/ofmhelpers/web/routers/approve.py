"""
ofmhelpers/web/routers/approve.py

Public (no-login) magic-link approval flow -- see web/approval_tokens.py.
Deliberately outside AuthMiddleware (registered in web/auth.py's
PUBLIC_PREFIXES): the whole point is a reviewer can tap a Discord link on
their phone, with no session cookie, and have it approve the asset and kick
off the Drive upload in one shot. Security comes from the token itself
(unguessable, single-use, expiring) rather than a login.
"""

import mimetypes
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import FileResponse, RedirectResponse

from ofmhelpers.web import approval_tokens, todos
from ofmhelpers.web.jobs import create_job, run_job
from ofmhelpers.web.routers.todo import _upload_to_drive
from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/approve", tags=["approve"])

_FAILURE_MESSAGES = {
    "not_found": "This approval link is invalid.",
    "expired": "This approval link has expired.",
    "used": "This approval link has already been used.",
    "stale": "The asset changed since this link was sent -- check the Todo page.",
    "missing": "This todo no longer exists.",
}


@router.get("/result")
def result(request: Request, status: str = "error", reason: str = ""):
    return templates.TemplateResponse(
        request,
        "approve_result.html",
        {
            "ok": status == "ok",
            "message": _FAILURE_MESSAGES.get(reason, "Something went wrong."),
        },
    )


@router.get("/{token}/asset")
def view_asset(token: str):
    record = approval_tokens.get_token(token)
    if record is None:
        return RedirectResponse(
            url="/approve/result?status=error&reason=not_found", status_code=303
        )

    path = Path(record["asset_path"])
    if not path.is_file():
        return RedirectResponse(
            url="/approve/result?status=error&reason=stale", status_code=303
        )

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type)


@router.get("/{token}")
def approve(token: str, background_tasks: BackgroundTasks):
    record = approval_tokens.get_token(token)
    if record is None:
        return RedirectResponse(
            url="/approve/result?status=error&reason=not_found", status_code=303
        )
    if record["used_at"] is not None:
        return RedirectResponse(
            url="/approve/result?status=error&reason=used", status_code=303
        )

    todo = todos.get_todo(record["todo_id"])
    if todo is None:
        return RedirectResponse(
            url="/approve/result?status=error&reason=missing", status_code=303
        )

    outcome = approval_tokens.consume(token, todo["asset_path"])
    if outcome != "ok":
        return RedirectResponse(
            url=f"/approve/result?status=error&reason={outcome}", status_code=303
        )

    todos.approve_todo(todo["id"])

    asset_path = todo["asset_path"]
    job_id = create_job("todo_drive_upload", {"todo_id": todo["id"]}, actor="discord")
    todos.set_drive_upload_job(todo["id"], job_id)
    background_tasks.add_task(
        run_job,
        job_id,
        _upload_to_drive,
        {"todo_id": todo["id"], "asset_path": asset_path},
    )

    return RedirectResponse(url="/approve/result?status=ok", status_code=303)
