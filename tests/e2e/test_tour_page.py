"""End to end, the browser parts of the 2026-11-14 backlog: a VA's /clone
starts its guided tour once per sign-in and Next walks the controls; the
time zone starts as the browser's own and the globe changes it; /clone's
"VAs" button and "Show not uploaded to Drive" switch filter the gallery."""

import re

import pytest
from e2e_session import Session
from playwright.sync_api import expect

pytestmark = pytest.mark.e2e

ADMIN_ID = 1  # e2e conftest `_seed_users`
USER_ID = 2
KEY = "tour:/clone"


def _make_a_va(user_id: int, owner_id: int) -> None:
    """The session's user becomes the admin's VA: the middleware reads the
    person on every request, so the next page is a VA's."""
    from ofmhelpers.db.stores import users
    from ofmhelpers.schemas import Role

    users.set_role(user_id, Role.VA)
    users.set_owner(user_id, owner_id)


def test_a_va_is_toured_through_clone_once_per_sign_in(va: Session):
    _make_a_va(USER_ID, ADMIN_ID)
    tip = va.page.locator("#tour-tip")
    shade = va.page.locator(".tour-shade")

    va.goto("/clone")
    expect(tip).to_be_visible()
    expect(shade).to_be_visible()
    expect(tip.locator(".tour-count")).to_have_text("1 / 4")
    expect(tip.locator(".tour-text")).to_have_text("Pick the picture of our model.")
    # The shade sits on the control the step is about.
    lit = shade.bounding_box()
    subject = va.page.locator('.file-picker[data-field="image"]').bounding_box()
    assert lit is not None
    assert subject is not None
    assert abs(lit["x"] - subject["x"]) < 10
    assert abs(lit["y"] - subject["y"]) < 10
    login = tip.get_attribute("data-login")
    assert login
    assert va.page.evaluate("key => localStorage.getItem(key)", KEY) == login

    for count in ("2 / 4", "3 / 4", "4 / 4"):
        tip.locator(".tour-next").click()
        expect(tip.locator(".tour-count")).to_have_text(count)
    expect(tip.locator(".tour-next")).to_have_text("Done")
    tip.locator(".tour-next").click()
    expect(tip).not_to_be_visible()
    expect(shade).not_to_be_visible()

    # The same sign-in: not again by itself, but the button starts it, and
    # Skip ends it at once.
    va.goto("/clone")
    expect(tip).not_to_be_visible()
    va.page.click(".tour-open")
    expect(tip).to_be_visible()
    tip.locator(".tour-skip").click()
    expect(tip).not_to_be_visible()

    # Shown for an earlier sign-in only: this one shows it again.
    va.page.evaluate("key => localStorage.setItem(key, '1')", KEY)
    va.goto("/clone")
    expect(tip).to_be_visible()
    va.page.keyboard.press("Escape")
    expect(tip).not_to_be_visible()
    assert va.js_errors == []


def test_a_user_starts_the_tour_with_the_button_only(va: Session):
    tip = va.page.locator("#tour-tip")

    va.goto("/download-assets")
    expect(tip).not_to_be_visible()
    va.page.click(".tour-open")

    expect(tip).to_be_visible()
    expect(tip.locator(".tour-text")).to_have_text("Paste the link here and press Run.")
    assert va.page.evaluate("key => localStorage.getItem(key)", KEY) is None
    assert va.js_errors == []


def _zone_cookie(session: Session) -> str | None:
    cookies = {c.get("name"): c.get("value") for c in session.page.context.cookies()}
    return cookies.get("ofm_timezone")


def test_the_zone_starts_as_the_browsers_own_and_the_globe_changes_it(va: Session):
    va.goto("/generate")
    own = va.page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")

    # Nothing picked: the cookie is the browser's zone, "Automatic" checked.
    assert _zone_cookie(va) == own
    va.page.click(".time-zone-menu summary")
    choices = va.page.locator(".time-zone-choice")
    expect(choices.first).to_have_text("Automatic")
    expect(choices.first).to_have_attribute("aria-current", "true")

    va.page.locator('.time-zone-choice[data-zone="Asia/Manila"]').click()
    assert _zone_cookie(va) == "Asia/Manila"
    va.goto("/generate")
    assert _zone_cookie(va) == "Asia/Manila"  # a pick stays over the browser's
    va.page.click(".time-zone-menu summary")
    expect(
        va.page.locator('.time-zone-choice[data-zone="Asia/Manila"]')
    ).to_have_attribute("aria-current", "true")

    va.page.locator(".time-zone-choice").first.click()
    assert _zone_cookie(va) == own
    assert va.js_errors == []


