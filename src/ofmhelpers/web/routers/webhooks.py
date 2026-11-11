"""
Outside services calling in, one route for every chat platform: what differs is in
each platform's `ChatSource`. Public (`settings.web.public_prefixes`) and left out of
the write rate limit, since a mass message can deliver hundreds at once: what guards
these is each delivery's signature, not a session. So the body is capped before it is
read, and an address sending deliveries that fail the signature is turned away.

A platform set up on its own site sends to whatever address was typed there, so a path
under the platform's is heard too (seen 2026-11-05: Fanvue at
/events/fanvue/creator-events, a 404 until then).

An AI provider's callback (`/events/ai/<provider>/<job key>`,
`features/generate/callbacks.py`) names the request it made for a job: what
finds one whose submit answer was lost.
"""

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
    """The raw bytes as they came, the signature covers those; None past the cap,
    which a false Content-Length does not get around."""
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
    """A 2xx tells the platform the event is stored; anything else, it retries."""
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
    """A 2xx ends the provider's delivery: a genuine callback is never answered
    with an error (kie.ai's retries are few or undocumented)."""
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
