"""Who may open what: THE access map. One place, read by everything that
asks -- the routers' guard (`web/dependencies.py` `Access` / `Can`), the menu
(`schemas/nav.py`), the templates (`can(...)`) and the tests' page lists.

An `Area` is a page with its routes (every router names one); an `Action`
is one thing inside an area that fewer roles may do. What each role opens is
written out below, one list per role: `VA`, `USER` (a VA's + its own),
`ADMIN` (a user's + its own), `MANAGER` (a user's pages but their own
business, + chatting: an employer's, looked at; of those, only what the
employer may open: `opens`). A page switched off whole is in `RETIRED`.
A new page: one `Area` member + its name in the list of the lowest role that
gets it; a new role: one more list and its line in `PAGES`.
`tests/unit/architecture/` fails on a route that names no area.
"""

from enum import StrEnum

from ofmhelpers.schemas.users import Role
lazy from ofmhelpers.schemas.users import Viewer


class Area(StrEnum):
    # Everyone signed in.
    SESSION = "session"  # signing out, leaving "view as"
    HOME = "home"
    JOBS = "jobs"
    REFS = "refs"
    # The tools a VA works in.
    GENERATE = "generate"
    CLONE = "clone"
    DOWNLOAD = "download"
    EDIT = "edit"
    HANDMADE = "handmade"  # a VA's handmade reels into their Drive folder
    # Everything else a user has.
    ACCOUNT = "account"
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
    THREADS = "threads"
    COOKIES = "cookies"
    VAS = "vas"  # what each VA did and spent, per day
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
    HIDE = "hide"  # a gallery card's Hide / Unhide and "Show hidden"
    # /handmade's Upload: a VA's own folder; the owner sees what went up.
    UPLOAD_HANDMADE = "upload-handmade"
    # /chatting's Connect Fanvue (GETs that save a login: look-only cannot
    # stop them).
    CONNECT_CHATS = "connect-chats"


EVERYONE = frozenset(Role)
MEMBERS = frozenset({Role.ADMIN, Role.USER})
VAS = frozenset({Role.VA})
ADMINS = frozenset({Role.ADMIN})

# What a VA opens: the tools they work in, nothing else.
VA = (
    Area.SESSION,
    Area.HOME,
    Area.JOBS,
    Area.REFS,
    Area.GENERATE,
    Area.CLONE,
    Area.DOWNLOAD,
    Area.EDIT,
    Area.HANDMADE,
)
# What a user opens: a VA's pages and their own business.
USER = (
    *VA,
    Area.ACCOUNT,
    Area.CLONE_LONG,
    Area.CLONE_LINKS,
    Area.LEGACY,
    Area.DOWNLOAD_OLD,
    Area.REELS,
    Area.VIRAL,
    Area.ACCOUNTS,
    Area.LINKS,
    Area.SELF_LINKS,
    Area.PHONES,
    Area.DRIVE,
    Area.PICTURES,
    Area.UNIQIFY,
    Area.REPURPOSE,
    Area.TRIALS,
    Area.THREADS,
    Area.COOKIES,
    Area.VAS,
)
# What an admin opens: a user's pages and the admin tools.
ADMIN = (
    *USER,
    Area.CHATTING,
    Area.CHARACTER_SHEET,
    Area.USERS,
    Area.APPLICATIONS,
    Area.STYLEGUIDE,
    Area.FILES,
    Area.ACTION_LOG,
)
# What a manager opens: a user's pages but their own account and cookies,
# and the chats; all of it their employer's, looked at (owner 2026-11-15:
# "can not generate anything ... see everything a normal user can see ...
# also chatting and /vas spending"; read only: `features/users/viewing.py`).
MANAGER = (
    *(area for area in USER if area not in {Area.ACCOUNT, Area.COOKIES}),
    Area.CHATTING,
)
PAGES: frozendict[Role, tuple[Area, ...]] = frozendict(
    {Role.VA: VA, Role.USER: USER, Role.ADMIN: ADMIN, Role.MANAGER: MANAGER}
)

# Switched off, not deleted (owner 2026-11-13: nobody uses them). A retired
# area is nobody's: its routes answer 403 to every role, the menu and the
# home page leave it out (`schemas/nav.py`), its sweeps and follow-up jobs
# do not run (`retired`: /reels' rescans, the unlock after a cookies upload)
# and its tests are parked (tests/conftest.py `collect_ignore_glob`,
# tests/e2e/test_app_flows.py `REELS_PARKED`). Code, tables and data stay.
# To bring one back: take it out of this set and un-park its tests.
# /pictures too (owner 2026-11-13); a picture run cut off by a deploy is
# still settled by recovery, so nothing is paid twice.
RETIRED = frozenset({Area.CLONE_LINKS, Area.PICTURES, Area.REELS, Area.REPURPOSE})


def _roles(area: Area) -> frozenset[Role]:
    if area in RETIRED:
        return frozenset()
    return frozenset(role for role, pages in PAGES.items() if area in pages)


# Area -> the roles that may open it, read off the three lists above; a
# retired one is nobody's.
ACCESS: frozendict[Area, frozenset[Role]] = frozendict(
    {area: _roles(area) for area in Area}
)

ACTIONS: frozendict[Action, frozenset[Role]] = frozendict(
    {
        Action.DRIVE_UPLOAD: MEMBERS,
        Action.SAVE_KEYS: MEMBERS,
        Action.CHANGE_PASSWORD: MEMBERS,
        Action.SERVER_STATUS: ADMINS,
        Action.HIDE: MEMBERS,
        Action.UPLOAD_HANDMADE: VAS,
        Action.CONNECT_CHATS: ADMINS,
    }
)


def allowed(role: Role, what: Area | Action) -> bool:
    """Whether someone of `role` may open the area or do the action."""
    roles = ACTIONS[what] if isinstance(what, Action) else ACCESS[what]
    return role in roles


def opens(viewer: Viewer, what: Area | Action) -> bool:
    """Whether a request's person may open the area or do the action: what
    their role's list allows; a manager, of that, only what their employer
    may open too (the chats are an admin's: a user's manager has none)."""
    if not allowed(viewer.access_role, what):
        return False
    return not viewer.managing or allowed(viewer.user.role, what)


def retired(area: Area) -> bool:
    """Whether the area is switched off (`RETIRED`)."""
    return area in RETIRED


def areas_of(role: Role) -> list[Area]:
    """Every area `role` may open, in the order above."""
    return [area for area in Area if role in ACCESS[area]]
