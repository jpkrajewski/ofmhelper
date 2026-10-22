"""
/cookies: the signed-in person's own cookies.txt -- upload, replace, remove.
Every user, admins included, sees and manages only their own; the page never
shows a cookie value, only which sites the file covers and whether it holds
an Instagram login (`stores.cookies`, which also says whose cookies each
background scan and download uses).
"""

from fastapi import APIRouter, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse, Response

from ofmhelpers.cache import enqueue
from ofmhelpers.web.account_tracker import rescan_limited
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.reel_watch import unlock_gated
from ofmhelpers.web.stores import cookies
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/cookies", tags=["cookies"])

_PRIVATE = {"Cache-Control": "no-store"}
_NOTES = {
    "uploaded": "Cookies saved. Your accounts synced without a login are syncing again.",
    "removed": "Cookies removed. Your downloads and scans now run logged out.",
}


def _page(
    request: Request,
    *,
    note: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    response = render(
        request,
        "cookies_form.html",
        saved=cookies.saved(AuthMiddleware.owner_id(request)),
        message=_NOTES.get(note or ""),
        error=error,
        status_code=status_code,
    )
    response.headers.update(_PRIVATE)
    return response


@router.get("")
def form(request: Request, note: str | None = None):
    return _page(request, note=note)


def _after_upload(user_id: int) -> None:
    # /reels' login-only reels of profiles they watch open up now.
    enqueue(unlock_gated, user_id)
    # Their /accounts synced without a working login: every reel counted now.
    rescan_limited(user_id)


@router.post("")
async def upload_cookies(request: Request, file: UploadFile) -> Response:
    user_id = AuthMiddleware.owner_id(request)
    payload = await file.read()
    try:
        await run_in_threadpool(
            cookies.save, user_id, payload, (file.filename or "cookies.txt")[:255]
        )
    except cookies.CookieFileError as exc:
        return _page(request, error=str(exc), status_code=400)
    await run_in_threadpool(_after_upload, user_id)
    return RedirectResponse(url="/cookies?note=uploaded", status_code=303)


@router.post("/delete")
def remove_cookies(request: Request) -> Response:
    cookies.remove(AuthMiddleware.owner_id(request))
    return RedirectResponse(url="/cookies?note=removed", status_code=303)
