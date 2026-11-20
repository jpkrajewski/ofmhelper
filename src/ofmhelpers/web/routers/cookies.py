from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import Response

from ofmhelpers.features.accounts.tracker import rescan_limited
from ofmhelpers.features.cookies import service
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, OwnerId
from ofmhelpers.web.responses import NO_STORE, see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/cookies", tags=["cookies"], dependencies=[Access(Area.COOKIES)]
)


def _page(
    request: Request,
    owner_id: int,
    *,
    note: str | None = None,
    error: str | None = None,
    status_code: int = 200,
) -> Response:
    view = service.page_view(owner_id, note, error)
    response = render(
        request, "cookies_form.html", **dict(view), status_code=status_code
    )
    response.headers.update(NO_STORE)
    return response


@router.get("")
def form(request: Request, owner_id: OwnerId, note: str | None = None) -> Response:
    return _page(request, owner_id, note=note)


@router.post("")
def upload_cookies(request: Request, owner_id: OwnerId, file: UploadFile) -> Response:
    try:
        service.upload(owner_id, file.file, file.filename, file.size, rescan_limited)
    except RefusedError as exc:  # silent: shown on the page
        return _page(request, owner_id, error=str(exc), status_code=400)
    return see_other("/cookies?note=uploaded")


@router.post("/delete")
def remove_cookies(owner_id: OwnerId) -> Response:
    service.remove(owner_id)
    return see_other("/cookies?note=removed")
