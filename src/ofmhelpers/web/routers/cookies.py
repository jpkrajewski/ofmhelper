"""
/cookies: the signed-in person's own cookies.txt -- upload, replace, remove.
Every user, admins included, sees and manages only their own; the page never
shows a cookie value, only which sites the file covers and whether it holds
an Instagram login (`stores.cookies`, which also says whose cookies each
background scan and download uses).
"""

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import Response

from ofmhelpers.cache import enqueue
from ofmhelpers.db.stores import cookies
from ofmhelpers.features.accounts.tracker import rescan_limited
from ofmhelpers.features.reels.watch import unlock_gated
from ofmhelpers.web.dependencies import OwnerId
from ofmhelpers.web.responses import NO_STORE, see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/cookies", tags=["cookies"])

_NOTES = {
    "uploaded": "Cookies saved. Your accounts synced without a login are syncing again.",
    "removed": "Cookies removed. Your downloads and scans now run logged out.",
}


def _page(
    request: Request,
    owner_id: int,
    *,
    note: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    response = render(
        request,
        "cookies_form.html",
        saved=cookies.saved(owner_id),
        message=_NOTES.get(note or ""),
        error=error,
        status_code=status_code,
    )
    response.headers.update(NO_STORE)
    return response


@router.get("")
def form(request: Request, owner_id: OwnerId, note: str | None = None) -> Response:
    return _page(request, owner_id, note=note)


def _after_upload(user_id: int) -> None:
    # /reels' login-only reels of profiles they watch open up now.
    enqueue(unlock_gated, user_id)
    # Their /accounts synced without a working login: every reel counted now.
    rescan_limited(user_id)


@router.post("")
def upload_cookies(request: Request, owner_id: OwnerId, file: UploadFile) -> Response:
    """A plain `def`, so FastAPI runs it in its threadpool: the file is read,
    parsed and stored -- and the page re-rendered on a refusal -- off the
    event loop. A file over the limit is refused by its size before it is
    read into memory."""
    try:
        if file.size is not None:
            cookies.check_size(file.size)
        cookies.save(owner_id, file.file.read(), (file.filename or "cookies.txt")[:255])
    except cookies.CookieFileError as exc:
        return _page(request, owner_id, error=str(exc), status_code=400)
    _after_upload(owner_id)
    return see_other("/cookies?note=uploaded")


@router.post("/delete")
def remove_cookies(owner_id: OwnerId) -> Response:
    cookies.remove(owner_id)
    return see_other("/cookies?note=removed")