def test_the_vas_button_and_the_not_uploaded_switch_filter_clone(va: Session):
    """The session's user owns a VA: both filters are links, no script."""
    from ofmhelpers.db.stores import users
    from ofmhelpers.db.stores.jobs import create_job
    from ofmhelpers.schemas import Role, UserForm

    helper = users.add_user(
        UserForm(
            email="helper@example.com", role=Role.VA, name="Hana", owner_id=USER_ID
        )
    )
    assert helper is not None
    mine = create_job("clone", {}, actor=str(USER_ID))
    theirs = create_job("clone", {}, actor=str(helper.id))

    va.goto("/clone")
    cards = va.page.locator("#clone-gallery .result-item")
    expect(cards).to_have_count(2)
    va.page.click(".made-by summary")
    va.page.click(".made-by .menu-choice >> text=Hana")

    expect(va.page).to_have_url(re.compile(rf"/clone\?va={helper.id}$"))
    expect(cards).to_have_count(1)
    expect(cards.first).to_have_attribute("data-job-id", theirs)
    expect(va.page.locator(".made-by summary")).to_contain_text("Hana")

    switch = va.page.locator(".not-uploaded")
    expect(switch).to_have_attribute("aria-pressed", "false")
    switch.click()
    # Running jobs are not "finished and left to post": none shows.
    expect(switch).to_have_attribute("aria-pressed", "true")
    expect(cards).to_have_count(0)
    switch.click()
    expect(cards).to_have_count(1)
    assert mine != theirs
    assert va.js_errors == []


def _box(session: Session, selector: str) -> dict[str, float]:
    box = session.page.locator(selector).bounding_box()
    assert box is not None, selector
    return box


def test_the_light_sits_on_each_gen_ai_control_and_the_tip_stays_in_the_window(
    va: Session,
):
    """Owner 2026-11-14: step 3 "is floating and not highlighting correct
    thing". /generate puts a NEW element in place of a model's fields when
    the model loads or changes; the tour held on to the old, removed one."""
    va.goto("/generate")
    va.page.locator("#model-fields .field").first.wait_for()
    tip = va.page.locator("#tour-tip")
    va.page.click(".tour-open")

    targets = [
        "#model-select",
        "#prompt-field",
        "#model-fields",
        '#generate-form button[type="submit"]',
    ]
    window = va.page.viewport_size
    assert window is not None
    for number, selector in enumerate(targets, start=1):
        expect(tip.locator(".tour-count")).to_have_text(f"{number} / 4")
        va.page.wait_for_timeout(400)  # the light follows every 250 ms
        lit, control, said = (
            _box(va, ".tour-shade"),
            _box(va, selector),
            _box(va, "#tour-tip"),
        )
        assert abs(lit["x"] - control["x"]) < 10, selector
        assert abs(lit["y"] - control["y"]) < 10, selector
        assert abs(lit["height"] - control["height"]) < 16, selector
        # The tip is whole inside the window, wherever the control is.
        assert said["x"] >= 0, selector
        assert said["y"] >= 0, selector
        assert said["x"] + said["width"] <= window["width"], selector
        assert said["y"] + said["height"] <= window["height"], selector
        tip.locator(".tour-next").click()
    expect(tip).not_to_be_visible()

    # A model picked while the tour stands on its fields: the light moves to
    # the new element.
    va.page.click(".tour-open")
    tip.locator(".tour-next").click()
    tip.locator(".tour-next").click()
    options = va.page.locator("#model-select option")
    if options.count() > 1:
        va.page.select_option("#model-select", index=1)
    va.page.wait_for_timeout(1200)
    lit, control = _box(va, ".tour-shade"), _box(va, "#model-fields")
    assert abs(lit["y"] - control["y"]) < 10
    assert abs(lit["height"] - control["height"]) < 16
    assert va.js_errors == []
