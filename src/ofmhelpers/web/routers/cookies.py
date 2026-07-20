# ofmhelpers/web/routers/cookies.py
import os
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.responses import RedirectResponse

from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/cookies", tags=["admin"])


@router.get("")
def form(request: Request, uploaded: bool = False):
    return templates.TemplateResponse(
        request, "cookies_form.html", {"uploaded": uploaded}
    )


@router.post("")
async def upload_cookies(file: UploadFile):
    dest = Path(os.getenv("OFM_COOKIES_FILE", "cookies/cookies.txt"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(await file.read())
    return RedirectResponse(url="/cookies?uploaded=1", status_code=303)
