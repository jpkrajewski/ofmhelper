# Copyright (C) 2026 jpkrajewski

from fastapi import APIRouter, WebSocket

from ofmhelpers.features.notifications.service import updates
from ofmhelpers.web.dependencies import SignedIn
from ofmhelpers.web.sockets import stream

router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.websocket("/ws")
async def notifications_socket(websocket: WebSocket, signed_in: SignedIn) -> None:
    await websocket.accept()
    await stream(websocket, updates(signed_in))
