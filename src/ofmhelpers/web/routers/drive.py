"""/drive: "Upload to Drive" on a /clone batch. The first click connects a
Google Drive the usual way -- Google's consent page, then back to the page
-- and every click after uploads the picked jobs' results into it.

The session holds only the connection id; the refresh token stays in
Postgres (stores/drive_connections.py) for the worker to upload with.
"""

from pathlib import Path
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.exceptions import GoogleAuthError, RefreshError
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.gdrive import account_email, authorization_url, exchange_code
from ofmhelpers.gdrive import upload_files as upload_to_drive
from ofmhelpers.gdrive.oauth import PendingAuth
from ofmhelpers.log import get_logger
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.schemas import DriveConnection
from ofmhelpers.web.stores.drive_connections import (
    add_connection,
    delete_connection,
    get_connection,
)
from ofmhelpers.web.stores.jobs import create_job, get_job, run_job

logger = get_logger(__name__)

router = APIRouter(prefix="/drive", tags=["drive"])

TASK = "drive_upload"

_CONNECTION_KEY = "gdrive_connection"
_PENDING_KEY = "gdrive_pending"


def session_drive(request: Request) -> DriveConnection | None:
    """The Drive this session connected, if it still exists."""
    connection_id = request.session.get(_CONNECTION_KEY)
    return get_connection(connection_id) if connection_id else None


def forget_drive(request: Request) -> None:
    """Logout: the next person in this browser connects their own Drive,
    and the token of this one is deleted rather than left behind."""
    connection_id = request.session.pop(_CONNECTION_KEY, None)
    if connection_id:
        delete_connection(connection_id)


def _local(next_url: str) -> str:
    """Only a path on this site: never an open redirect."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


def _require_configured() -> None:
    if not settings.gdrive.configured:
        raise HTTPException(status_code=503, detail="Google Drive is not set up")


@router.get("/connect")
def connect(request: Request, next_url: Annotated[str, Query(alias="next")] = "/"):
    _require_configured()
    url, pending = authorization_url(_local(next_url))
    request.session[_PENDING_KEY] = pending.model_dump()
    return RedirectResponse(url=url, status_code=303)


@router.get("/callback")
def callback(
    request: Request,
    state: str = "",
    code: str | None = None,
    error: str | None = None,
):
    stored = request.session.pop(_PENDING_KEY, None)
    if stored is None or stored["state"] != state:
        raise HTTPException(status_code=400, detail="Drive login expired, try again")
    pending = PendingAuth.model_validate(stored)
    if error or not code:
        # The user pressed Cancel on Google's page: back where they were.
        return RedirectResponse(url=pending.next_url, status_code=303)
    try:
        credentials = exchange_code(code, pending)
        email = account_email(credentials)
    except (GoogleAuthError, HttpError, OAuth2Error):
        logger.warning("google drive connect failed", exc_info=True)
        raise HTTPException(
            status_code=502, detail="Google refused the login"
        ) from None
    if not credentials.refresh_token:
        raise HTTPException(status_code=502, detail="Google sent no refresh token")
    request.session[_CONNECTION_KEY] = add_connection(email, credentials.refresh_token)
    return RedirectResponse(url=pending.next_url, status_code=303)


def _result_paths(request: Request, job_ids: list[str]) -> list[str]:
    """The finished local files of the jobs this session may see."""
    actor = AuthMiddleware.visible_actor(request)
    return [
        f.path
        for job_id in job_ids
        if (job := get_job(job_id, actor)) is not None and job.status == "done"
        for f in job.result_files()
        if f.path
    ]


def _upload(connection_id: str, paths: list[str]) -> dict:
    """Fails the job when any file did not go up, saying how many did: the
    next click uploads only the rest (`upload_files` skips what is there)."""
    connection = get_connection(connection_id)
    if connection is None:
        msg = "Google Drive was disconnected, click Upload again"
        raise RuntimeError(msg)
    try:
        report = upload_to_drive(connection.refresh_token, [Path(p) for p in paths])
    except RefreshError:
        # Revoked or expired on Google's side: the next click reconnects.
        delete_connection(connection_id)
        msg = "Google login expired, click Upload again to reconnect"
        raise RuntimeError(msg) from None
    if report.failed:
        done = len(paths) - len(report.failed)
        msg = (
            f"{done}/{len(paths)} in Drive; failed: {', '.join(report.failed)}. "
            "Click Upload again to retry the rest."
        )
        raise RuntimeError(msg)
    return report.model_dump()


@router.post("/upload")
def upload(
    request: Request,
    job_ids: Annotated[list[str], Form()],
    next_url: Annotated[str, Form(alias="next")] = "/",
):
    """Queues the upload and answers its job id -- or, with no Drive
    connected yet, the URL that connects one and comes back to `next`."""
    _require_configured()
    drive = session_drive(request)
    if drive is None:
        query = urlencode({"next": _local(next_url)})
        return JSONResponse({"connect_url": f"/drive/connect?{query}"}, 409)
    paths = _result_paths(request, job_ids)
    if not paths:
        raise HTTPException(status_code=400, detail="Nothing finished to upload yet")
    job_id = create_job(
        TASK,
        {"files": [Path(p).name for p in paths], "drive": drive.email},
        actor=request.session.get("role"),
    )
    enqueue(run_job, job_id, _upload, {"connection_id": drive.id, "paths": paths})
    return {"job_id": job_id, "files": len(paths), "drive": drive.email}


@router.get("/uploads/{job_id}")
def upload_status(request: Request, job_id: str):
    job = get_job(job_id, AuthMiddleware.visible_actor(request))
    if job is None or job.task != TASK:
        raise HTTPException(status_code=404, detail="Upload not found")
    result = job.result if isinstance(job.result, dict) else {}
    return {
        "status": job.status,
        "error": job.error,
        "folder_url": result.get("folder_url"),
    }
