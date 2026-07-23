import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.web.templates_config import templates
from ofmhelpers.web.auth import AuthMiddleware
from ofmhelpers.web.recovery import recovery_loop

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
from ofmhelpers.web.routers.refs import router as ref_router
from ofmhelpers.web.routers.auth import router as auth_router
from ofmhelpers.web.routers.download_images import router as download_images_router
from ofmhelpers.web.routers.generate import router as generate_router
from ofmhelpers.web.routers.fake_ai import router as fake_ai_router
from ofmhelpers.web.routers.download_assets import router as download_assets_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Background recovery sweeper: auto-downloads kie.ai generations whose
    # in-request poll timed out (or that a restart orphaned) -- see
    # ofmhelpers/web/recovery.py. Cancelled cleanly on shutdown.
    sweeper = asyncio.create_task(recovery_loop())
    yield
    sweeper.cancel()


app = FastAPI(title="Global Ascend LLC — Content Ops", lifespan=lifespan)

# --- Auth setup -------------------------------------------------------
# SessionMiddleware signs/reads the cookie; AuthMiddleware gates every
# request on it. Order matters: SessionMiddleware must be added so it
# wraps AuthMiddleware (Starlette applies middleware outside-in in the
# order added, so Session needs to be added AFTER Auth here -- the last
# .add_middleware() call ends up outermost / runs first).
app.add_middleware(AuthMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ["SESSION_SECRET"],  # required -- set in .env
    session_cookie="ofm_session",
    max_age=60 * 60 * 5,  # 5 hours -- shared admin/VA passwords, keep it short
    https_only=os.getenv("SESSION_HTTPS_ONLY", "false").lower() == "true",
)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

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
app.include_router(ref_router)
app.include_router(auth_router)
app.include_router(download_images_router)
app.include_router(generate_router)
app.include_router(fake_ai_router)
app.include_router(download_assets_router)


@app.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/health")
def health():
    return {"status": "ok"}
