import pytest
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.testclient import TestClient
from signin import sign_in_admin

from ofmhelpers.cache import get_redis
from ofmhelpers.features.chatting.recent import recent_models
from ofmhelpers.web.exceptions import exception_handlers
from ofmhelpers.web.main import app
from ofmhelpers.web.middleware.frame_guard import FrameGuardMiddleware

PAGE = {"sec-fetch-mode": "navigate"}


@pytest.fixture
def admin() -> tuple[TestClient, int]:
    client = TestClient(app)
    user = sign_in_admin(client, "csp-boss@example.com")
    get_redis().delete(f"chatting:recent:{user.id}")
    return client, user.id


@pytest.fixture
def known_model(monkeypatch):
    monkeypatch.setattr(
        "ofmhelpers.features.chatting.service.platform_models", lambda _p: ["x"]
    )


def test_opening_a_chat_page_writes_nothing_and_the_open_call_records_it(
    admin, known_model
):
    client, user_id = admin

    client.get("/chatting/fanvue/x", headers=PAGE)
    assert recent_models(user_id) == []

    answer = client.post("/chatting/fanvue/x/visits")

    assert answer.status_code == 204
    assert [(m.platform.value, m.username) for m in recent_models(user_id)] == [
        ("fanvue", "x")
    ]


def test_the_open_call_ignores_a_model_that_does_not_exist(admin, known_model):
    client, user_id = admin

    client.post("/chatting/fanvue/nobody/visits")

    assert recent_models(user_id) == []


def test_the_chat_page_sends_the_open_call(admin, known_model):
    client, _ = admin

    page = client.get("/chatting/fanvue/x", headers=PAGE)

    assert 'data-chat-visit="/chatting/fanvue/x/visits"' in page.text
    assert "js/chatting-model.js" in page.text


def test_the_error_page_cannot_be_framed():
    crashing = FastAPI(exception_handlers=exception_handlers().handlers)

    @crashing.get("/boom")
    def boom() -> None:
        raise RuntimeError

    answer = TestClient(crashing, raise_server_exceptions=False).get(
        "/boom", headers=PAGE
    )

    assert answer.status_code == 500
    assert answer.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in answer.headers["content-security-policy"]


def test_a_template_without_a_nonce_is_logged(caplog):
    from ofmhelpers.web import templates_config  # noqa: PLC0415

    request = Request({"type": "http", "headers": [], "state": {}})

    with caplog.at_level("ERROR"):
        assert templates_config.csp_nonce(request) == ""

    assert "without a CSP nonce" in caplog.text


@pytest.fixture
def polled_app(monkeypatch) -> TestClient:
    from ofmhelpers.web import templates_config  # noqa: PLC0415

    fragment = {"text": "same"}

    def render(request, _name, _context=None):
        nonce = templates_config.csp_nonce(request)
        return HTMLResponse(
            f'<script nonce="{nonce}"></script><p>{fragment["text"]}</p>'
        )

    monkeypatch.setattr(templates_config, "render", render)
    polled = FastAPI()
    polled.add_middleware(FrameGuardMiddleware)

    @polled.get("/poll")
    def poll(request: Request):
        return templates_config.render_polled(request, "x.html")

    polled.state.fragment = fragment
    return TestClient(polled)


def test_a_polled_fragment_keeps_its_etag_across_nonces_and_answers_304(polled_app):
    first = polled_app.get("/poll")
    second = polled_app.get("/poll", headers={"If-None-Match": first.headers["etag"]})

    assert first.status_code == 200
    assert second.status_code == 304
    assert second.headers["etag"] == first.headers["etag"]


def test_a_changed_fragment_gets_a_new_etag_and_a_full_answer(polled_app):
    first = polled_app.get("/poll")
    polled_app.app.state.fragment["text"] = "changed"

    second = polled_app.get("/poll", headers={"If-None-Match": first.headers["etag"]})

    assert second.status_code == 200
    assert second.headers["etag"] != first.headers["etag"]
    assert "changed" in second.text


def test_the_api_docs_may_run_their_scripts_and_every_other_page_may_not():
    docs = FastAPI()
    docs.add_middleware(FrameGuardMiddleware)

    @docs.get("/page")
    def page() -> str:
        return "x"

    client = TestClient(docs)
    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        policy = client.get(path).headers["content-security-policy"]
        assert "script-src" not in policy, path
        assert "frame-ancestors 'none'" in policy, path
    assert (
        "script-src 'self' 'nonce-"
        in client.get("/page").headers["content-security-policy"]
    )


def test_a_running_guard_gives_the_docs_paths_the_strict_policy_in_production(
    monkeypatch,
):
    docs = FastAPI()
    docs.add_middleware(FrameGuardMiddleware)
    client = TestClient(docs)
    relaxed = client.get("/docs").headers["content-security-policy"]
    assert "script-src" not in relaxed
    monkeypatch.setenv("OFM_ENV", "production")
    monkeypatch.setenv("SESSION_SECRET", "a" * 64)
    monkeypatch.setenv("SESSION_HTTPS_ONLY", "true")

    for path in ("/docs", "/redoc", "/docs/oauth2-redirect"):
        policy = client.get(path).headers["content-security-policy"]
        assert "script-src 'self' 'nonce-" in policy, path
