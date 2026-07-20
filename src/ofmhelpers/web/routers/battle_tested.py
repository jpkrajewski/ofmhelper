import tempfile
from pathlib import Path

from fastapi import APIRouter, Request, UploadFile, File, HTTPException
from fastapi.responses import RedirectResponse

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.battle_tested import parse_xlsx, load_prompts, save_prompts

router = APIRouter(prefix="/battle-tested", tags=["battle-tested"])


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(
        request, "battle_tested.html", {"prompts": load_prompts()}
    )


@router.post("/upload")
async def upload(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".xlsx"):
        raise HTTPException(status_code=400, detail="Please upload a .xlsx file")

    with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = Path(tmp.name)

    try:
        prompts = parse_xlsx(tmp_path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        tmp_path.unlink(missing_ok=True)  # delete the temp excel after parsing

    if not prompts:
        raise HTTPException(status_code=400, detail="No rows found in sheet")

    save_prompts(prompts)  # overwrites the single prompts.json
    return RedirectResponse(url="/battle-tested", status_code=303)
