# Copyright (C) 2026 jpkrajewski

import hmac

from itsdangerous import BadSignature, URLSafeTimedSerializer

from ofmhelpers.config import settings
from ofmhelpers.db.users.schema import Identity
from ofmhelpers.integrations.signin.base import SignInError, TokenReply

_CODE_TTL_S = 60
SUBJECT = "app-owner"


def _codes() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(
        settings.session.session_secret,
        salt="ofm-owner-sign-in",
    )


def password_ok(password: str) -> bool:
    admin = settings.web.app_password_admin
    return bool(admin) and hmac.compare_digest(
        password.encode(),
        (admin or "").encode(),
    )


def owner_code(nonce: str) -> str:
    return _codes().dumps(nonce)


class Owner:
    label = "Big G Login"
    callback_method = "GET"
    local_consent = True
    linkable = False
    connects_drive = False

    @property
    def name(self) -> str:
        return settings.signin.owner_provider

    def configured(self) -> bool:
        return bool(settings.web.app_password_admin)

    def consent_url(
        self,
        redirect_uri: str,  # noqa: ARG002
        state: str,
        nonce: str,  # noqa: ARG002
        *,
        connect: bool,  # noqa: ARG002
        challenge: str,  # noqa: ARG002
    ) -> str:
        return f"/auth/owner/consent?state={state}"

    def exchange(
        self,
        code: str,
        redirect_uri: str,  # noqa: ARG002
        verifier: str,  # noqa: ARG002
    ) -> TokenReply:
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
