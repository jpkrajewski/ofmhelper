# ofmhelpers/web/routers/cookies.py
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import RedirectResponse

from ofmhelpers.config import settings
from ofmhelpers.web.templates_config import templates

router = APIRouter(prefix="/cookies", tags=["admin"])


@router.get("")
def form(request: Request, uploaded: bool = False):
    return templates.TemplateResponse(
        request, "cookies_form.html", {"uploaded": uploaded}
    )


@router.post("")
async def upload_cookies(file: UploadFile):
    dest = Path(settings.downloaders.cookies_file)
    payload = await file.read()
    # Blocking disk I/O off the event loop.
    await run_in_threadpool(dest.parent.mkdir, parents=True, exist_ok=True)
    await run_in_threadpool(dest.write_bytes, payload)
    return RedirectResponse(url="/cookies?uploaded=1", status_code=303)
