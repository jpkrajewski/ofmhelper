from ofmhelpers.config import settings
from ofmhelpers.db.stores import users

SLOT = "stacked"


class StackedKeys:
    def keys(self) -> list[str]:
        found = [k.key for k in settings.stacked.api_keys]
        found += users.keys_in_slot(SLOT)
        return list(dict.fromkeys(found))
