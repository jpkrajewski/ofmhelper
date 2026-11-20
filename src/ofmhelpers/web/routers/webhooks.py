from fastapi import APIRouter, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import Response

from ofmhelpers.config import settings
from ofmhelpers.features.chatting.sources import source_for
from ofmhelpers.features.chatting.webhooks import receive
from ofmhelpers.features.generate import callbacks
from ofmhelpers.schemas.chatting import ChatPlatform
from ofmhelpers.web.middleware.ratelimit import record_refused_webhook, webhook_blocked

router = APIRouter(prefix="/events", tags=["events"])


async def _capped_body(request: Request) -> bytes | None:
    limit = settings.chatting.webhook_max_body_bytes
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > limit:
            return None
    return bytes(body)


@router.post("/{platform}")
@router.post("/{platform}/{destination}")
async def delivery(platform: ChatPlatform, request: Request) -> Response:
    source = source_for(platform)
    if source is None:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if await run_in_threadpool(webhook_blocked, request):
        return Response(status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    raw = await _capped_body(request)
    if raw is None:
        return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    if await run_in_threadpool(receive, source, raw, request.headers):
        return Response(status_code=status.HTTP_200_OK)
    await run_in_threadpool(record_refused_webhook, request)
    return Response(status_code=status.HTTP_401_UNAUTHORIZED)


@router.post("/ai/{provider}/{job_key}")
async def provider_callback(provider: str, job_key: str, request: Request) -> Response:
    if await run_in_threadpool(webhook_blocked, request):
        return Response(status_code=status.HTTP_429_TOO_MANY_REQUESTS)
    raw = await _capped_body(request)
    if raw is None:
        return Response(status_code=status.HTTP_413_CONTENT_TOO_LARGE)
    heard = await run_in_threadpool(
        callbacks.receive, provider, job_key, raw, request.headers
    )
    if not heard.known:
        return Response(status_code=status.HTTP_404_NOT_FOUND)
    if not heard.genuine:
        await run_in_threadpool(record_refused_webhook, request)
        return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    return Response(status_code=status.HTTP_200_OK)
