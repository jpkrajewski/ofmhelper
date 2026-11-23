# Copyright (C) 2026 jpkrajewski

import asyncio
lazy from collections.abc import AsyncIterator

from fastapi import WebSocket, WebSocketDisconnect, status
lazy from pydantic import BaseModel


async def _send_until_signed_out(
    websocket: WebSocket,
    updates: AsyncIterator[BaseModel],
) -> bool:
    try:
        async for update in updates:
            await websocket.send_text(update.model_dump_json())
    except WebSocketDisconnect:  # silent: the browser left mid-send
        return False
    return True


async def _until_gone(websocket: WebSocket) -> None:
    while (await websocket.receive())["type"] != "websocket.disconnect":
        pass


async def _settled(task: asyncio.Task) -> None:
    try:
        await task
    except asyncio.CancelledError:  # silent: cancelled here, its work is over
        return


async def stream(websocket: WebSocket, updates: AsyncIterator[BaseModel]) -> None:
    sending = asyncio.create_task(_send_until_signed_out(websocket, updates))
    leaving = asyncio.create_task(_until_gone(websocket))
    done, _ = await asyncio.wait(
        {sending, leaving},
        return_when=asyncio.FIRST_COMPLETED,
    )
    other = leaving if sending in done else sending
    other.cancel()
    await _settled(other)
    if sending in done and sending.result():
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
