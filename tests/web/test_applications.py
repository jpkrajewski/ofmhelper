"""
Covers the public landing-page application form (routers/apply.py) and its
admin-only CRM view (routers/admin/applications.py): submitting without a
session, storing every field, and who may read the list.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.stores import applications as applications_store

FORM = {
    "first_name": "Ada",
    "last_name": "Lovelace",
    "email": "ada@example.com",
    "phone": "+48 123 456 789",
    "telegram_handle": "@ada",
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
    c.post("/login", data={"password": "test-va", "next": "/"})
    return c


def test_anonymous_submit_stores_every_field():
    anon = TestClient(app)

    r = anon.post("/apply", data=FORM, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?applied=1"

    stored = applications_store.list_applications()[0]
    for key, value in FORM.items():
        assert stored[key] == value


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
        anon.post("/apply", data={**FORM, "email": "not-an-address"}).status_code == 422
    )
    assert anon.post("/apply", data={**FORM, "first_name": "  "}).status_code == 422


def test_optional_fields_may_be_blank():
    anon = TestClient(app)

    r = anon.post(
        "/apply",
        data={
            "first_name": "Grace",
            "last_name": "Hopper",
            "email": "g@example.com",
            "phone": "+1 555 0100",
        },
        follow_redirects=False,
    )
    assert r.status_code == 303

    stored = applications_store.list_applications()[0]
    assert stored["telegram_handle"] == ""
    assert stored["instagram_handle"] == ""
    assert stored["monthly_revenue"] == ""
    assert stored["goals"] == ""


def test_phone_or_telegram_is_required_but_either_one_suffices():
    anon = TestClient(app)

    no_channel = {**FORM, "phone": "", "telegram_handle": ""}
    assert anon.post("/apply", data=no_channel).status_code == 422
    assert anon.post("/apply", data={**no_channel, "phone": "  "}).status_code == 422

    telegram_only = {**no_channel, "telegram_handle": "@grace"}
    assert (
        anon.post("/apply", data=telegram_only, follow_redirects=False).status_code
        == 303
    )
    stored = applications_store.list_applications()[0]
    assert stored["telegram_handle"] == "@grace"
    assert stored["phone"] == ""


def test_crm_page_lists_submissions_and_deletes(client):
    TestClient(app).post("/apply", data=FORM)

    html = client.get("/applications").text
    assert "Ada" in html
    assert "ada@example.com" in html
    assert "+48 123 456 789" in html
    assert "https://t.me/ada" in html
    assert "Scale to six figures." in html

    application_id = applications_store.list_applications()[0]["id"]
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
