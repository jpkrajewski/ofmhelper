"""Signing a TestClient in as a user without a sign-in round trip.

The app's session is Starlette's signed cookie; this writes the same
cookie the app writes after a sign-in (`AuthMiddleware.sign_in`), for a
`users` row it makes when missing. The sign-in flows themselves (Google,
Apple, password, links) are covered in tests/integration/web/test_signin.py.
"""

import json
import time
from base64 import b64encode

from itsdangerous import TimestampSigner
lazy from fastapi.testclient import TestClient

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users
from ofmhelpers.schemas import Role, User, UserForm

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
    from ofmhelpers.db.stores.users import _repository
    from ofmhelpers.integrations.signin.passwords import hash_password

    user = make_user(email, role)
    _repository().set_password_hash(user.id, hash_password(password))
    return user


def session_cookie(user: User) -> str:
    data = {
        "user_id": user.id,
        "email": user.email,
        "role": str(user.role),
        "stamp": user.credential_stamp,
        "since": time.time(),
    }
    payload = b64encode(json.dumps(data).encode())
    signer = TimestampSigner(str(settings.session.session_secret))
    return signer.sign(payload).decode()


def sign_in(
    client: TestClient, email: str = USER_EMAIL, role: Role = Role.USER
) -> User:
    """Signs `client` in as that user (made when missing) and returns it."""
    user = make_user(email, role)
    # The one session cookie, as a browser keeps it: the app sets its own
    # copy again on every action (the session slides), and a second cookie
    # of the same name next to it would be sent first.
    client.cookies.delete(SESSION_COOKIE)
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
