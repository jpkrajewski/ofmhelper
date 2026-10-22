"""Static assets are linked with a content-hash version, so a deploy is never
served the previous file from a browser cache (nginx caches /static a week)."""

import os

os.environ["APP_PASSWORD_ADMIN"] = "test-admin"
os.environ.setdefault("SESSION_SECRET", "test-secret")

import re

from fastapi.testclient import TestClient
from signin import sign_in

from ofmhelpers.web import templates_config
from ofmhelpers.web.main import app


def test_every_static_link_is_versioned():
    client = TestClient(app)
    sign_in(client)
    html = client.get("/clone").text

    links = re.findall(r'(?:src|href)="(/static/[^"]+)"', html)
    assert "/static/js/clone.js" in " ".join(links)
    assert all(re.search(r"\?v=[0-9a-f]{10}$", link) for link in links), links
    assert client.get(links[0]).status_code == 200


def test_version_changes_with_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(templates_config, "STATIC_DIR", tmp_path)
    asset = tmp_path / "app.js"
    asset.write_text("one")
    first = templates_config.static_url("app.js")
    asset.write_text("two!")
    os.utime(asset, ns=(1, asset.stat().st_mtime_ns + 1_000_000))

    assert first.startswith("/static/app.js?v=")
    assert templates_config.static_url("app.js") != first
