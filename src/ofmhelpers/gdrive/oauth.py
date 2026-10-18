"""Google's OAuth 2.0 web-server flow, with PKCE (google-auth-oauthlib's
`Flow` generates the code verifier; the caller keeps it in the session
between the redirect out and the callback)."""

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from pydantic import BaseModel

from ofmhelpers.config import settings

# drive.file: only files this app creates. Non-sensitive, so no Google
# security review -- and all an upload needs.
SCOPES = ["https://www.googleapis.com/auth/drive.file"]

_AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"  # noqa: S105 - a URL


class PendingAuth(BaseModel):
    """What the callback needs from the redirect out: kept in the session."""

    state: str
    code_verifier: str
    next_url: str


def _flow(state: str | None = None, code_verifier: str | None = None) -> Flow:
    s = settings.gdrive
    config = {
        "web": {
            "client_id": s.client_id,
            "client_secret": s.client_secret,
            "auth_uri": _AUTH_URI,
            "token_uri": TOKEN_URI,
            "redirect_uris": [s.redirect_uri],
        }
    }
    return Flow.from_client_config(
        config,
        scopes=SCOPES,
        redirect_uri=s.redirect_uri,
        state=state,
        code_verifier=code_verifier,
    )


def authorization_url(next_url: str) -> tuple[str, PendingAuth]:
    """Google's consent page URL. `prompt=consent` makes Google return a
    refresh token on every connect, not only the first one."""
    flow = _flow()
    url, state = flow.authorization_url(prompt="consent")
    pending = PendingAuth(
        state=state, code_verifier=flow.code_verifier, next_url=next_url
    )
    return url, pending


def exchange_code(code: str, pending: PendingAuth) -> Credentials:
    flow = _flow(state=pending.state, code_verifier=pending.code_verifier)
    flow.fetch_token(code=code)
    return flow.credentials
