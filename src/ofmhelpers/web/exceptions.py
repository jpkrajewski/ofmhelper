from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, model_serializer
from starlette.exceptions import HTTPException as StarletteHTTPException

from ofmhelpers.features.generate.service import GenerateRefusedError
from ofmhelpers.features.refusals import (
    AssetRefusedError,
    CloneRefusedError,
    ConflictError,
    EditRefusedError,
    ForbiddenError,
    RefusedError,
    TooSoonError,
    TryAgainError,
    UnavailableError,
)
from ofmhelpers.features.refusals import NotFoundError as MissingError
from ofmhelpers.features.trials.folder import NotFoundError
from ofmhelpers.log import get_logger
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.middleware.frame_guard import FRAME_HEADERS

logger = get_logger(__name__)

MESSAGE = "Something went wrong. Try again."

REFUSALS: frozendict[type[Exception], int] = frozendict(
    {
        AssetRefusedError: 400,
        CloneRefusedError: 400,
        EditRefusedError: 400,
        GenerateRefusedError: 400,
        RefusedError: 400,
        ForbiddenError: 403,
        MissingError: 404,
        NotFoundError: 404,
        ConflictError: 409,
        TooSoonError: 429,
        UnavailableError: 502,
        TryAgainError: 503,
    }
)


async def unhandled(request: Request, exc: Exception) -> Response:
    logger.error("%s %s crashed", request.method, request.url.path, exc_info=exc)
    if AuthMiddleware.is_fetch(request):
        return JSONResponse({"detail": MESSAGE}, status_code=500)
    return HTMLResponse(
        f"<p>{MESSAGE}</p>", status_code=500, headers=dict(FRAME_HEADERS)
    )


async def invalid_request(_request: Request, exc: RequestValidationError) -> Response:
    errors = [
        {key: value for key, value in error.items() if key != "input"}
        for error in exc.errors()
    ]
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


async def http_error(request: Request, exc: StarletteHTTPException) -> Response:
    lost = exc.status_code == HTTPStatus.NOT_FOUND and "route" not in request.scope
    if lost and request.method == "GET" and AuthMiddleware.is_page_load(request):
        return RedirectResponse(AuthMiddleware.HOME, status_code=303)
    return await http_exception_handler(request, exc)


Handler = Callable[[Request, Any], Coroutine[Any, Any, Response]]


def _refusal(status_code: int) -> Handler:
    async def answer(_request: Request, exc: Exception) -> Response:
        detail = exc.detail if isinstance(exc, RefusedError) else None
        return JSONResponse(
            {
                "detail": detail.model_dump()
                if isinstance(detail, BaseModel)
                else str(exc)
            },
            status_code=status_code,
        )

    return answer


class ExceptionHandlers(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    handlers: dict[int | type[Exception], Handler]

    @model_serializer
    def _as_given(self) -> dict[int | type[Exception], Handler]:
        return dict(self.handlers)


def exception_handlers() -> ExceptionHandlers:
    return ExceptionHandlers(
        handlers={
            Exception: unhandled,
            StarletteHTTPException: http_error,
            RequestValidationError: invalid_request,
            **{error: _refusal(status) for error, status in REFUSALS.items()},
        }
    )
