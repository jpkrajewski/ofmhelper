"""Every Stacked API key the agency's chats are read with, for
`StackedSource` and the daily report: the server's `.env` lines
(`STACKED_API_KEY_<NAME>`) and the keys admins saved on /account (slot
`stacked`, owner 2026-11-15: connect Stacked without editing the server).
The chats stay the agency's (/chatting is admins' and managers')."""

from ofmhelpers.config import settings
from ofmhelpers.db.stores import users

SLOT = "stacked"


class StackedKeys:
    def keys(self) -> list[str]:
        """`.env` first, then saved, each once."""
        found = [k.key for k in settings.stacked.api_keys]
        found += users.keys_in_slot(SLOT)
        return list(dict.fromkeys(found))
