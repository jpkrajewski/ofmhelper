from fastapi import FastAPI, Request
from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.routers.download_reels import router as download_reels_router
from ofmhelpers.web.routers.clean_image import router as clean_images_router
from ofmhelpers.web.routers.seedance import router as seedance_router
from ofmhelpers.web.routers.jobs_status import router as job_router
from ofmhelpers.web.routers.el import router as el_router
from ofmhelpers.web.routers.helper_index import router as helper_router
from ofmhelpers.web.routers.radio_comms import router as radio_router
from ofmhelpers.web.routers.scraper import router as scraper_router
from ofmhelpers.web.routers.uploads_manager import router as up_router
from ofmhelpers.web.routers.cookies import router as cookie_router
from ofmhelpers.web.routers.nbp import router as nbp_router
from ofmhelpers.web.routers.kling import router as kling_router
from ofmhelpers.web.routers.prompt_history import router as ph_router
from ofmhelpers.web.routers.refs import router as ref_router


app = FastAPI(title="OFM VA Toolkit")

app.include_router(download_reels_router)
app.include_router(clean_images_router)
app.include_router(seedance_router)
app.include_router(job_router)
app.include_router(el_router)
app.include_router(helper_router)
app.include_router(radio_router)
app.include_router(scraper_router)
app.include_router(up_router)
app.include_router(cookie_router)
app.include_router(nbp_router)
app.include_router(kling_router)
app.include_router(ph_router)
app.include_router(ref_router)


@app.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/health")
def health():
    return {"status": "ok"}
