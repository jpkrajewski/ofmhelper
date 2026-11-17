"""The models a platform's keys reach, as the last sync round found them, so
/chatting lists a creator before their first chat is read (one just linked to
the agency: owner 2026-11-16, "it should just sync when there is new creator in
agency"). Redis, like recent.py: a page list every round writes again; lost, a
model hides only until the next round, a minute later."""

from pydantic import BaseModel, ValidationError

from ofmhelpers.cache import get_text, set_text
from ofmhelpers.config import settings
from ofmhelpers.log import get_logger
lazy from ofmhelpers.schemas.chatting import ChatPlatform

logger = get_logger(__name__)


class _Reached(BaseModel):
    usernames: list[str]


def _key(platform: ChatPlatform) -> str:
    return f"chatting:reached:{platform.value}"


def remember_reached(platform: ChatPlatform, usernames: list[str]) -> None:
    value = _Reached(usernames=sorted(set(usernames))).model_dump_json()
    set_text(_key(platform), value, settings.chatting.reached_keep_s)


def reached_names(platform: ChatPlatform) -> list[str]:
    """Empty when no round ran lately or Redis is unreachable."""
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
