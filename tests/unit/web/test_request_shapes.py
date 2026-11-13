"""What the routers lean on instead of repeating it: a form taken whole
(`web/forms.py`), a refusal raised past its route (`web/exceptions.py`), and
what telemetry keeps of either."""

import os
from datetime import date

os.environ.setdefault("SESSION_SECRET", "test-secret")

import inspect
from collections.abc import Iterator
from typing import Annotated
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Form, HTTPException
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.telemetry import _request_attributes
from ofmhelpers.web.exceptions import REFUSALS, exception_handlers
from ofmhelpers.web.forms import Files, FormModel, PaidForm, Stripped, Upper
from ofmhelpers.web.gallery import GalleryQuery
from ofmhelpers.web.routers import ROUTERS
from ofmhelpers.web.routers.inventory import phones


class _Form(PaidForm):
    prompt: Stripped = "the default"
    resolution: Upper = "720P"
    name: str = Field("", max_length=5)
    links: list[str] = []
    image: Files


class _Required(FormModel):
    name: str


def _client() -> TestClient:
    app = FastAPI(exception_handlers=exception_handlers())

    @app.post("/form")
    def form(sent: Annotated[_Form, Form()]):
        return {
            **sent.model_dump(exclude={"image"}),
            "image": [f.filename for f in sent.image],
        }

    @app.post("/required")
    def required(sent: Annotated[_Required, Form()]):
        return sent.name

    @app.post("/refuse/{index}")
    def refuse(index: int):
        error = list(REFUSALS)[index]
        # Past its __init__: some refusals word themselves.
        raise error.__new__(error, "words for the person")

    return TestClient(app)


def test_a_blank_field_takes_its_default_like_a_form_parameter():
    body = _client().post("/form", data={"prompt": "", "provider": ""}).json()

    assert body["prompt"] == "the default"
    assert body["provider"] == settings.aigen.default_provider


def test_spaces_are_a_value_not_a_blank_field():
    body = _client().post("/form", data={"provider": "  "}).json()

    assert body["provider"] == "  "


def test_a_form_is_normalised_on_its_fields():
    body = (
        _client()
        .post("/form", data={"api_key": " k ", "prompt": " hi ", "resolution": "480p"})
        .json()
    )

    assert (body["api_key"], body["prompt"], body["resolution"]) == ("k", "hi", "480P")


def test_files_and_lists_arrive_inside_the_form():
    r = _client().post(
        "/form",
        data={"prompt": "hi", "links": ["a", "b"]},
        files=[("image", ("a.png", b"x")), ("image", ("b.png", b"y"))],
    )

    assert r.status_code == 200, r.text
    assert r.json()["links"] == ["a", "b"]
    assert r.json()["image"] == ["a.png", "b.png"]


def test_a_required_field_left_blank_is_missing():
    assert _client().post("/required", data={"name": ""}).status_code == 422


def test_a_field_over_its_limit_is_refused():
    assert _client().post("/form", data={"name": "toolong"}).status_code == 422


def test_a_refused_form_never_echoes_what_was_sent():
    r = _client().post("/required", data={"api_key": "sk-secret"})

    assert r.status_code == 422
    assert "sk-secret" not in r.text
    assert r.json()["detail"][0]["msg"] == "Field required"
    assert r.json()["detail"][0]["loc"] == ["body", "name"]


@pytest.mark.parametrize("index", range(len(REFUSALS)))
def test_a_refusal_answers_its_status_and_its_own_words(index):
    r = _client().post(f"/refuse/{index}")

    assert r.status_code == list(REFUSALS.values())[index]
    assert r.json() == {"detail": "words for the person"}


def test_only_the_phone_routes_that_use_a_secret_answer_the_vault_reason():
    """On the whole router, a broken vault key would turn the /phones page's
    plain "Something went wrong" into raw JSON and lose its crash log."""
    with_reason = {
        route.endpoint.__name__
        for route in phones.router.routes
        if isinstance(route, APIRoute)
        and any(d.call is phones._vault_reason for d in route.dependant.dependencies)
    }

    assert with_reason == {
        "export",
        "secret",
        "add",
        "edit",
        "add_login",
        "assign_account",
        "edit_login",
    }


def _logged(**values) -> dict:
    return _request_attributes(MagicMock(), {"values": values, "errors": []})["values"]


def test_a_form_is_logged_field_by_field_and_its_key_stays_hidden():
    logged = _logged(form=PaidForm(api_key="sk-secret", provider="kie"))

    assert logged == {"api_key": "[hidden]", "provider": "kie"}


def test_a_gallerys_query_is_logged_field_by_field():
    logged = _logged(query=GalleryQuery(offset=20, hidden=True))

    assert logged == {
        "offset": 20,
        "hidden": True,
        "date_from": None,
        "date_to": None,
    }


def test_a_gallerys_date_filter_is_logged():
    asked = GalleryQuery(date_from=date(2026, 11, 1), date_to=date(2026, 11, 12))

    assert _logged(query=asked) == {
        "offset": 0,
        "hidden": False,
        "date_from": date(2026, 11, 1),
        "date_to": date(2026, 11, 12),
    }


def test_every_route_says_what_it_returns():
    silent = [
        route.path
        for router in ROUTERS
        for route in router.routes
        if isinstance(route, APIRoute)
        and inspect.signature(route.endpoint).return_annotation
        is inspect.Signature.empty
    ]

    assert silent == []


def test_a_refusal_before_the_first_yield_would_answer_200():
    """Why /clone-links/previews returns a `StreamingResponse` and does not
    `yield`, against the FastAPI skill's advice: its refusals (no Drive, bad
    paste, unknown resolution) must stay 400s. If this ever fails, FastAPI
    changed and that route can follow the skill."""
    app = FastAPI()

    @app.post("/stream")
    def stream() -> Iterator[int]:
        raise HTTPException(status_code=400, detail="refused")
        yield 1

    r = TestClient(app, raise_server_exceptions=False).post("/stream")

    assert r.status_code == 200
    assert r.text == ""


def test_an_allowed_name_holding_a_whole_object_is_hidden():
    class _Preset(BaseModel):
        prompt: str = "her private prompt"

    assert _logged(preset=_Preset()) == {"preset": "[hidden]"}
    assert _logged(preset=7) == {"preset": 7}
    assert _logged(links=["a", "b"]) == {"links": ["a", "b"]}
