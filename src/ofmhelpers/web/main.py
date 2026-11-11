"""
The FastAPI application: logging, middleware, static files and the router
registration loop. Nothing feature-specific lives here -- a new page is
added to `routers/__init__.py`'s ROUTERS list. HTTP only: the background
sweeps are a process of their own (`ofmhelpers/sweeps.py`, the `sweeps`
compose service), so any number of web processes can serve.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.config import settings
from ofmhelpers.log import configure_logging, get_logger
from ofmhelpers.telemetry import configure_telemetry, instrument_app
from ofmhelpers.web.exceptions import exception_handlers
from ofmhelpers.web.middleware import (
    AuthMiddleware,
    BridgeMiddleware,
    CanonicalHostMiddleware,
    CompressionMiddleware,
    WriteRateLimitMiddleware,
)
from ofmhelpers.web.routers import ROUTERS

# Before anything else in the process logs: uvicorn imports this module to
# find `app`, so this runs ahead of the first request and ahead of uvicorn's
# own startup records. See ofmhelpers/log.py.
configure_telemetry("ascend-web")
configure_logging()
logger = get_logger(__name__)


app = FastAPI(
    title="Ascend Global LLC — Content Ops",
    exception_handlers=exception_handlers(),
)
instrument_app(app)

# --- Middleware -------------------------------------------------------
# Starlette applies middleware outside-in in the order added, so the LAST
# .add_middleware() call ends up outermost / runs first -- this block reads
# bottom-up. See web/middleware/__init__.py for the resulting request order.
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
# Outermost: compresses what everything inside produced (pages, JSON, CSS/JS).
app.add_middleware(CompressionMiddleware)
# www. of APP_BASE_URL redirects to the one public host.
app.add_middleware(CanonicalHostMiddleware)
# Before all of it: our own bridge domains are served here, never touching the
# session/auth stack; every other host falls through to the app unchanged.
app.add_middleware(BridgeMiddleware)

app.mount(
    "/static",
    StaticFiles(directory=Path(__file__).parent / "static"),
    name="static",
)

for router in ROUTERS:
    app.include_router(router)
