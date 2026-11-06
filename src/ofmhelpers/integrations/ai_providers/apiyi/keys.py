"""Which APIYI key a model runs on. One APIYI key reaches one token group's
models (`GET /v1/models` per key, dev 2026-11-06): the Wan&HappyHorse group
lists `wan*` and `happyhorse-*`, the SeeDance2 group `doubao-seedance-*`, the
default group the rest (images, Veo; no Wan). The admin keeps one key per
group in .env (`ApiyiSettings`); a user saves one key of their own and it is
used as is."""

from ofmhelpers.config import settings

WAN = "wan"
SEEDANCE = "seedance"
DEFAULT = "default"
# Model id prefix -> its group, most specific first; anything else: DEFAULT.
GROUP_OF_PREFIX: tuple[tuple[str, str], ...] = (
    ("wan", WAN),
    ("happyhorse", WAN),
    ("doubao-seedance", SEEDANCE),
    ("seedance", SEEDANCE),
)


def group_of(model_id: str) -> str:
    name = model_id.lower()
    return next(
        (g for prefix, g in GROUP_OF_PREFIX if name.startswith(prefix)), DEFAULT
    )


def admin_keys() -> dict[str, str]:
    """Group -> the admin's key for it, the ones set."""
    s = settings.apiyi
    keys = {
        WAN: s.api_key_admin,
        SEEDANCE: s.api_key_seedance,
        DEFAULT: s.api_key_default,
    }
    return {group: key for group, key in keys.items() if key}


def key_for(model_id: str, given: str) -> str:
    """The key `model_id` runs on: the admin's key of its group when `given` is
    one of the admin's keys (the form pre-filled it) and that group has one;
    a key of the user's own is never swapped."""
    keys = admin_keys()
    if given not in keys.values():
        return given
    return keys.get(group_of(model_id), given)
