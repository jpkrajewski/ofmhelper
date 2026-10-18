"""/drive: connect a Google Drive the usual way -- click, Google's consent
page, back here -- so /clone uploads each finished video into it.

The session holds only the connection id; the refresh token stays in
Postgres (stores/drive_connections.py) for the worker to upload with.
"""

from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from google.auth.exceptions import GoogleAuthError
from googleapiclient.errors import HttpError
from oauthlib.oauth2.rfc6749.errors import OAuth2Error

from ofmhelpers.config import settings
from ofmhelpers.gdrive import account_email, authorization_url, exchange_code
from ofmhelpers.gdrive.oauth import PendingAuth
from ofmhelpers.log import get_logger
from ofmhelpers.web.schemas import DriveConnection
from ofmhelpers.web.stores.drive_connections import (
    add_connection,
    delete_connection,
    get_connection,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/drive", tags=["drive"])

_CONNECTION_KEY = "gdrive_connection"
_PENDING_KEY = "gdrive_pending"


def session_drive(request: Request) -> DriveConnection | None:
    """The Drive this session connected, if it still exists."""
    connection_id = request.session.get(_CONNECTION_KEY)
    return get_connection(connection_id) if connection_id else None


def _local(next_url: str) -> str:
    """Only a path on this site: never an open redirect."""
    if next_url.startswith("/") and not next_url.startswith("//"):
        return next_url
    return "/"


@router.get("/connect")
def connect(request: Request, next_url: Annotated[str, Query(alias="next")] = "/"):
    if not settings.gdrive.configured:
        raise HTTPException(status_code=503, detail="Google Drive is not set up")
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


@router.post("/disconnect")
def disconnect(request: Request, next_url: Annotated[str, Form(alias="next")] = "/"):
    connection_id = request.session.pop(_CONNECTION_KEY, None)
    if connection_id:
        delete_connection(connection_id)
    return RedirectResponse(url=_local(next_url), status_code=303)
