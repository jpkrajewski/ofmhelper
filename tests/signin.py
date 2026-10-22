"""Signing a TestClient in as a user without a sign-in round trip.

The app's session is Starlette's signed cookie; this writes the same
cookie the app writes after a sign-in (`AuthMiddleware.sign_in`), for a
`users` row it makes when missing. The sign-in flows themselves (Google,
Apple, password, links) are covered in tests/integration/web/test_signin.py.
"""

from __future__ import annotations

import json
from base64 import b64encode
from typing import TYPE_CHECKING

from itsdangerous import TimestampSigner

from ofmhelpers.config import settings
from ofmhelpers.web.schemas import Role, User, UserForm
from ofmhelpers.web.stores import users

if TYPE_CHECKING:
    from fastapi.testclient import TestClient

SESSION_COOKIE = "ofm_session"
USER_EMAIL = "user@example.com"
ADMIN_EMAIL = "boss@example.com"


def make_user(email: str = USER_EMAIL, role: Role = Role.USER) -> User:
    user = users.by_email(email) or users.add_user(UserForm(email=email, role=role))
    assert user is not None
    if user.role != role:
        user = users.set_role(user.id, role)
        assert user is not None
    return user


def make_user_with_password(
    email: str = USER_EMAIL,
    password: str = "a long test password",
    role: Role = Role.USER,
) -> User:
    """A user who signs in with email + password (the real flow)."""
    from ofmhelpers.signin.passwords import hash_password
    from ofmhelpers.web.stores.users import _repository

    user = make_user(email, role)
    _repository().set_password_hash(user.id, hash_password(password))
    return user


def session_cookie(user: User) -> str:
    data = {"user_id": user.id, "email": user.email, "role": str(user.role)}
    payload = b64encode(json.dumps(data).encode())
    signer = TimestampSigner(str(settings.session.session_secret))
    return signer.sign(payload).decode()


def sign_in(
    client: TestClient, email: str = USER_EMAIL, role: Role = Role.USER
) -> User:
    """Signs `client` in as that user (made when missing) and returns it."""
    user = make_user(email, role)
    client.cookies.set(SESSION_COOKIE, session_cookie(user))
    return user


def sign_in_admin(client: TestClient, email: str = ADMIN_EMAIL) -> User:
    return sign_in(client, email, Role.ADMIN)


def sign_in_agency(client: TestClient) -> User:
    """Signs in as the password account: the admin who owns what the stores
    make without an owner (`users.agency_id`) -- the phones, accounts and
    watched profiles a test seeds through them."""
    user = users.get_user(users.agency_id())
    assert user is not None
    client.cookies.set(SESSION_COOKIE, session_cookie(user))
    return user
