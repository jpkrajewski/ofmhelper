"""Sign in as the app owner: the admin password (`APP_PASSWORD_ADMIN`) alone
opens the agency admin (the password account, stores/users.py). One more
provider on /login: its consent screen is this app's own password page
(`/auth/owner/consent`, routers/auth.py), which on the right password comes
back to `/auth/owner/callback` like Google does -- so the state cookie,
`next`, logging and refusals are the same code for every provider.

The code it hands back is a signed, short-lived token of the sign-in's
nonce (`ofm_signin` cookie): it opens nothing but that one sign-in.
Never linkable to a user (/account): it is no one's own account."""

from __future__ import annotations

import hmac

from itsdangerous import BadSignature, URLSafeTimedSerializer

from ofmhelpers.config import settings
from ofmhelpers.signin.base import Identity, SignInError, TokenReply

# How long the password page's code may take to reach the callback.
_CODE_TTL_S = 60
SUBJECT = "app-owner"


def _codes() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session.session_secret, salt="ofm-owner-sign-in"
    )


def password_ok(password: str) -> bool:
    """Whether `password` is the admin password (constant-time)."""
    admin = settings.web.app_password_admin
    return bool(admin) and hmac.compare_digest(
        password.encode(), (admin or "").encode()
    )


def owner_code(nonce: str) -> str:
    """What the password page sends to the callback once the password was
    right."""
    return _codes().dumps(nonce)


class Owner:
    name = "owner"
    label = "Big G Login"
    callback_method = "GET"
    # Its consent screen is ours, under fake externals too.
    local_consent = True
    linkable = False
    connects_drive = False

    def configured(self) -> bool:
        return bool(settings.web.app_password_admin)

    def consent_url(
        self,
        redirect_uri: str,  # noqa: ARG002 -- the callback is fixed
        state: str,
        nonce: str,  # noqa: ARG002 -- the page reads it from the state cookie
        *,
        connect: bool,  # noqa: ARG002 -- never linked
    ) -> str:
        return f"/auth/owner/consent?state={state}"

    def exchange(self, code: str, redirect_uri: str) -> TokenReply:  # noqa: ARG002
        try:
            nonce = _codes().loads(code, max_age=_CODE_TTL_S)
        except BadSignature as exc:
            msg = "This sign-in took too long: try again"
            raise SignInError(msg) from exc
        return TokenReply(id_token=nonce)

    def identity(self, reply: TokenReply, nonce: str) -> Identity:
        if not hmac.compare_digest(reply.id_token, nonce):
            msg = "This sign-in was not started here: try again"
            raise SignInError(msg)
        return Identity(provider=self.name, subject=SUBJECT, name=self.label)
