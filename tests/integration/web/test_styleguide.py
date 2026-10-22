"""/styleguide renders every element of components/ui.html (so a macro
that breaks shows up here first) and is for admins only."""

import os

os.environ.setdefault("SESSION_SECRET", "test-secret")

from fastapi.testclient import TestClient
from signin import sign_in, sign_in_admin

from ofmhelpers.web.main import app


def test_styleguide_shows_every_element_to_an_admin():
    client = TestClient(app)
    sign_in_admin(client)
    html = client.get("/styleguide").text
    for marker in (
        'class="btn btn-primary"',
        'class="btn btn-ghost"',
        'class="btn btn-danger"',
        'class="icon-btn"',
        '<button type="button" class="btn btn-primary" disabled>',
        'class="page-head-actions"',
        'class="card-title"',
        'class="notice notice-fail" role="alert"',
        'class="notice" role="status"',
        'class="badge badge-ok"',
        'class="stat-value"',
        'class="empty-state"',
        'class="btn btn-ghost back-link"',
        'class="link-card"',
        '<label for="sg-text">Text</label>',
    ):
        assert marker in html, marker


def test_styleguide_is_admin_only():
    client = TestClient(app)
    sign_in(client)
    assert client.get("/styleguide", follow_redirects=False).status_code == 403
