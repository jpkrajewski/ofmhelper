"""The chat pop-ups in a real browser, on any page. Their answer is given here, every
0.2 s, so what the JavaScript does with it is what is tested: what the server answers
is covered in tests/integration/web/test_chat_platforms.py. Sound and desktop
notifications are counted: there is no speaker and no desktop here."""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING

import pytest
from playwright.sync_api import expect

if TYPE_CHECKING:
    from e2e_session import Session

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
        "text": f"Chats unanswered ({len(fans)})",
        "username": username,
        "url": f"/chatting/stacked/{username}",
    }


def _event(event_id, text, username="model_b"):
    return {
        "id": event_id,
        "event_ids": [event_id],
        "kind": "info",
        "text": text,
        "username": username,
        "url": f"/chatting/fanvue/{username}",
    }


class Answer:
    def __init__(self, session: Session, *popups: dict) -> None:
        self.popups = list(popups)
        self.status = 200
        session.context.route("**/chatting/alerts", self._reply)

    def _reply(self, route) -> None:
        route.fulfill(
            status=self.status,
            content_type="application/json",
            body=json.dumps({"popups": self.popups, "check_every_s": 0.2}),
        )


def _shown(session: Session):
    return session.page.locator(".chat-popup")


def _notes(session: Session) -> list[str]:
    return [n["title"] for n in session.page.evaluate("window.__notes")]


def _chimes(session: Session) -> float:
    return session.page.evaluate("window.__chimes")


def _unlock_sound(session: Session) -> None:
    _shown(session).first.locator(".chat-popup-text").click()


def _start(session: Session, *popups: dict) -> Answer:
    session.context.add_init_script(SPY)
    answer = Answer(session, *popups)
    session.goto("/accounts")
    return answer


def test_one_popup_per_model_and_per_event_in_the_order_given(admin: Session):
    _start(
        admin,
        _model("model_a", "f1", "f2", "f3"),
        _event("e1", "New subscriber"),
        _event("e2", "Sold $75 post"),
    )

    expect(_shown(admin)).to_have_text(
        [
            "Chats unanswered (3): @model_a",
            "New subscriber: @model_b",
            "Sold $75 post: @model_b",
        ]
    )
    expect(_shown(admin).first).to_have_class(re.compile("notice-warn"))
    link = _shown(admin).first.locator("a")
    assert link.get_attribute("href") == "/chatting/stacked/model_a"
    assert admin.js_errors == []


def test_each_popup_notifies_the_desktop_once_and_sound_waits_for_a_click(
    admin: Session,
):
    _start(admin, _model("model_a", "f1"), _event("e1", "New subscriber"))
    expect(_shown(admin)).to_have_count(2)

    assert sorted(_notes(admin)) == [
        "Chats unanswered (1): @model_a",
        "New subscriber: @model_b",
    ]
    assert _chimes(admin) == 0

    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    # Next ask, and next page: not again.
    admin.page.wait_for_timeout(600)
    assert _chimes(admin) == 1
    admin.goto("/phones")
    expect(_shown(admin)).to_have_count(2)
    _unlock_sound(admin)
    admin.page.wait_for_timeout(600)
    assert _notes(admin) == []
    assert _chimes(admin) == 0


def test_the_number_moves_on_the_open_page_with_no_new_alert_when_it_falls(
    admin: Session,
):
    answer = _start(admin, _model("model_a", "f1", "f2", "f3"))
    expect(_shown(admin)).to_have_text(["Chats unanswered (3): @model_a"])
    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")
    admin.page.evaluate("document.querySelector('.chat-popup').__first = true")

    answer.popups = [_model("model_a", "f2", "f3")]
    expect(_shown(admin)).to_have_text(["Chats unanswered (2): @model_a"])

    assert admin.page.evaluate("document.querySelector('.chat-popup').__first") is True
    admin.page.wait_for_timeout(400)
    assert _chimes(admin) == 1
    assert _notes(admin) == ["Chats unanswered (3): @model_a"]

    answer.popups = []
    expect(_shown(admin)).to_have_count(0)


def test_a_new_fan_alerts_again(admin: Session):
    answer = _start(admin, _model("model_a", "f1"))
    expect(_shown(admin)).to_have_count(1)
    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    answer.popups = [_model("model_a", "f1", "f2")]
    expect(_shown(admin)).to_have_text(["Chats unanswered (2): @model_a"])

    admin.page.wait_for_function("window.__chimes === 2")
    assert _notes(admin)[-1] == "Chats unanswered (2): @model_a"


def test_closed_stays_closed_until_a_new_fan_waits(admin: Session):
    answer = _start(
        admin, _model("model_a", "f1", "f2"), _event("e1", "New subscriber")
    )
    expect(_shown(admin)).to_have_count(2)

    _shown(admin).first.locator(".chat-popup-dismiss").click()
    expect(_shown(admin)).to_have_text(["New subscriber: @model_b"])
    admin.page.wait_for_timeout(500)
    expect(_shown(admin)).to_have_count(1)

    admin.goto("/phones")
    expect(_shown(admin)).to_have_text(["New subscriber: @model_b"])

    answer.popups = [
        _model("model_a", "f1", "f2", "f3"),
        _event("e1", "New subscriber"),
    ]
    expect(_shown(admin)).to_have_text(
        ["Chats unanswered (3): @model_a", "New subscriber: @model_b"]
    )


def test_a_closed_event_never_comes_back(admin: Session):
    _start(admin, _event("e1", "New subscriber"))
    expect(_shown(admin)).to_have_count(1)

    _shown(admin).first.locator(".chat-popup-dismiss").click()
    expect(_shown(admin)).to_have_count(0)

    admin.goto("/phones")
    admin.page.wait_for_timeout(500)
    expect(_shown(admin)).to_have_count(0)


def test_a_failing_ask_keeps_what_shows_and_is_tried_again(admin: Session):
    answer = _start(admin, _model("model_a", "f1"))
    expect(_shown(admin)).to_have_count(1)

    answer.status = 500
    admin.page.wait_for_timeout(600)
    expect(_shown(admin)).to_have_count(1)

    answer.status = 200
    answer.popups = [_model("model_a", "f1", "f2")]
    expect(_shown(admin)).to_have_text(["Chats unanswered (2): @model_a"])


def test_a_browser_refusing_storage_still_remembers_what_was_closed(admin: Session):
    admin.context.add_init_script(REFUSED_STORAGE)
    _start(admin, _model("model_a", "f1"))
    expect(_shown(admin)).to_have_count(1)
    notes = len(_notes(admin))

    _shown(admin).first.locator(".chat-popup-dismiss").click()
    expect(_shown(admin)).to_have_count(0)
    admin.page.wait_for_timeout(600)

    expect(_shown(admin)).to_have_count(0)
    assert len(_notes(admin)) == notes


def test_someone_who_is_not_admin_never_asks(va: Session):
    asked = []
    va.context.route(
        "**/chatting/alerts", lambda route: (asked.append(1), route.abort())
    )

    va.goto("/accounts")
    va.page.wait_for_timeout(500)

    assert asked == []
    expect(_shown(va)).to_have_count(0)
