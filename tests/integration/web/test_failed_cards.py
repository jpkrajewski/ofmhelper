import re

from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.db.stores import users
from ofmhelpers.db.stores.jobs import create_job, fail_job
from ofmhelpers.schemas import Role
from ofmhelpers.web.main import app

ANN = "ann@example.com"
KEY_LINK = 'class="result-failed-fix" href="/account"'


def _failed_clone(email: str, error: str) -> str:
    job_id = create_job("clone", {"prompt": "p"}, actor=str(users.by_email(email).id))
    fail_job(job_id, error)
    return job_id


def _heads(html: str) -> list[str]:
    return re.findall(r'result-failed-head">([^<]+)<', html)


def _saids(html: str) -> list[str]:
    return re.findall(r'result-failed-said">([^<]+)<', html)


def test_who_and_why_come_first_and_a_key_failure_links_to_the_keys():
    ann = TestClient(app)
    sign_in(ann, ANN)
    _failed_clone(ANN, "Stopped")
    _failed_clone(ANN, "kie.ai: wrong API key")
    _failed_clone(ANN, "APIYI: insufficient balance (HTTP 403)")

    page = ann.get("/clone").text

    assert _heads(page) == ["APIYI: No credits", "kie.ai: Wrong API key", "Stopped"]
    assert _saids(page) == ["insufficient balance (HTTP 403)"]
    template = re.search(r'<template id="card-failed-keys">.*?</template>', page)
    assert template is not None
    assert KEY_LINK in template.group()
    assert page.replace(template.group(), "").count(KEY_LINK) == 2


def test_a_va_gets_the_reason_but_no_link_to_keys_it_cannot_open():
    owner = TestClient(app)
    sign_in(owner, ANN)
    va = TestClient(app)
    va_user = sign_in(va, "va@example.com", Role.VA)
    users.set_owner(va_user.id, users.by_email(ANN).id)
    _failed_clone("va@example.com", "kie.ai: wrong API key")

    vas_page = va.get("/clone").text

    assert _heads(vas_page) == ["kie.ai: Wrong API key"]
    assert KEY_LINK not in vas_page
    assert 'id="card-failed-keys"' not in vas_page
