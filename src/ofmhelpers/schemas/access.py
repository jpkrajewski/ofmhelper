"""Who may open what: THE access map. One place, read by everything that
asks -- the routers' guard (`web/dependencies.py` `Access` / `Can`), the menu
(`schemas/nav.py`), the templates (`can(...)`) and the tests' page lists.

An `Area` is a page with its routes (every router names one); an `Action`
is one thing inside an area that fewer roles may do. A page switched off
whole is in `RETIRED`. A new page: one `Area`
member + its line in `ACCESS`; a new role: one more name in the sets below.
`tests/unit/architecture/` fails on a route that names no area.
"""

from enum import StrEnum

from ofmhelpers.schemas.users import Role


class Area(StrEnum):
    # Everyone signed in.
    SESSION = "session"  # signing out, leaving "view as"
    HOME = "home"
    ACCOUNT = "account"
    JOBS = "jobs"
    REFS = "refs"
    # The tools a VA works in.
    GENERATE = "generate"
    CLONE = "clone"
    DOWNLOAD = "download"
    EDIT = "edit"
    # Everything else a user has.
    CLONE_LONG = "clone-long"
    CLONE_LINKS = "clone-links"
    LEGACY = "legacy"
    DOWNLOAD_OLD = "download-old"
    REELS = "reels"
    VIRAL = "viral"
    ACCOUNTS = "accounts"
    LINKS = "links"
    SELF_LINKS = "self-links"
    PHONES = "phones"
    DRIVE = "drive"
    PICTURES = "pictures"
    UNIQIFY = "uniqify"
    REPURPOSE = "repurpose"
    TRIALS = "trials"
    COOKIES = "cookies"
    SPENDING = "spending"
    # Admins.
    CHATTING = "chatting"
    CHARACTER_SHEET = "character-sheet"
    USERS = "users"
    APPLICATIONS = "applications"
    STYLEGUIDE = "styleguide"
    FILES = "files"
    ACTION_LOG = "action-log"


class Action(StrEnum):
    DRIVE_UPLOAD = "drive-upload"  # /clone "Upload to Drive"
    SAVE_KEYS = "save-keys"  # /account API keys
    # /account; an admin sets a VA's.
    CHANGE_PASSWORD = "change-password"  # noqa: S105 -- an action's name
    SERVER_STATUS = "server-status"  # /home Server section


EVERYONE = frozenset(Role)
MEMBERS = frozenset({Role.ADMIN, Role.USER})
ADMINS = frozenset({Role.ADMIN})

_EVERYONE = (
    Area.SESSION,
    Area.HOME,
    Area.ACCOUNT,
    Area.JOBS,
    Area.REFS,
    Area.GENERATE,
    Area.CLONE,
    Area.DOWNLOAD,
    Area.EDIT,
)
_ADMINS = (
    Area.CHATTING,
    Area.CHARACTER_SHEET,
    Area.USERS,
    Area.APPLICATIONS,
    Area.STYLEGUIDE,
    Area.FILES,
    Area.ACTION_LOG,
)

# Switched off, not deleted (owner 2026-11-13: nobody uses them). A retired
# area is nobody's: its routes answer 403 to every role, the menu and the
# home page leave it out (`schemas/nav.py`), its sweeps and follow-up jobs
# do not run (`retired`: /reels' rescans, the unlock after a cookies upload)
# and its tests are parked (tests/conftest.py `collect_ignore_glob`,
# tests/e2e/test_app_flows.py `REELS_PARKED`). Code, tables and data stay.
# To bring one back: take it out of this set and un-park its tests.
RETIRED = frozenset({Area.CLONE_LINKS, Area.REELS, Area.REPURPOSE})


def _roles(area: Area) -> frozenset[Role]:
    if area in RETIRED:
        return frozenset()
    if area in _EVERYONE:
        return EVERYONE
    return ADMINS if area in _ADMINS else MEMBERS


# Area -> the roles that may open it. An area in neither list above is
# every member's (admin and user), not a VA's; a retired one is nobody's.
ACCESS: frozendict[Area, frozenset[Role]] = frozendict(
    {area: _roles(area) for area in Area}
)

ACTIONS: frozendict[Action, frozenset[Role]] = frozendict(
    {
        Action.DRIVE_UPLOAD: MEMBERS,
        Action.SAVE_KEYS: MEMBERS,
        Action.CHANGE_PASSWORD: MEMBERS,
        Action.SERVER_STATUS: ADMINS,
    }
)


def allowed(role: Role, what: Area | Action) -> bool:
    """Whether someone of `role` may open the area or do the action."""
    roles = ACTIONS[what] if isinstance(what, Action) else ACCESS[what]
    return role in roles


def retired(area: Area) -> bool:
    """Whether the area is switched off (`RETIRED`)."""
    return area in RETIRED


def areas_of(role: Role) -> list[Area]:
    """Every area `role` may open, in the order above."""
    return [area for area in Area if role in ACCESS[area]]
