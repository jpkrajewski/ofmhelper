"""
The FastAPI application: logging, middleware, static files, lifespan, and
the router registration loop. Nothing feature-specific lives here -- a new
page is added to `routers/__init__.py`'s ROUTERS list, not to this file.
"""

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.config import settings
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.web.auth import AuthMiddleware
from ofmhelpers.web.ratelimit import WriteRateLimitMiddleware
from ofmhelpers.web.recovery import recovery_loop
from ofmhelpers.web.routers import ROUTERS
from ofmhelpers.web.stores.jobs import load_jobs
from ofmhelpers.web.templates_config import templates

# Before anything else in the process logs: uvicorn imports this module to
# find `app`, so this runs ahead of the first request and ahead of uvicorn's
# own startup records. See ofmhelpers/log.py.
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Job history (the /generate gallery, Action log, every task's status
    # page) is persisted -- reload it now so a restart/rebuild doesn't start
    # with a blank slate. See web/stores/jobs.py.
    load_jobs()

    # Background recovery sweeper: auto-downloads kie.ai generations whose
    # in-request poll timed out (or that a restart orphaned) -- see
    # ofmhelpers/web/recovery.py. Cancelled cleanly on shutdown.
    sweeper = asyncio.create_task(recovery_loop())
    logger.info("startup complete: job history loaded, recovery sweeper running")
    yield
    sweeper.cancel()
    logger.info("shutdown: recovery sweeper cancelled")


app = FastAPI(title="Global Ascend LLC — Content Ops", lifespan=lifespan)

# --- Middleware -------------------------------------------------------
# Starlette applies middleware outside-in in the order added, so the LAST
# .add_middleware() call ends up outermost / runs first. Reading bottom-up,
# a request therefore passes: SessionMiddleware (reads/signs the cookie) ->
# WriteRateLimitMiddleware (drops a flood before any auth work) ->
# AuthMiddleware (gates everything not on the public allowlist).
_session_settings = settings.session
app.add_middleware(AuthMiddleware)
app.add_middleware(WriteRateLimitMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_session_settings.session_secret,  # required -- set in .env
    session_cookie="ofm_session",
    max_age=_session_settings.session_max_age_s,
    https_only=_session_settings.session_https_only,
)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

for router in ROUTERS:
    app.include_router(router)


@app.get("/")
def root(request: Request):
    return templates.TemplateResponse(request, "home.html", {})


@app.get("/health")
def health():
    return {"status": "ok"}
