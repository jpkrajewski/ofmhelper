"""
/chatting (admins, the credentials here are the agency's): the chat platforms, each
one's models, and a model's average response time with every wait that went too long.
Pages read what webhooks and the sync stored (`features/chatting/`), so none waits on
a platform. Fanvue has no API keys: its page connects an account.
"""

from typing import Annotated

from fastapi import APIRouter, Cookie, Request
from fastapi.responses import Response

from ofmhelpers.features.chatting.alerts import chat_alerts
from ofmhelpers.features.chatting.fanvue_connect import can_connect, connect_url, finish
from ofmhelpers.features.chatting.recent import recent_models
from ofmhelpers.features.chatting.service import accounts_to_reconnect, open_model
from ofmhelpers.features.chatting.service import platform_models as models_of
from ofmhelpers.schemas.chatting import ChatAlerts, ChatPlatform
from ofmhelpers.schemas.time_zones import time_zone
from ofmhelpers.web.dependencies import AdminOnly, OwnerId
from ofmhelpers.web.responses import see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/chatting", tags=["chatting"], dependencies=[AdminOnly])

_NOTES = {
    "connected": ("Fanvue connected.", "ok"),
    "failed": ("Could not connect Fanvue. Try again.", "fail"),
}


@router.get("")
def platforms(request: Request, owner_id: OwnerId) -> Response:
    return render(
        request,
        "chatting.html",
        platforms=list(ChatPlatform),
        recent=recent_models(owner_id),
    )


@router.get("/alerts")
def alerts() -> ChatAlerts:
    """What every admin page's chat pop-ups name."""
    return chat_alerts()


@router.get("/fanvue/connect")
def fanvue_connect() -> Response:
    if not can_connect():
        return see_other("/chatting/fanvue?note=failed")
    return see_other(connect_url())


@router.get("/fanvue/callback")
def fanvue_callback(code: str | None = None, state: str | None = None) -> Response:
    """Where Fanvue sends the browser back. Without a code they said no."""
    connected = bool(code and state) and finish(code or "", state or "")
    return see_other(f"/chatting/fanvue?note={'connected' if connected else 'failed'}")


@router.get("/{platform}")
def models(
    request: Request, platform: ChatPlatform, note: str | None = None
) -> Response:
    is_fanvue = platform is ChatPlatform.FANVUE
    return render(
        request,
        "chatting_models.html",
        platform=platform,
        models=models_of(platform),
        can_connect=is_fanvue and can_connect(),
        reconnect=accounts_to_reconnect(platform),
        note=_NOTES.get(note or ""),
    )


@router.get("/{platform}/{username}")
def model(
    request: Request,
    platform: ChatPlatform,
    username: str,
    owner_id: OwnerId,
    ofm_timezone: Annotated[str | None, Cookie()] = None,
) -> Response:
    zone = time_zone(ofm_timezone).zone
    return render(
        request,
        "chatting_model.html",
        platform=platform,
        report=open_model(owner_id, platform, username, zone),
    )
