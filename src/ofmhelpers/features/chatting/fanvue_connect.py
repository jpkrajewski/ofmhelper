import secrets

from ofmhelpers.cache import delete_text, get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import ChatAccount, ChatPlatform
from ofmhelpers.features.chatting.webhooks import sync_done_key
from ofmhelpers.integrations.chat_source import ChatSourceError
from ofmhelpers.integrations.fanvue import FanvueClient, oauth
from ofmhelpers.log import get_logger

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
    pkce = oauth.pkce_pair()
    state = secrets.token_urlsafe(32)
    set_text(_state_key(state), pkce.verifier, settings.fanvue.connect_ttl_s)
    return oauth.authorize_url(_redirect_uri(), state, pkce.challenge)


def finish(code: str, state: str) -> bool:
    verifier = get_text(_state_key(state))
    if verifier is None:
        logger.warning("fanvue connect: unknown or expired state")
        return False
    delete_text(_state_key(state))
    try:
        tokens = oauth.exchange(code, _redirect_uri(), verifier)
        me = FanvueClient(lambda: tokens.access_token).me()
    except ChatSourceError:
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
