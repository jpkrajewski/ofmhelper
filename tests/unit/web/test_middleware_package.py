"""Both middlewares are cross-cutting, so they live in web/middleware/ rather
than beside the feature whose policy they read. Their *order* is the part that
is easy to break silently: auth must run inside the rate limiter, or a flood of
unauthenticated writes does password work before it is dropped."""

import pytest
from starlette.middleware.sessions import SessionMiddleware

from ofmhelpers.config import settings
from ofmhelpers.web.middleware import AuthMiddleware, WriteRateLimitMiddleware


def test_both_middlewares_are_importable_from_the_package():
    assert AuthMiddleware.__module__ == "ofmhelpers.web.middleware.auth"
    assert WriteRateLimitMiddleware.__module__ == "ofmhelpers.web.middleware.ratelimit"


def test_request_passes_session_then_rate_limit_then_auth():
    from ofmhelpers.web.main import app

    # user_middleware is in *added* order; Starlette applies it outside-in, so
    # the last entry is the innermost / last to see a request.
    added = [m.cls for m in app.user_middleware]
    assert added.index(SessionMiddleware) < added.index(WriteRateLimitMiddleware)
    assert added.index(WriteRateLimitMiddleware) < added.index(AuthMiddleware)


def test_auth_policy_lives_on_the_middleware():
    """web/auth.py is gone: the allowlist check, the session's user and the
    admin dependency are all reachable off AuthMiddleware, so there is one
    place to look for anything auth-shaped (who may sign in is
    stores/users.py)."""
    import importlib

    assert callable(AuthMiddleware.is_public)
    assert callable(AuthMiddleware.is_fetch)
    assert callable(AuthMiddleware.sign_in)
    assert callable(AuthMiddleware.actor)
    assert callable(AuthMiddleware.require_admin)

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ofmhelpers.web.auth")


def test_allowlist_comes_from_settings():
    """A deployment knob, not literals in the middleware; the sign-in round
    trips and set-password links are public."""
    s = settings.web
    assert "/login" in s.public_paths
    assert AuthMiddleware.is_public("/static/css/app.css")
    assert not AuthMiddleware.is_public("/generate")
    assert AuthMiddleware.is_public("/auth/google/callback")
    assert AuthMiddleware.is_public("/password/some-token")


def test_api_key_prefills_are_not_in_the_auth_module():
    """They decide what a form field starts out containing, not who may reach
    it -- so they live in web/api_keys.py."""
    from ofmhelpers.web.api_keys import get_api_key

    assert get_api_key.__module__ == "ofmhelpers.web.api_keys"
