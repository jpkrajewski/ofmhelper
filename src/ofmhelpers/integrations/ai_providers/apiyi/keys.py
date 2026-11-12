"""Which APIYI key a model runs on. One APIYI key reaches one token group's
models (`GET /v1/models` per key, dev 2026-11-06): the Wan&HappyHorse group
lists `wan*` and `happyhorse-*`, the SeeDance2 group `doubao-seedance-*`, the
default group the rest (images, Veo; no Wan). The admin keeps one key per
group in .env (`ApiyiSettings`); a user saves one key per group on /account
(`SLOTS`), the default group's under the provider's own name."""

from ofmhelpers.config import settings
from ofmhelpers.schemas.generation import KeySlot
from ofmhelpers.utils.registry import Registry

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


def _slot(group: str) -> str:
    """A group's /account key slot: `apiyi` for the default group,
    `apiyi-<group>` for the others."""
    return "apiyi" if group == DEFAULT else f"apiyi-{group}"


def slot_of(model_id: str) -> str:
    """The /account key slot `model_id` runs on."""
    return _slot(group_of(model_id))


def slot_listing(model_ids: list[str]) -> str | None:
    """The slot of the key that lists `model_ids` (`GET /v1/models`): its
    token group read from what it reaches -- the default group is the only
    one listing models outside Wan and Seedance. None for an empty list."""
    groups = {group_of(model_id) for model_id in model_ids}
    found = next((g for g in (DEFAULT, WAN, SEEDANCE) if g in groups), None)
    return None if found is None else _slot(found)


# A person's keys, the default group's first.
SLOTS = (
    KeySlot(id="apiyi", label="APIYI"),
    KeySlot(id=f"apiyi-{WAN}", label="APIYI Wan & HappyHorse"),
    KeySlot(id=f"apiyi-{SEEDANCE}", label="APIYI Seedance"),
)


def admin_keys() -> Registry[str]:
    """Group -> the admin's key for it, the ones set."""
    s = settings.apiyi
    keys = {
        WAN: s.api_key_admin,
        SEEDANCE: s.api_key_seedance,
        DEFAULT: s.api_key_default,
    }
    return Registry[str](entries={group: key for group, key in keys.items() if key})


def key_for(model_id: str, given: str) -> str:
    """The key `model_id` runs on: the admin's key of its group when `given` is
    one of the admin's keys (the form pre-filled it) and that group has one;
    a key of the user's own is never swapped."""
    keys = admin_keys()
    if given not in keys.all():
        return given
    return keys.get(group_of(model_id)) or given
