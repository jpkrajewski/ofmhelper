from fastapi import APIRouter, Request
from fastapi.responses import Response

from ofmhelpers.db.chats.schema import ChatPlatform
from ofmhelpers.features.chatting.fanvue_connect import can_connect, connect_url, finish
from ofmhelpers.features.chatting.recent import recent_models
from ofmhelpers.features.chatting.service import (
    accounts_to_reconnect,
    model_report,
    remember_visit,
)
from ofmhelpers.features.chatting.service import platform_models as models_of
from ofmhelpers.web.access import Action, Area
from ofmhelpers.web.dependencies import (
    Access,
    Can,
    CurrentUser,
    OwnerId,
    SessionId,
    Zone,
)
from ofmhelpers.web.responses import see_other
from ofmhelpers.web.templates_config import render

router = APIRouter(
    prefix="/chatting", tags=["chatting"], dependencies=[Access(Area.CHATTING)]
)

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


@router.get("/fanvue/connect", dependencies=[Can(Action.CONNECT_CHATS)])
def fanvue_connect(user: CurrentUser, session_id: SessionId) -> Response:
    return see_other(connect_url(user.id, session_id) or "/chatting/fanvue?note=failed")


@router.get("/fanvue/callback", dependencies=[Can(Action.CONNECT_CHATS)])
def fanvue_callback(
    user: CurrentUser,
    session_id: SessionId,
    code: str | None = None,
    state: str | None = None,
) -> Response:
    connected = bool(code and state) and finish(
        user.id, session_id, code or "", state or ""
    )
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
    request: Request, platform: ChatPlatform, username: str, zone: Zone
) -> Response:
    return render(
        request,
        "chatting_model.html",
        platform=platform,
        report=model_report(platform, username, zone.zone),
    )


@router.post("/{platform}/{username}/visits", status_code=204)
def model_visit(platform: ChatPlatform, username: str, owner_id: OwnerId) -> Response:
    remember_visit(owner_id, platform, username)
    return Response(status_code=204)
