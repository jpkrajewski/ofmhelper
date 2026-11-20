import json

import pytest
from playwright.sync_api import expect
lazy from e2e_session import Session
lazy from playwright.sync_api import WebSocketRoute

pytestmark = pytest.mark.e2e

SPY = """
window.__notes = [];
window.__chimes = 0;
class FakeNotification {
  static permission = 'granted';
  static requestPermission() { return Promise.resolve('granted'); }
  constructor(title, options) { window.__notes.push({title, tag: options && options.tag}); }
  close() {}
}
window.Notification = FakeNotification;
const realOsc = AudioContext.prototype.createOscillator;
AudioContext.prototype.createOscillator = function () {
  window.__chimes += 0.5;
  return realOsc.call(this);
};
"""
REFUSED_STORAGE = """
Storage.prototype.setItem = function () { throw new Error('refused'); };
"""


def _model(username, *fans):
    return {
        "id": f"waits:stacked:{username}",
        "event_ids": list(fans),
        "kind": "warn",
        "title": f"@{username}",
        "text": f"Chats unanswered ({len(fans)})",
        "url": f"/chatting/stacked/{username}",
    }


def _event(event_id, text, username="model_b"):
    return {
        "id": event_id,
        "event_ids": [event_id],
        "kind": "info",
        "title": f"@{username}",
        "text": text,
        "url": f"/chatting/fanvue/{username}",
    }


class Server:
    def __init__(self, session: Session, *items: dict) -> None:
        self.items = list(items)
        self.opened: list[WebSocketRoute] = []
        session.context.route_web_socket("**/notifications/ws", self._open)

    def _open(self, socket: WebSocketRoute) -> None:
        self.opened.append(socket)
        socket.send(json.dumps({"items": self.items}))

    def send(self, *items: dict) -> None:
        self.items = list(items)
        self.opened[-1].send(json.dumps({"items": self.items}))

    def close(self) -> None:
        self.opened[-1].close()


def _menu(session: Session):
    return session.page.locator(".notifications-menu")


def _count(session: Session):
    return session.page.locator(".notifications-menu .notifications-count")


def _items(session: Session):
    return session.page.locator(".notifications-menu .notifications-item")


def _new_items(session: Session):
    return session.page.locator(".notifications-menu .notifications-item[data-new]")


def _toggle(session: Session) -> None:
    session.page.locator(".notifications-menu > summary").click()


def _notes(session: Session) -> list[str]:
    return [n["title"] for n in session.page.evaluate("window.__notes")]


def _chimes(session: Session) -> float:
    return session.page.evaluate("window.__chimes")


def _unlock_sound(session: Session) -> None:
    session.page.keyboard.press("Shift")


def _start(session: Session, *items: dict) -> Server:
    session.context.add_init_script(SPY)
    server = Server(session, *items)
    session.goto("/accounts")
    return server


def test_one_line_per_model_and_per_event_in_the_order_given(admin: Session):
    _start(
        admin,
        _model("model_a", "f1", "f2", "f3"),
        _event("e1", "New subscriber"),
        _event("e2", "Sold $75 post"),
    )

    expect(_count(admin)).to_have_text("3")
    _toggle(admin)

    expect(_items(admin).locator(".notifications-item-title")).to_have_text(
        ["@model_a", "@model_b", "@model_b"]
    )
    expect(_items(admin).locator(".notifications-item-text")).to_have_text(
        ["Chats unanswered (3)", "New subscriber", "Sold $75 post"]
    )
    assert _items(admin).first.get_attribute("href") == "/chatting/stacked/model_a"
    assert _items(admin).first.get_attribute("data-kind") == "warn"
    assert admin.js_errors == []


def test_each_new_one_notifies_the_desktop_once_and_sound_waits_for_a_gesture(
    admin: Session,
):
    server = _start(admin, _model("model_a", "f1"), _event("e1", "New subscriber"))
    expect(_count(admin)).to_have_text("2")

    assert sorted(_notes(admin)) == [
        "@model_a: Chats unanswered (1)",
        "@model_b: New subscriber",
    ]
    assert _chimes(admin) == 0

    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    server.send(*server.items)
    admin.page.wait_for_timeout(300)
    assert _chimes(admin) == 1
    admin.goto("/phones")
    expect(_count(admin)).to_have_text("2")
    _unlock_sound(admin)
    admin.page.wait_for_timeout(300)
    assert _notes(admin) == []
    assert _chimes(admin) == 0


