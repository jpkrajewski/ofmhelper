"""
The FastAPI application, built by `create_app()` out of three parts:
`_middleware()` (the request's way in, outermost first), `_monitoring()`
(Logfire's request traces) and `_routes()` (static files and the router
registration loop). Nothing feature-specific lives here -- a new page is
added to `routers/__init__.py`'s ROUTERS list. HTTP only: the background
sweeps are a process of their own (`ofmhelpers/sweeps.py`, the `sweeps`
compose service), so any number of web processes can serve.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware import Middleware
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.config import settings
from ofmhelpers.log import configure_logging
from ofmhelpers.sentry import configure_sentry
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
configure_sentry("ascend-web")
configure_logging()


def create_app() -> FastAPI:
    """The app as this deployment's settings say.

    Outside production: `/docs`, `/redoc` and `/openapi.json` (behind sign-in
    like any page), and with `OFM_DEBUG` a crash answers its traceback page.
    Production serves none of the three (`openapi_url=None` turns the two
    pages off with it) and refuses to start with debug on.

    `callbacks=` and `webhooks=` are left out on purpose: both only document,
    in the OpenAPI schema, requests THIS app sends to its caller. Ours go the
    other way (Fanvue, Stacked and the AI providers call us) and are ordinary
    routes of `routers/webhooks.py`.
    """
    infra = settings.infra
    app = FastAPI(
        title="Ascend Global LLC — Content Ops",
        version=settings.telemetry.release,
        debug=infra.debug,
        openapi_url=None if infra.production else "/openapi.json",
        exception_handlers=exception_handlers(),
        middleware=_middleware(),
    )
    _monitoring(app)
    _routes(app)
    return app


def _middleware() -> list[Middleware]:
    """In the order a request passes them: the first is outermost. See
    web/middleware/__init__.py."""
    session = settings.session
    return [
        # Our own bridge domains are served here, never touching the
        # session/auth stack; every other host falls through unchanged.
        Middleware(BridgeMiddleware),
        # www. of APP_BASE_URL redirects to the one public host.
        Middleware(CanonicalHostMiddleware),
        # Compresses what everything inside produced (pages, JSON, CSS/JS).
        Middleware(CompressionMiddleware),
        Middleware(
            SessionMiddleware,
            secret_key=session.session_secret,  # required -- set in .env
            session_cookie="ofm_session",
            max_age=session.session_max_age_s,
            https_only=session.session_https_only,
        ),
        Middleware(WriteRateLimitMiddleware),
        Middleware(AuthMiddleware),
    ]


def _monitoring(app: FastAPI) -> None:
    """Each request its own Logfire trace. Sentry needs no call here: its
    FastAPI hook was installed by `configure_sentry()` above."""
    instrument_app(app)


def _routes(app: FastAPI) -> None:
    app.mount(
        "/static",
        StaticFiles(directory=Path(__file__).parent / "static"),
        name="static",
    )
    for router in ROUTERS:
        app.include_router(router)


app = create_app()
