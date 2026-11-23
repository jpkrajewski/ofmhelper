# Copyright (C) 2026 jpkrajewski

import hashlib
import json
from collections.abc import Callable, Coroutine
from http import HTTPStatus
from typing import Annotated, Any

from fastapi import Depends, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
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
RAW_MAX = 64 * 1024
TOO_LARGE = "Answer too large to replay. The action already ran."
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


RAW = "__idempotent_raw__"


def _is_json(response: Response) -> bool:
    return response.media_type == "application/json"


def _raw_answer(response: Response) -> bytes | None:
    if isinstance(response, (FileResponse, StreamingResponse)):
        return None
    return bytes(response.body)


def _succeeded(response: Response) -> bool:
    return HTTPStatus.OK <= response.status_code < HTTPStatus.MULTIPLE_CHOICES


def _refusal(response: Response) -> bool:
    code = response.status_code
    return HTTPStatus.BAD_REQUEST <= code < HTTPStatus.INTERNAL_SERVER_ERROR


def _try_again(response: Response) -> bool:
    return response.status_code in (
        HTTPStatus.BAD_GATEWAY,
        HTTPStatus.SERVICE_UNAVAILABLE,
    )


def _raw_envelope(text: str, response: Response) -> JsonValue:
    return {RAW: {"text": text, "media_type": response.media_type or ""}}


def _stored_body(response: Response, raw: bytes | None) -> JsonValue:
    if raw is None:
        return _raw_envelope("", response)
    if _is_json(response):
        try:
            return json.loads(raw)
        except ValueError:
            logger.warning("idempotency: answer is not valid JSON", exc_info=True)
    if len(raw) > RAW_MAX:
        logger.warning("idempotency: answer of %s bytes too large to keep", len(raw))
        return _raw_envelope(TOO_LARGE, response)
    return _raw_envelope(raw.decode(errors="replace"), response)


async def _keep(held: Held, response: Response) -> None:
    raw = _raw_answer(response)
    json_answer = raw is not None and _is_json(response)
    if _try_again(response):
        await run_in_threadpool(idempotency.release, held)
    elif response.status_code >= HTTPStatus.INTERNAL_SERVER_ERROR and not json_answer:
        await _finish_crashed(held)
    elif _refusal(response) or (raw is None and not _succeeded(response)):
        await run_in_threadpool(idempotency.release, held)
    else:
        body = _stored_body(response, raw)
        if not json_answer:
            logger.warning("idempotency: %s answered no JSON", held.asked.path)
        await run_in_threadpool(idempotency.finish, held, response.status_code, body)


async def _finish_crashed(held: Held) -> None:
    crashed = HTTPStatus.INTERNAL_SERVER_ERROR
    await run_in_threadpool(idempotency.finish, held, crashed, {"detail": CRASHED})


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
            await _finish_crashed(held)
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
    headers = {REPLAYED: "true"}
    if isinstance(body, dict) and isinstance(raw := body.get(RAW), dict):
        return Response(
            str(raw["text"]),
            status_code=stored,
            media_type=str(raw["media_type"]) or None,
            headers=headers,
        )
    return JSONResponse(body, status_code=code, headers=headers)


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
                    {"detail": BUSY},
                    status_code=409,
                    headers={"Retry-After": "1"},
                )
            if claimed.outcome is Outcome.ABANDONED:
                return JSONResponse({"detail": ABANDONED}, status_code=409)
            return JSONResponse({"detail": MISMATCH}, status_code=400)

        return once
