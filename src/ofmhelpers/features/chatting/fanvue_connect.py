"""Connecting a Fanvue account: Fanvue has no API keys, so someone authorises the app
once in their browser. A connected account is read from the next sync on."""

import secrets

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.stores import chats
from ofmhelpers.errors import AppError
from ofmhelpers.features.chatting.webhooks import sync_done_key
from ofmhelpers.integrations.fanvue import FanvueClient, oauth
from ofmhelpers.log import get_logger
from ofmhelpers.schemas.chatting import ChatAccount, ChatPlatform

logger = get_logger(__name__)

_PLATFORM = ChatPlatform.FANVUE
_AGENCY_ROLES = frozenset({"agency", "agency_admin"})


def _state_key(state: str) -> str:
    return f"chatting:fanvue-connect:{state}"


def _redirect_uri() -> str:
    return f"{settings.web.base_url.rstrip('/')}/chatting/fanvue/callback"


def can_connect() -> bool:
    return bool(settings.fanvue.client_id and settings.fanvue.client_secret)


def connect_url() -> str:
    """Where to send the browser. The verifier waits in Redis, under a state only
    this browser's trip carries."""
    verifier, challenge = oauth.pkce_pair()
    state = secrets.token_urlsafe(32)
    set_text(_state_key(state), verifier, settings.fanvue.connect_ttl_s)
    return oauth.authorize_url(_redirect_uri(), state, challenge)


def finish(code: str, state: str) -> bool:
    """Fanvue sent the browser back. False when the trip is not ours or Fanvue
    refused; the reason is in the log, not on the page."""
    verifier = get_text(_state_key(state))
    if verifier is None:
        logger.warning("fanvue connect: unknown or expired state")
        return False
    delete_text(_state_key(state))
    try:
        tokens = oauth.exchange(code, _redirect_uri(), verifier)
        me = FanvueClient(lambda: tokens.access_token).me()
    except AppError:
        logger.warning("fanvue connect failed", exc_info=True)
        return False
    chats.save_account(
        _PLATFORM,
        ChatAccount(
            account_id=me.uuid,
            username=me.handle,
            is_agency=bool(_AGENCY_ROLES & set(me.roles)),
        ),
        tokens,
    )
    logger.info("fanvue connected: %s", me.handle)
    delete_text(sync_done_key(_PLATFORM))
    return True
