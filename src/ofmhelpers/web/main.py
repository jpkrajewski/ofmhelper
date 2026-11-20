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
    FrameGuardMiddleware,
    WriteRateLimitMiddleware,
)
from ofmhelpers.web.routers import ROUTERS

configure_telemetry("ascend-web")
configure_sentry("ascend-web")
configure_logging()


def create_app() -> FastAPI:
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
    session = settings.session
    return [
        Middleware(BridgeMiddleware),
        Middleware(CanonicalHostMiddleware),
        Middleware(FrameGuardMiddleware),
        Middleware(CompressionMiddleware),
        Middleware(
            SessionMiddleware,
            secret_key=session.session_secret,
            session_cookie="ofm_session",
            max_age=session.session_max_age_s,
            https_only=session.session_https_only,
        ),
        Middleware(WriteRateLimitMiddleware),
        Middleware(AuthMiddleware),
    ]


def _monitoring(app: FastAPI) -> None:
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
