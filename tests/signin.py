# Copyright (C) 2026 jpkrajewski

import json
import time
from base64 import b64encode

from itsdangerous import TimestampSigner
lazy from fastapi.testclient import TestClient

from ofmhelpers.config import settings
from ofmhelpers.db.users import store as users
from ofmhelpers.db.users.schema import Role, User, UserForm
lazy from ofmhelpers.integrations.signin.passwords import hash_password

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
    user = make_user(email, role)
    users._repository().set_password_hash(user.id, hash_password(password))
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
    client: TestClient,
    email: str = USER_EMAIL,
    role: Role = Role.USER,
) -> User:
    user = make_user(email, role)
    client.cookies.delete(SESSION_COOKIE)
    client.cookies.set(SESSION_COOKIE, session_cookie(user))
    return user


def sign_in_admin(client: TestClient, email: str = ADMIN_EMAIL) -> User:
    return sign_in(client, email, Role.ADMIN)


def sign_in_agency(client: TestClient) -> User:
    user = users.get_user(users.agency_id())
    assert user is not None
    client.cookies.set(SESSION_COOKIE, session_cookie(user))
    return user
