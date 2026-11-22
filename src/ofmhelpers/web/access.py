from enum import StrEnum

from ofmhelpers.config.retired import retired as retired_config
from ofmhelpers.db.users.schema import Role
lazy from ofmhelpers.db.users.schema import Viewer


class Area(StrEnum):
    SESSION = "session"
    HOME = "home"
    JOBS = "jobs"
    REFS = "refs"
    GENERATE = "generate"
    CLONE = "clone"
    DOWNLOAD = "download"
    EDIT = "edit"
    HANDMADE = "handmade"
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
    STORAGE = "storage"
    PICTURES = "pictures"
    UNIQIFY = "uniqify"
    REPURPOSE = "repurpose"
    TRIALS = "trials"
    THREADS = "threads"
    DISTRIBUTION = "distribution"
    COOKIES = "cookies"
    VAS = "vas"
    CHATTING = "chatting"
    CHARACTER_SHEET = "character-sheet"
    USERS = "users"
    APPLICATIONS = "applications"
    STYLEGUIDE = "styleguide"
    FILES = "files"
    ACTION_LOG = "action-log"


class Action(StrEnum):
    DRIVE_UPLOAD = "drive-upload"
    SAVE_KEYS = "save-keys"
    CHANGE_PASSWORD = "change-password"  # noqa: S105
    SERVER_STATUS = "server-status"
    HIDE = "hide"
    UPLOAD_HANDMADE = "upload-handmade"
    CONNECT_CHATS = "connect-chats"


EVERYONE = frozenset(Role)
MEMBERS = frozenset({Role.ADMIN, Role.USER})
VAS = frozenset({Role.VA})
ADMINS = frozenset({Role.ADMIN})

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
    Area.STORAGE,
    Area.PICTURES,
    Area.UNIQIFY,
    Area.REPURPOSE,
    Area.TRIALS,
    Area.THREADS,
    Area.DISTRIBUTION,
    Area.COOKIES,
    Area.VAS,
)
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
MANAGER = (
    *(area for area in USER if area not in {Area.ACCOUNT, Area.COOKIES}),
    Area.CHATTING,
)
PAGES: frozendict[Role, tuple[Area, ...]] = frozendict(
    {Role.VA: VA, Role.USER: USER, Role.ADMIN: ADMIN, Role.MANAGER: MANAGER}
)

RETIRED = frozenset(Area(name) for name in retired_config().areas)


def _roles(area: Area) -> frozenset[Role]:
    if area in RETIRED:
        return frozenset()
    return frozenset(role for role, pages in PAGES.items() if area in pages)


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
    roles = ACTIONS[what] if isinstance(what, Action) else ACCESS[what]
    return role in roles


def opens(viewer: Viewer, what: Area | Action) -> bool:
    if not allowed(viewer.access_role, what):
        return False
    if not viewer.managing:
        return True
    employer = Role.USER if viewer.user.role == Role.MANAGER else viewer.user.role
    return allowed(employer, what)


def retired(area: Area) -> bool:
    return area in RETIRED


def areas_of(role: Role) -> list[Area]:
    return [area for area in Area if role in ACCESS[area]]
