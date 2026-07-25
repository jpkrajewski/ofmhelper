"""
Covers the two-role (admin/VA) login logic in web/auth.py: whichever of the
two shared passwords matches determines the role, which is what later picks
KIE_AI_API_KEY_ADMIN vs KIE_AI_API_KEY_VA for the kie.ai forms.
"""

from ofmhelpers.web.auth import check_password


def test_admin_password_returns_admin_role(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_ADMIN", "admin-secret")
    monkeypatch.setenv("APP_PASSWORD_VA", "va-secret")

    assert check_password("admin-secret") == "admin"


def test_va_password_returns_va_role(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_ADMIN", "admin-secret")
    monkeypatch.setenv("APP_PASSWORD_VA", "va-secret")

    assert check_password("va-secret") == "va"


def test_wrong_password_returns_none(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD_ADMIN", "admin-secret")
    monkeypatch.setenv("APP_PASSWORD_VA", "va-secret")

    assert check_password("something-else") is None
