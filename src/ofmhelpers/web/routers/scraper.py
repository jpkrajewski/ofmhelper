"""
ofmhelpers/web/routers/scraper.py
"""

import shutil
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    Request,
    Form,
    UploadFile,
    File,
    BackgroundTasks,
    HTTPException,
)
from fastapi.responses import RedirectResponse, FileResponse

from ofmhelpers.config.scrapers import SCRAPRES_REGISTRY, Scrapers
from ofmhelpers.scraping.apify import get_client_with_most_credits, run_actor

from ofmhelpers.scraping.models import PostBase, Reel, TikTokVideo

from ofmhelpers.scraping.post_exporter import PostExcelExporter
from ofmhelpers.scraping.post_scorer import PostFilterProcessor
from ofmhelpers.utils.profile_loader import normalize_profiles_names

from ofmhelpers.utils.sheets_to_columns import sheets_columns_to_keys
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.jobs import create_job, run_job, get_job
from ofmhelpers.web.routers.task_helpers import asset_card

router = APIRouter(prefix="/helpers/scraper", tags=["scraper"])

UPLOAD_ROOT = Path("uploads") / "scraper"

# Maps each scraper to the post model used to parse its Apify output.
ODT: dict[Scrapers, type[PostBase]] = {
    Scrapers.INSTAGRAM_PROFILES: Reel,
    Scrapers.TIKTOK_PROFILES: TikTokVideo,
}


def _run_scrape(
    sheet_path: str,
    api_keys: list[str],
    results_per_profile: int,
    results_days_back: int,
    job_dir: str,
) -> list[dict]:
    xlsx_path = str(Path(job_dir) / "scraped_posts.xlsx")

    sheet_configs = {scraper.value: SCRAPRES_REGISTRY[scraper] for scraper in Scrapers}
    data = sheets_columns_to_keys(sheet_path)
    client = get_client_with_most_credits(api_keys)

    scraper_sheets: list[tuple[str, list[PostBase]]] = []
    for scraper in Scrapers:
        profiles = normalize_profiles_names(data[scraper])
        configuration = SCRAPRES_REGISTRY[scraper]
        items = run_actor(
            client=client,
            actor_id=configuration.actor_id,
            raw_input=configuration.prepare_raw_input_func(
                profiles, results_per_profile, results_days_back
            ),
        )
        post_cls = ODT[scraper]
        converted: list[PostBase] = [post_cls.from_apify(i) for i in items]
        converted_valid = [cv for cv in converted if cv.is_valid()]
        scraper_sheets.append((scraper.value, converted_valid))

    PostExcelExporter().export(scraper_sheets, xlsx_path)
    final_path = PostFilterProcessor().process(xlsx_path, sheet_configs)

    return [{"name": Path(final_path).name, "path": str(final_path)}]


@router.get("")
def form(request: Request):
    return templates.TemplateResponse(request, "scraper_form.html", {})


@router.post("/run")
async def run(
    request: Request,
    background_tasks: BackgroundTasks,
    api_keys: str = Form(...),  # textarea, one token per line
    sheet: UploadFile = File(...),
    results_per_profile: int = Form(20),
    results_days_back: int = Form(7),
):
    keys = [k.strip() for k in api_keys.splitlines() if k.strip()]
    if not keys:
        raise HTTPException(status_code=400, detail="At least one API key is required")
    if not sheet.filename:
        raise HTTPException(status_code=400, detail="Sheet file is required")

    job_dir = UPLOAD_ROOT / uuid.uuid4().hex[:8]
    job_dir.mkdir(parents=True, exist_ok=True)

    sheet_dest = job_dir / sheet.filename
    with sheet_dest.open("wb") as out:
        shutil.copyfileobj(sheet.file, out)

    # api_keys excluded from stored job params -- passed only to the
    # background task, never persisted via create_job/get_job.
    params = {
        "sheet_name": sheet.filename,
        "results_per_profile": results_per_profile,
        "results_days_back": results_days_back,
    }
    job_id = create_job("scraper", params, actor=request.session.get("role"))
    background_tasks.add_task(
        run_job,
        job_id,
        _run_scrape,
        {
            "sheet_path": str(sheet_dest),
            "api_keys": keys,
            "results_per_profile": results_per_profile,
            "results_days_back": results_days_back,
            "job_dir": str(job_dir),
        },
    )

    return RedirectResponse(url=f"/helpers/scraper/jobs/{job_id}", status_code=303)


@router.get("/jobs/{job_id}")
def job_status(request: Request, job_id: str):
    job = get_job(job_id)
    if job is None:
        return templates.TemplateResponse(
            request, "scraper_form.html", {}, status_code=404
        )

    assets = []
    if job.get("status") == "done":
        assets = [
            asset_card(f["name"], idx, f"/helpers/scraper/files/{job_id}")
            for idx, f in enumerate(job["result"])
        ]

    return templates.TemplateResponse(
        request,
        "job_status.html",
        {
            "job": job,
            "assets": assets,
            "title": "Scraper",
            "pending_message": "Scraping profiles…",
            "back_url": "/helpers/scraper",
            "back_label": "run another scrape",
        },
    )


@router.get("/files/{job_id}/{index}")
def download_file(job_id: str, index: int):
    job = get_job(job_id)
    if job is None or job.get("status") != "done":
        raise HTTPException(status_code=404, detail="Job not found or not finished")

    files = job["result"]
    if index < 0 or index >= len(files):
        raise HTTPException(status_code=404, detail="File not found")

    path = Path(files[index]["path"])
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File no longer exists on server")

    return FileResponse(
        path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
