"""Going to a provider's consent screen, and past the fake one (e2e)."""

import secrets

from pydantic import BaseModel

from ofmhelpers.config import settings
from ofmhelpers.features.signin.state import SignInState, safe_next, seal, unseal
from ofmhelpers.integrations.signin import Provider
from ofmhelpers.integrations.signin.fake import FakeAccount, fake_code


class ConsentStart(BaseModel):
    """Where to send the browser, and the state cookie's value for the trip."""

    url: str
    cookie: str


def redirect_uri(provider: Provider, site: str) -> str:
    """The callback the provider returns to; `site` = this request's base URL,
    used when `APP_BASE_URL` is not set."""
    path = f"auth/{provider.name}/callback"
    base = settings.web.base_url
    return f"{base.rstrip('/')}/{path}" if base else site + path


def begin(
    provider: Provider, site: str, next_url: str, link_user_id: int | None
) -> ConsentStart:
    """`link_user_id`: connect the account to this signed-in user."""
    carried = SignInState(
        provider=provider.name,
        state=secrets.token_urlsafe(24),
        nonce=secrets.token_urlsafe(24),
        next=safe_next(next_url),
        link_user_id=link_user_id,
    )
    if settings.infra.fake_externals and not provider.local_consent:
        url = f"/auth/fake-consent?provider={provider.name}&state={carried.state}"
    else:
        url = provider.consent_url(
            redirect_uri(provider, site),
            carried.state,
            carried.nonce,
            connect=link_user_id is not None,
        )
    return ConsentStart(url=url, cookie=seal(carried))


def fake_callback(cookie: str, provider: str, state: str, email: str) -> str:
    """Where the fake consent screen returns to. Raises `SignInError`."""
    carried = unseal(cookie, provider, state)
    # Like Google: a Drive (refresh) token only when connecting, never on a
    # plain sign-in.
    code = fake_code(
        FakeAccount(
            email=email.strip().lower(),
            nonce=carried.nonce,
            refresh_token=carried.link_user_id is not None,
        )
    )
    return f"/auth/{provider}/callback?code={code}&state={state}"
