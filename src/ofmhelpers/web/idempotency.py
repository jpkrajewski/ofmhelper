import hashlib
import json
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException
lazy from pydantic import JsonValue
lazy from starlette.datastructures import UploadFile

from ofmhelpers.db.idempotency_keys.schema import Asked, Held, Outcome
from ofmhelpers.features.idempotency import service as idempotency
from ofmhelpers.features.refusals import RefusedError
from ofmhelpers.log import get_logger
from ofmhelpers.web.exceptions import REFUSALS
from ofmhelpers.web.middleware import AuthMiddleware

logger = get_logger(__name__)

HEADER = "Idempotency-Key"
REPLAYED = "Idempotent-Replayed"
KEY_MAX = 255
FILE_SAMPLE = 64 * 1024
NO_KEY = "Send an Idempotency-Key header, one per action"
BUSY = "This request is still being handled"
ABANDONED = "This request did not finish. Reload the page and try again."
CRASHED = "Something went wrong. Reload the page and try again."
MISMATCH = "This Idempotency-Key was used for a different request"
_FORMS = ("multipart/form-data", "application/x-www-form-urlencoded")

Handler = Callable[[Request], Coroutine[Any, Any, Response]]


def _key_required(
    idempotency_key: Annotated[str | None, Header(alias=HEADER)] = None,
) -> None:
    if not idempotency_key or len(idempotency_key) > KEY_MAX:
        raise RefusedError(NO_KEY)


Idempotent = Depends(_key_required)


def marked(route: APIRoute) -> bool:
    return any(d.call is _key_required for d in route.dependant.dependencies)


def _field(name: str, value: str | UploadFile) -> str:
    if isinstance(value, str):
        return json.dumps([name, value])
    value.file.seek(0)
    head = hashlib.sha256(value.file.read(FILE_SAMPLE)).hexdigest()
    value.file.seek(0)
    return json.dumps([name, f"file:{value.filename}:{value.size}:{head}"])


async def _fields(request: Request) -> list[str]:
    if request.headers.get("content-type", "").startswith(_FORMS):
        form = await request.form()
        return [_field(name, value) for name, value in form.multi_items()]
    return [(await request.body()).decode(errors="replace")]


async def _asked(request: Request, key: str) -> Asked:
    return Asked(
        user_id=AuthMiddleware.user(request).id,
        key=key,
        path=f"{request.method} {request.url.path}",
        fingerprint=idempotency.fingerprint(await _fields(request)),
    )


def _answer(response: Response) -> bytes | None:
    raw = getattr(response, "body", None)
    code = response.status_code
    refused = HTTPStatus.BAD_REQUEST <= code < HTTPStatus.INTERNAL_SERVER_ERROR
    if refused or response.media_type != "application/json" or raw is None:
        return None
    return bytes(raw)


async def _keep(held: Held, response: Response) -> None:
    raw = _answer(response)
    if raw is None:
        await run_in_threadpool(idempotency.release, held)
        return
    try:
        body = json.loads(raw)
    except ValueError:
        path = held.asked.path
        logger.warning("idempotency: %s answered no JSON", path, exc_info=True)
        await run_in_threadpool(idempotency.release, held)
        return
    await run_in_threadpool(idempotency.finish, held, response.status_code, body)


def _refused(exc: BaseException) -> bool:
    if isinstance(exc, RequestValidationError):
        return True
    if isinstance(exc, StarletteHTTPException):
        return exc.status_code < HTTPStatus.INTERNAL_SERVER_ERROR
    return any(isinstance(exc, kind) for kind in REFUSALS)


async def _settle_crash(held: Held, exc: BaseException) -> None:
    try:
        if _refused(exc):
            await run_in_threadpool(idempotency.release, held)
        else:
            crashed = HTTPStatus.INTERNAL_SERVER_ERROR
            await run_in_threadpool(
                idempotency.finish, held, crashed, {"detail": CRASHED}
            )
    except Exception:
        logger.exception("idempotency: key of %s not settled", held.asked.path)


async def _first(handle: Handler, request: Request, held: Held) -> Response:
    try:
        response = await handle(request)
    except BaseException as exc:
        await _settle_crash(held, exc)
        raise
    try:
        await _keep(held, response)
    except Exception:
        logger.exception("idempotency: answer to %s not kept", held.asked.path)
    return response


def _replayed(status: int | None, body: JsonValue) -> Response:
    stored = status or HTTPStatus.OK
    code = HTTPStatus.OK if stored < HTTPStatus.MULTIPLE_CHOICES else stored
    return JSONResponse(body, status_code=code, headers={REPLAYED: "true"})


class IdempotentRoute(APIRoute):
    def get_route_handler(self) -> Handler:
        handle = super().get_route_handler()
        if not marked(self):
            return handle

        async def once(request: Request) -> Response:
            key = request.headers.get(HEADER)
            if not key or len(key) > KEY_MAX:
                return await handle(request)
            asked = await _asked(request, key)
            claimed = await run_in_threadpool(idempotency.claim, asked)
            if claimed.held is not None:
                return await _first(handle, request, claimed.held)
            logger.info(
                "idempotency: %s %s for user %s",
                claimed.outcome,
                asked.path,
                asked.user_id,
            )
            if claimed.outcome is Outcome.REPLAY:
                return _replayed(claimed.response_status, claimed.response_body)
            if claimed.outcome is Outcome.BUSY:
                return JSONResponse(
                    {"detail": BUSY}, status_code=409, headers={"Retry-After": "1"}
                )
            if claimed.outcome is Outcome.ABANDONED:
                return JSONResponse({"detail": ABANDONED}, status_code=409)
            return JSONResponse({"detail": MISMATCH}, status_code=400)

        return once
