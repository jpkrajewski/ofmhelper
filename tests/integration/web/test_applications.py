"""
Covers the public landing-page application form (routers/apply.py) and its
admin-only CRM view (routers/admin/applications.py): submitting without a
session, storing every field, and who may read the list.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.web.db.repositories import ApplicationRepository
from ofmhelpers.web.main import app
from ofmhelpers.web.routers.apply import form_token
from ofmhelpers.web.stores import applications as applications_store


@pytest.fixture(autouse=True)
def _no_fill_wait(monkeypatch):
    """The form's "too fast" trap off: a test submits in milliseconds."""
    monkeypatch.setenv("OFM_APPLY_MIN_FILL_S", "0")


def _form(**changes) -> dict:
    """FORM as the served page posts it: with its form token."""
    return {**FORM, "form_token": form_token(), **changes}


FORM = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "+48 123 456 789",
    "telegram_handle": "@adalovelace",
    "instagram_handle": "@ada",
    "monthly_revenue": "$5,000 - $10,000",
    "experience_level": "Some Experience",
    "goals": "Scale to six figures.",
}


@pytest.fixture
def client():
    c = TestClient(app)
    c.post("/login", data={"password": "test-admin", "next": "/"})
    return c


@pytest.fixture
def va_client():
    c = TestClient(app)
    sign_in(c)
    return c


def test_anonymous_submit_stores_every_field():
    anon = TestClient(app)

    r = anon.post("/apply", data=_form(), follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?applied=1"

    stored = applications_store.list_applications()[0]
    for key, value in FORM.items():
        assert getattr(stored, key) == value


def test_thank_you_modal_only_shows_after_a_submission():
    anon = TestClient(app)

    # The closing script always ships; only the dialog itself is conditional.
    assert 'id="rm-thanks"' not in anon.get("/").text

    applied = anon.get("/?applied=1").text
    assert 'id="rm-thanks"' in applied
    assert "We'll contact you as soon as possible" in applied


def test_missing_required_fields_are_rejected():
    anon = TestClient(app)

    assert anon.post("/apply", data={"first_name": "Ada"}).status_code == 422
    assert (
        anon.post("/apply", data={**_form(), "email": "not-an-address"}).status_code
        == 422
    )
    assert anon.post("/apply", data={**_form(), "first_name": "  "}).status_code == 422


def test_optional_fields_may_be_blank():
    anon = TestClient(app)

    r = anon.post(
        "/apply",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "g@example.com",
            "phone": "+1 555 0100",
            "form_token": form_token(),
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    stored = applications_store.list_applications()[0]
    assert stored.telegram_handle == ""
    assert stored.instagram_handle == ""
    assert stored.monthly_revenue == ""
    assert stored.goals == ""


def test_phone_or_telegram_is_required_but_either_one_suffices():
    anon = TestClient(app)

    no_channel = {**_form(), "phone": "", "telegram_handle": ""}
    assert anon.post("/apply", data=no_channel).status_code == 422
    assert anon.post("/apply", data={**no_channel, "phone": "  "}).status_code == 422

    telegram_only = {**no_channel, "telegram_handle": "@grace"}
    assert (
        anon.post("/apply", data=telegram_only, follow_redirects=False).status_code
        == 303
    )
    stored = applications_store.list_applications()[0]
    assert stored.telegram_handle == "@grace"
    assert stored.phone == ""


def test_crm_page_lists_submissions_and_deletes(client):
    TestClient(app).post("/apply", data=_form())

    html = client.get("/applications").text
    assert "Ada" in html
    assert "ada@example.com" in html
    assert "+48 123 456 789" in html
    assert "https://t.me/ada" in html
    assert "Scale to six figures." in html

    application_id = applications_store.list_applications()[0].id
    r = client.post(f"/applications/{application_id}/delete", follow_redirects=False)
    assert r.status_code == 303
    assert applications_store.list_applications() == []

    assert client.post(f"/applications/{application_id}/delete").status_code == 404


def test_crm_page_is_admin_only(va_client):
    assert va_client.get("/applications").status_code == 403
    assert va_client.post("/applications/xyz/delete").status_code == 403

    anon = TestClient(app).get("/applications", follow_redirects=False)
    assert anon.status_code == 303
    assert anon.headers["location"].startswith("/login")


def test_crm_page_lists_a_lead_stored_before_the_contact_rule(client):
    """Regression: the stored lead was validated with the form's rules, so one
    accepted before "phone or Telegram" existed 500ed the whole CRM page."""
    ApplicationRepository().add(
        {"first_name": "Old", "last_name": "Lead", "email": "old@example.com"}
    )

    r = client.get("/applications")

    assert r.status_code == 200
    assert "old@example.com" in r.text


# --- spam bots ------------------------------------------------------------------


def _stored() -> int:
    return len(applications_store.list_applications())


@pytest.mark.parametrize(
    "changes",
    [
        {"website": "http://spam.example"},  # the honeypot
        {"form_token": ""},
        {"form_token": "forged"},
        {"telegram_handle": "coxkskvs tchbop"},
        {"instagram_handle": "{[url=https://n.example]x[/url]}"},
        {"goals": "Buy now https://spam.example"},
        {"phone": "call me maybe"},
        {"first_name": "A" * 61},
    ],
)
def test_a_bot_is_thanked_and_nothing_is_stored(changes, caplog):
    with caplog.at_level("WARNING", logger="ofmhelpers.web.routers.apply"):
        r = TestClient(app).post(
            "/apply", data=_form(**changes), follow_redirects=False
        )
    assert (r.status_code, r.headers["location"]) == (303, "/?applied=1")
    assert _stored() == 0
    assert "application dropped as spam" in caplog.text


def test_a_form_filled_in_seconds_is_a_bot(monkeypatch):
    monkeypatch.setenv("OFM_APPLY_MIN_FILL_S", "30")
    TestClient(app).post("/apply", data=_form(), follow_redirects=False)
    assert _stored() == 0


def test_the_landing_form_carries_the_token_honeypot_and_shapes():
    html = TestClient(app).get("/").text
    assert 'name="form_token" value="' in html
    assert 'name="website" tabindex="-1"' in html
    assert 'pattern="@?[A-Za-z][A-Za-z0-9_]{4,31}"' in html
