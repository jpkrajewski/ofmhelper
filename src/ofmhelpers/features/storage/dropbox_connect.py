import secrets

from redis.exceptions import RedisError

from ofmhelpers.cache import get_redis
from ofmhelpers.config import settings
from ofmhelpers.db.storage_accounts import store as storage_accounts
from ofmhelpers.db.users.schema import StorageProvider
from ofmhelpers.features.storage import storage_choice
from ofmhelpers.integrations.pkce import pkce_pair
from ofmhelpers.integrations.storage.dropbox import oauth
from ofmhelpers.log import get_logger

logger = get_logger(__name__)


def _state_key(state: str) -> str:
    return f"storage:dropbox-connect:{state}"


def _redirect_uri() -> str:
    return f"{settings.web.base_url.rstrip('/')}/account/dropbox/callback"


def can_connect() -> bool:
    return bool(settings.dropbox.app_key and settings.dropbox.app_secret)


def connect_url(user_id: int) -> str | None:
    if not can_connect():
        return None
    pkce = pkce_pair()
    state = secrets.token_urlsafe(32)
    try:
        get_redis().set(
            _state_key(state),
            f"{user_id}:{pkce.verifier}",
            ex=settings.dropbox.connect_ttl_s,
        )
    except RedisError:
        logger.warning("dropbox connect: state not kept", exc_info=True)
        return None
    return oauth.authorize_url(_redirect_uri(), state, pkce.challenge)


def _take_state(state: str) -> str | None:
    try:
        raw = get_redis().getdel(_state_key(state))
    except RedisError:
        logger.warning("dropbox connect: state store unreachable", exc_info=True)
        return None
    return raw.decode() if isinstance(raw, bytes) else raw


def finish(user_id: int, code: str, state: str) -> bool:
    stored = _take_state(state)
    if stored is None:
        logger.warning("dropbox connect: unknown or expired state")
        return False
    starter, _, verifier = stored.partition(":")
    if starter != str(user_id):
        logger.warning(
            "dropbox connect: state started by user %s used by %s", starter, user_id
        )
        return False
    try:
        grant = oauth.exchange(code, _redirect_uri(), verifier)
        account = oauth.account(grant.refresh_token)
    except Exception:
        logger.exception("dropbox connect: the token exchange of %s failed", user_id)
        return False
    try:
        storage_accounts.save(
            user_id,
            StorageProvider.DROPBOX,
            grant.refresh_token,
            grant.account_id,
            account.email,
        )
    except Exception:
        logger.exception("dropbox connect: saving the connection of %s failed", user_id)
        return False
    try:
        storage_choice.use(user_id, StorageProvider.DROPBOX)
    except Exception:
        logger.exception(
            "dropbox connect: the connection of %s is stored, choosing it failed",
            user_id,
        )
        return False
    logger.info("dropbox connected for user %s", user_id)
    return True