def test_the_number_moves_on_the_open_page_with_no_new_alert_when_it_falls(
    admin: Session,
):
    server = _start(admin, _model("model_a", "f1", "f2", "f3"))
    expect(_items(admin).locator(".notifications-item-text")).to_have_text(
        ["Chats unanswered (3)"]
    )
    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    server.send(_model("model_a", "f2", "f3"))
    expect(_items(admin).locator(".notifications-item-text")).to_have_text(
        ["Chats unanswered (2)"]
    )

    admin.page.wait_for_timeout(300)
    assert _chimes(admin) == 1
    assert _notes(admin) == ["@model_a: Chats unanswered (3)"]

    server.send()
    expect(_menu(admin)).to_be_hidden()


def test_a_new_fan_alerts_again(admin: Session):
    server = _start(admin, _model("model_a", "f1"))
    expect(_count(admin)).to_have_text("1")
    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    server.send(_model("model_a", "f1", "f2"))
    expect(_items(admin).locator(".notifications-item-text")).to_have_text(
        ["Chats unanswered (2)"]
    )

    admin.page.wait_for_function("window.__chimes === 2")
    assert _notes(admin)[-1] == "@model_a: Chats unanswered (2)"


def test_a_finished_job_shows_like_any_other(admin: Session):
    _start(
        admin,
        {
            "id": "job:abc:done",
            "event_ids": ["job:abc:done"],
            "kind": "info",
            "title": "@ann.ig",
            "text": "Threads pictures ready: VID_1.mp4",
            "url": "/threads/7",
        },
    )

    expect(_count(admin)).to_have_text("1")
    assert _notes(admin) == ["@ann.ig: Threads pictures ready: VID_1.mp4"]
    _toggle(admin)
    assert _items(admin).first.get_attribute("href") == "/threads/7"


def test_opening_the_list_marks_it_seen_until_a_new_fan_waits(admin: Session):
    server = _start(
        admin, _model("model_a", "f1", "f2"), _event("e1", "New subscriber")
    )
    expect(_count(admin)).to_have_text("2")

    _toggle(admin)
    expect(_new_items(admin)).to_have_count(2)
    expect(_count(admin)).to_be_hidden()
    _toggle(admin)
    expect(_new_items(admin)).to_have_count(0)

    admin.goto("/phones")
    expect(_items(admin)).to_have_count(2)
    expect(_count(admin)).to_be_hidden()

    server.send(_model("model_a", "f1", "f2", "f3"), _event("e1", "New subscriber"))
    expect(_count(admin)).to_have_text("1")
    _toggle(admin)
    expect(_new_items(admin).locator(".notifications-item-title")).to_have_text(
        ["@model_a"]
    )


def test_a_seen_event_is_never_counted_again(admin: Session):
    _start(admin, _event("e1", "New subscriber"))
    expect(_count(admin)).to_have_text("1")

    _toggle(admin)
    expect(_count(admin)).to_be_hidden()

    admin.goto("/phones")
    expect(_items(admin)).to_have_count(1)
    expect(_new_items(admin)).to_have_count(0)
    expect(_count(admin)).to_be_hidden()


def test_a_closed_socket_keeps_what_shows_and_opens_again(admin: Session):
    server = _start(admin, _model("model_a", "f1"))
    expect(_items(admin)).to_have_count(1)

    server.items = [_model("model_a", "f1", "f2")]
    server.close()
    expect(_items(admin)).to_have_count(1)
    expect(_count(admin)).to_have_text("1")

    expect(_items(admin).locator(".notifications-item-text")).to_have_text(
        ["Chats unanswered (2)"], timeout=10_000
    )
    assert len(server.opened) == 2


def test_a_browser_refusing_storage_still_remembers_what_was_seen(admin: Session):
    admin.context.add_init_script(REFUSED_STORAGE)
    server = _start(admin, _model("model_a", "f1"))
    expect(_count(admin)).to_have_text("1")
    notes = len(_notes(admin))

    _toggle(admin)
    expect(_count(admin)).to_be_hidden()
    _toggle(admin)
    server.send(*server.items)
    admin.page.wait_for_timeout(300)

    expect(_count(admin)).to_be_hidden()
    expect(_new_items(admin)).to_have_count(0)
    assert len(_notes(admin)) == notes


def test_an_empty_notifications_stays_hidden(va: Session):
    server = Server(va)

    va.goto("/home")

    expect(_menu(va)).to_have_count(1)
    expect(_menu(va)).to_be_hidden()
    assert len(server.opened) == 1
