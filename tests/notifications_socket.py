from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from ofmhelpers.db.stores import users
from ofmhelpers.features.notifications.service import read
from ofmhelpers.features.users.viewing import viewer_for
from ofmhelpers.schemas.notifications import Notification

SOCKET = "/notifications/ws"


def agency_notifications() -> list[Notification]:
    admin = users.get_user(users.agency_id())
    assert admin is not None
    return read(viewer_for(admin, None)).items


def first_sent(client: TestClient) -> list[dict]:
    with client.websocket_connect(SOCKET) as socket:
        return socket.receive_json()["items"]


def next_sent(socket: WebSocketTestSession, within_s: float = 10.0) -> list[dict]:
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        return pool.submit(socket.receive_json).result(timeout=within_s)["items"]
    finally:
        pool.shutdown(wait=False)


def next_lines(socket: WebSocketTestSession) -> list[dict]:
    lines = next_sent(socket)
    while not lines:
        lines = next_sent(socket)
    return lines
