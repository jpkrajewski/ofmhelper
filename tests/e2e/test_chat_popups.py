"""The header's chats menu in a real browser, on any page (components/ui.html
`chats_menu`, static/js/chats-alert.js; pop-ups over the page until 2026-11-18).
Its answer is given here, every 0.2 s, so what the JavaScript does with it is
what is tested: what the server answers is covered in
tests/integration/web/test_chat_platforms.py. Sound and desktop notifications
are counted: there is no speaker and no desktop here."""

import json

import pytest
from playwright.sync_api import expect
lazy from e2e_session import Session

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


def _menu(session: Session):
    return session.page.locator(".chats-menu")


def _count(session: Session):
    return session.page.locator(".chats-menu .chats-count")


def _items(session: Session):
    return session.page.locator(".chats-menu .chats-item")


def _new_items(session: Session):
    return session.page.locator(".chats-menu .chats-item[data-new]")


def _toggle(session: Session) -> None:
    session.page.locator(".chats-menu > summary").click()


def _notes(session: Session) -> list[str]:
    return [n["title"] for n in session.page.evaluate("window.__notes")]


def _chimes(session: Session) -> float:
    return session.page.evaluate("window.__chimes")


def _unlock_sound(session: Session) -> None:
    """A key press: the first gesture allows sound (notify.js). Not a click on
    the menu, which would also mark everything seen."""
    session.page.keyboard.press("Shift")


def _start(session: Session, *popups: dict) -> Answer:
    session.context.add_init_script(SPY)
    answer = Answer(session, *popups)
    session.goto("/accounts")
    return answer


def test_one_line_per_model_and_per_event_in_the_order_given(admin: Session):
    _start(
        admin,
        _model("model_a", "f1", "f2", "f3"),
        _event("e1", "New subscriber"),
        _event("e2", "Sold $75 post"),
    )

    expect(_count(admin)).to_have_text("3")
    _toggle(admin)

    expect(_items(admin).locator(".chats-item-who")).to_have_text(
        ["@model_a", "@model_b", "@model_b"]
    )
    expect(_items(admin).locator(".chats-item-text")).to_have_text(
        ["Chats unanswered (3)", "New subscriber", "Sold $75 post"]
    )
    assert _items(admin).first.get_attribute("href") == "/chatting/stacked/model_a"
    assert _items(admin).first.get_attribute("data-kind") == "warn"
    assert admin.js_errors == []


def test_each_new_one_notifies_the_desktop_once_and_sound_waits_for_a_gesture(
    admin: Session,
):
    _start(admin, _model("model_a", "f1"), _event("e1", "New subscriber"))
    expect(_count(admin)).to_have_text("2")

    assert sorted(_notes(admin)) == [
        "Chats unanswered (1): @model_a",
        "New subscriber: @model_b",
    ]
    assert _chimes(admin) == 0

    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    # Next ask, and next page: not again, though both still count as new.
    admin.page.wait_for_timeout(600)
    assert _chimes(admin) == 1
    admin.goto("/phones")
    expect(_count(admin)).to_have_text("2")
    _unlock_sound(admin)
    admin.page.wait_for_timeout(600)
    assert _notes(admin) == []
    assert _chimes(admin) == 0


def test_the_number_moves_on_the_open_page_with_no_new_alert_when_it_falls(
    admin: Session,
):
    answer = _start(admin, _model("model_a", "f1", "f2", "f3"))
    expect(_items(admin).locator(".chats-item-text")).to_have_text(
        ["Chats unanswered (3)"]
    )
    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    answer.popups = [_model("model_a", "f2", "f3")]
    expect(_items(admin).locator(".chats-item-text")).to_have_text(
        ["Chats unanswered (2)"]
    )

    admin.page.wait_for_timeout(400)
    assert _chimes(admin) == 1
    assert _notes(admin) == ["Chats unanswered (3): @model_a"]

    answer.popups = []
    expect(_menu(admin)).to_be_hidden()


def test_a_new_fan_alerts_again(admin: Session):
    answer = _start(admin, _model("model_a", "f1"))
    expect(_count(admin)).to_have_text("1")
    _unlock_sound(admin)
    admin.page.wait_for_function("window.__chimes === 1")

    answer.popups = [_model("model_a", "f1", "f2")]
    expect(_items(admin).locator(".chats-item-text")).to_have_text(
        ["Chats unanswered (2)"]
    )

    admin.page.wait_for_function("window.__chimes === 2")
    assert _notes(admin)[-1] == "Chats unanswered (2): @model_a"


def test_opening_the_list_marks_it_seen_until_a_new_fan_waits(admin: Session):
    answer = _start(
        admin, _model("model_a", "f1", "f2"), _event("e1", "New subscriber")
    )
    expect(_count(admin)).to_have_text("2")

    _toggle(admin)
    # Open: still marked in front of the reader, no longer counted.
    expect(_new_items(admin)).to_have_count(2)
    expect(_count(admin)).to_be_hidden()
    _toggle(admin)
    expect(_new_items(admin)).to_have_count(0)

    admin.goto("/phones")
    expect(_items(admin)).to_have_count(2)
    expect(_count(admin)).to_be_hidden()

    answer.popups = [
        _model("model_a", "f1", "f2", "f3"),
        _event("e1", "New subscriber"),
    ]
    expect(_count(admin)).to_have_text("1")
    _toggle(admin)
    expect(_new_items(admin).locator(".chats-item-who")).to_have_text(["@model_a"])


def test_a_seen_event_is_never_counted_again(admin: Session):
    _start(admin, _event("e1", "New subscriber"))
    expect(_count(admin)).to_have_text("1")

    _toggle(admin)
    expect(_count(admin)).to_be_hidden()

    admin.goto("/phones")
    admin.page.wait_for_timeout(500)
    expect(_items(admin)).to_have_count(1)
    expect(_new_items(admin)).to_have_count(0)
    expect(_count(admin)).to_be_hidden()


def test_a_failing_ask_keeps_what_shows_and_is_tried_again(admin: Session):
    answer = _start(admin, _model("model_a", "f1"))
    expect(_items(admin)).to_have_count(1)

    answer.status = 500
    admin.page.wait_for_timeout(600)
    expect(_items(admin)).to_have_count(1)
    expect(_count(admin)).to_have_text("1")

    answer.status = 200
    answer.popups = [_model("model_a", "f1", "f2")]
    expect(_items(admin).locator(".chats-item-text")).to_have_text(
        ["Chats unanswered (2)"]
    )


def test_a_browser_refusing_storage_still_remembers_what_was_seen(admin: Session):
    admin.context.add_init_script(REFUSED_STORAGE)
    _start(admin, _model("model_a", "f1"))
    expect(_count(admin)).to_have_text("1")
    notes = len(_notes(admin))

    _toggle(admin)
    expect(_count(admin)).to_be_hidden()
    _toggle(admin)
    admin.page.wait_for_timeout(600)

    expect(_count(admin)).to_be_hidden()
    expect(_new_items(admin)).to_have_count(0)
    assert len(_notes(admin)) == notes


def test_someone_who_is_not_admin_never_asks(va: Session):
    asked = []
    va.context.route(
        "**/chatting/alerts", lambda route: (asked.append(1), route.abort())
    )

    va.goto("/accounts")
    va.page.wait_for_timeout(500)

    assert asked == []
    expect(_menu(va)).to_have_count(0)
