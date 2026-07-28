"""
Covers the admin-only Competition page (routers/admin/competition.py): the
per-model list of competing Instagram profiles, add (one URL per line) and
delete. Admin-gated like the roster it reads from.
"""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ["APP_PASSWORD_VA"] = "test-va"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import pytest
from fastapi.testclient import TestClient

from ofmhelpers.web.main import app
from ofmhelpers.web.stores import models as models_store


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


def test_va_gets_403_on_every_route(va_client):
    model = models_store.add_model("Comp VA", "")

    assert va_client.get("/competition").status_code == 403
    assert (
        va_client.post(
            f"/competition/{model['id']}/add", data={"urls": "https://instagram.com/x"}
        ).status_code
        == 403
    )
    assert va_client.post("/competition/xyz/delete").status_code == 403


def test_add_lists_and_delete(client):
    model = models_store.add_model("Comp Admin", "")

    r = client.post(
        f"/competition/{model['id']}/add",
        data={"urls": "https://instagram.com/rival1\n\nhttps://instagram.com/rival2\n"},
        follow_redirects=False,
    )
    assert r.status_code == 303

    html = client.get("/competition").text
    assert "Comp Admin" in html
    assert "https://instagram.com/rival1" in html
    assert "https://instagram.com/rival2" in html

    competitors = models_store.get_model(model["id"])["competitors"]
    assert [c["url"] for c in competitors] == [
        "https://instagram.com/rival1",
        "https://instagram.com/rival2",
    ]

    r = client.post(
        f"/competition/{competitors[0]['id']}/delete",
        follow_redirects=False,
    )
    assert r.status_code == 303
    left = models_store.get_model(model["id"])["competitors"]
    assert [c["url"] for c in left] == ["https://instagram.com/rival2"]


def test_add_to_unknown_model_is_404_and_blank_input_is_400(client):
    assert (
        client.post(
            "/competition/nope/add", data={"urls": "https://instagram.com/x"}
        ).status_code
        == 404
    )

    model = models_store.add_model("Comp Blank", "")
    assert (
        client.post(
            f"/competition/{model['id']}/add", data={"urls": "  \n"}
        ).status_code
        == 400
    )


def test_deleting_a_model_takes_its_competitors_with_it(client):
    model = models_store.add_model("Comp Cascade", "")
    models_store.add_competitors_bulk(model["id"], ["https://instagram.com/rival"])

    assert models_store.delete_model(model["id"]) is True
    assert models_store.get_model(model["id"]) is None
