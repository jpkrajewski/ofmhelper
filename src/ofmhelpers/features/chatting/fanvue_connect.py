# Copyright (C) 2026 jpkrajewski

import secrets

from redis.exceptions import RedisError

from ofmhelpers.cache import delete_text, get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.chats import store as chats
from ofmhelpers.db.chats.schema import ChatAccount, ChatPlatform
from ofmhelpers.features.chatting.webhooks import sync_done_key
from ofmhelpers.integrations.chat_source import ChatSourceError
from ofmhelpers.integrations.fanvue import FanvueClient, oauth
from ofmhelpers.integrations.pkce import pkce_pair
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


def connect_url(user_id: int, session_id: str) -> str | None:
    if not can_connect():
        return None
    pkce = pkce_pair()
    state = secrets.token_urlsafe(32)
    try:
        get_redis().set(
            _state_key(state),
            f"{user_id}:{session_id}:{pkce.verifier}",
            ex=settings.fanvue.connect_ttl_s,
        )
    except RedisError:
        logger.warning("fanvue connect: state not kept", exc_info=True)
        return None
    return oauth.authorize_url(_redirect_uri(), state, pkce.challenge)


def _take_state(state: str) -> str | None:
    try:
        raw = get_redis().getdel(_state_key(state))
    except RedisError:
        logger.warning("fanvue connect: state store unreachable", exc_info=True)
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


def finish(user_id: int, session_id: str, code: str, state: str) -> bool:
    stored = _take_state(state)
    if stored is None:
        logger.warning("fanvue connect: unknown or expired state")
        return False
    starter, _, rest = stored.partition(":")
    started_in, _, verifier = rest.partition(":")
    if starter != str(user_id) or not secrets.compare_digest(
        started_in.encode(),
        session_id.encode(),
    ):
        logger.warning(
            "fanvue connect: state started by user %s used by %s or in another session",
            starter,
            user_id,
        )
        return False
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
