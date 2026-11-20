import pytest
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.config import settings
from ofmhelpers.web.middleware import AuthMiddleware, WriteRateLimitMiddleware


def test_both_middlewares_are_importable_from_the_package():
    assert AuthMiddleware.__module__ == "ofmhelpers.web.middleware.auth"
    assert WriteRateLimitMiddleware.__module__ == "ofmhelpers.web.middleware.ratelimit"


def test_request_passes_session_then_rate_limit_then_auth():
    from ofmhelpers.web.main import app

    added = [m.cls for m in app.user_middleware]
    assert added.index(SessionMiddleware) < added.index(WriteRateLimitMiddleware)
    assert added.index(WriteRateLimitMiddleware) < added.index(AuthMiddleware)


def test_auth_policy_lives_on_the_middleware():
    import importlib

    assert callable(AuthMiddleware.is_public)
    assert callable(AuthMiddleware.is_fetch)
    assert callable(AuthMiddleware.sign_in)
    assert callable(AuthMiddleware.actor)
    assert callable(AuthMiddleware.require)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ofmhelpers.web.auth")


def test_allowlist_comes_from_settings():
    s = settings.web
    assert "/login" in s.public_paths
    assert AuthMiddleware.is_public("/static/css/pages.css")
    assert not AuthMiddleware.is_public("/generate")
    assert AuthMiddleware.is_public("/auth/google/callback")
    assert AuthMiddleware.is_public("/password/some-token")


def test_api_key_prefills_are_not_in_the_auth_module():
    from ofmhelpers.web.api_keys import get_api_key

    assert get_api_key.__module__ == "ofmhelpers.web.api_keys"
