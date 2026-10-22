import re

import pytest
from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.web.main import app


@pytest.fixture
def client():
    with TestClient(app) as c:
        sign_in(c)
        yield c


def _selected(html: str) -> str:
    return re.search(r'<option value="([^"]+)"[^>]*\bselected\b', html).group(1)


@pytest.mark.parametrize("page", ["/generate", "/clone"])
def test_higgsfield_is_the_default_provider(client, page):
    assert _selected(client.get(page).text) == "higgsfield"


@pytest.mark.parametrize("page", ["/generate", "/clone"])
def test_default_provider_follows_the_setting(client, page, monkeypatch):
    monkeypatch.setenv("OFM_DEFAULT_PROVIDER", "kie")

    assert _selected(client.get(page).text) == "kie"
