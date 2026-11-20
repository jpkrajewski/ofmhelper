from pydantic import BaseModel, ValidationError

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
lazy from ofmhelpers.db.chats.schema import ChatPlatform

logger = get_logger(__name__)


class _Reached(BaseModel):
    usernames: list[str]


def _key(platform: ChatPlatform) -> str:
    return f"chatting:reached:{platform.value}"


def remember_reached(platform: ChatPlatform, usernames: list[str]) -> None:
    value = _Reached(usernames=sorted(set(usernames))).model_dump_json()
    set_text(_key(platform), value, settings.chatting.reached_keep_s)


def reached_names(platform: ChatPlatform) -> list[str]:
    raw = get_text(_key(platform))
    if raw is None:
        return []
    try:
        return _Reached.model_validate_json(raw).usernames
    except ValidationError:
        logger.warning(
            "chatting: reached models of %s unreadable", platform.value, exc_info=True
        )
        return []
