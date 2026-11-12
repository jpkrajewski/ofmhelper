"""What a request answers when something is raised past its route.

A refusal a feature words for the person (`REFUSALS`) is `{"detail": <its
message>}` with its status, so a route just calls the feature: no try/except
per route, and features never know HTTP. A new refusal is one row. Only errors
whose message is written for the person belong here; anything else stays the
crash below.

A crash answers one plain sentence. Nothing raw reaches the page: the exception
and its traceback are in the logs and the request's trace.
"""

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from ofmhelpers.db.repositories import DuplicateError
from ofmhelpers.features.assets.store import AssetRefusedError
from ofmhelpers.features.clone.service import CloneRefusedError
from ofmhelpers.features.drive.uploads import UploadRefusedError
from ofmhelpers.features.edit.service import EditRefusedError
from ofmhelpers.features.generate.service import GenerateRefusedError
from ofmhelpers.features.refusals import (
    ConflictError,
    ForbiddenError,
    RefusedError,
    TooSoonError,
    TryAgainError,
    UnavailableError,
)
from ofmhelpers.features.refusals import NotFoundError as MissingError
from ofmhelpers.features.trials.folder import NotFoundError
from ofmhelpers.integrations.ai_providers.errors import InvalidInputError
from ofmhelpers.integrations.downloaders.urls import NotALinkError
from ofmhelpers.log import get_logger
from ofmhelpers.web.middleware import AuthMiddleware

logger = get_logger(__name__)

MESSAGE = "Something went wrong. Try again."

REFUSALS: frozendict[type[Exception], int] = frozendict(
    {
        AssetRefusedError: 400,
        CloneRefusedError: 400,
        EditRefusedError: 400,
        GenerateRefusedError: 400,
        InvalidInputError: 400,
        NotALinkError: 400,
        RefusedError: 400,
        UploadRefusedError: 400,
        ForbiddenError: 403,
        MissingError: 404,
        NotFoundError: 404,
        ConflictError: 409,
        DuplicateError: 409,
        TooSoonError: 429,
        UnavailableError: 502,
        TryAgainError: 503,
    }
)


async def unhandled(request: Request, exc: Exception) -> Response:
    logger.error("%s %s crashed", request.method, request.url.path, exc_info=exc)
    if AuthMiddleware.is_fetch(request):
        return JSONResponse({"detail": MESSAGE}, status_code=500)
    return HTMLResponse(f"<p>{MESSAGE}</p>", status_code=500)


async def invalid_request(_request: Request, exc: RequestValidationError) -> Response:
    """FastAPI's own 422 without `input`: a form model's missing field would
    echo the whole form back, API key included."""
    errors = [
        {key: value for key, value in error.items() if key != "input"}
        for error in exc.errors()
    ]
    return JSONResponse({"detail": jsonable_encoder(errors)}, status_code=422)


Handler = Callable[[Request, Any], Coroutine[Any, Any, Response]]


def _refusal(status_code: int) -> Handler:
    async def answer(_request: Request, exc: Exception) -> Response:
        detail = getattr(exc, "detail", None)
        return JSONResponse(
            {
                "detail": detail.model_dump()
                if isinstance(detail, BaseModel)
                else str(exc)
            },
            status_code=status_code,
        )

    return answer


def exception_handlers() -> dict[int | type[Exception], Handler]:
    """`FastAPI(exception_handlers=...)`."""
    return {
        Exception: unhandled,
        RequestValidationError: invalid_request,
        **{error: _refusal(status) for error, status in REFUSALS.items()},
    }
