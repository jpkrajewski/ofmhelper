"""The site's pages as navigation knows them: the header bar, its "More"
menu and the home page's tool cards all render from `NAV`, so a new page
is one `NavItem` here, never an edit to three templates. Who sees an item
is the access map's answer for its `area` (`schemas/access.py`): the menu
never lists a page its reader cannot open."""

from enum import StrEnum

from pydantic import BaseModel

from ofmhelpers.schemas.access import Area, allowed
from ofmhelpers.schemas.users import Role


class NavPlace(StrEnum):
    BAR = "bar"  # always visible in the header
    MORE = "more"  # folded under "More"


class NavItem(BaseModel):
    href: str
    label: str
    icon: str  # a name in templates/_icons.html
    place: NavPlace
    area: Area

    def is_current(self, path: str) -> bool:
        """Whether `path` is this page or one under it."""
        return path == self.href or path.startswith(f"{self.href}/")


NAV = (
    NavItem(
        href="/generate",
        label="Gen AI",
        icon="sparkles",
        place=NavPlace.BAR,
        area=Area.GENERATE,
    ),
    NavItem(
        href="/clone",
        label="Clone",
        icon="film",
        place=NavPlace.BAR,
        area=Area.CLONE,
    ),
    NavItem(
        href="/clone-long",
        label="Clone long",
        icon="film",
        place=NavPlace.BAR,
        area=Area.CLONE_LONG,
    ),
    NavItem(
        href="/clone-links",
        label="Clone links",
        icon="film",
        place=NavPlace.BAR,
        area=Area.CLONE_LINKS,
    ),
    NavItem(
        href="/download-assets",
        label="Download",
        icon="download",
        place=NavPlace.BAR,
        area=Area.DOWNLOAD,
    ),
    NavItem(
        href="/accounts",
        label="Accounts",
        icon="chart",
        place=NavPlace.BAR,
        area=Area.ACCOUNTS,
    ),
    NavItem(
        href="/links",
        label="Links",
        icon="link",
        place=NavPlace.BAR,
        area=Area.LINKS,
    ),
    NavItem(
        href="/phones",
        label="Phones",
        icon="smartphone",
        place=NavPlace.BAR,
        area=Area.PHONES,
    ),
    NavItem(
        href="/drive",
        label="Drive",
        icon="folder",
        place=NavPlace.BAR,
        area=Area.DRIVE,
    ),
    NavItem(
        href="/chatting",
        label="Chatting",
        icon="message",
        place=NavPlace.BAR,
        area=Area.CHATTING,
    ),
    NavItem(
        href="/viral",
        label="Viral",
        icon="flame",
        place=NavPlace.MORE,
        area=Area.VIRAL,
    ),
    NavItem(
        href="/pictures",
        label="Pictures",
        icon="image",
        place=NavPlace.MORE,
        area=Area.PICTURES,
    ),
    NavItem(
        href="/reels",
        label="Viral watch",
        icon="flame",
        place=NavPlace.MORE,
        area=Area.REELS,
    ),
    NavItem(
        href="/uniqify",
        label="Uniqify",
        icon="shuffle",
        place=NavPlace.MORE,
        area=Area.UNIQIFY,
    ),
    NavItem(
        href="/edit",
        label="Edit",
        icon="scissors",
        place=NavPlace.MORE,
        area=Area.EDIT,
    ),
    NavItem(
        href="/trials",
        label="Trials",
        icon="flame",
        place=NavPlace.MORE,
        area=Area.TRIALS,
    ),
    NavItem(
        href="/self-links",
        label="Self links",
        icon="chart",
        place=NavPlace.MORE,
        area=Area.SELF_LINKS,
    ),
    NavItem(
        href="/repurpose",
        label="Repurpose",
        icon="sparkles",
        place=NavPlace.MORE,
        area=Area.REPURPOSE,
    ),
    NavItem(
        href="/account",
        label="Account",
        icon="user",
        place=NavPlace.MORE,
        area=Area.ACCOUNT,
    ),
    NavItem(
        href="/cookies",
        label="Cookies",
        icon="cookie",
        place=NavPlace.MORE,
        area=Area.COOKIES,
    ),
    NavItem(
        href="/spending",
        label="Spending",
        icon="chart",
        place=NavPlace.MORE,
        area=Area.SPENDING,
    ),
    NavItem(
        href="/users",
        label="Users",
        icon="users",
        place=NavPlace.MORE,
        area=Area.USERS,
    ),
    NavItem(
        href="/character-sheet",
        label="Character Sheet",
        icon="grid",
        place=NavPlace.MORE,
        area=Area.CHARACTER_SHEET,
    ),
    NavItem(
        href="/applications",
        label="Leads",
        icon="inbox",
        place=NavPlace.MORE,
        area=Area.APPLICATIONS,
    ),
    NavItem(
        href="/file-manager",
        label="Files",
        icon="file",
        place=NavPlace.MORE,
        area=Area.FILES,
    ),
    NavItem(
        href="/styleguide",
        label="Styleguide",
        icon="grid",
        place=NavPlace.MORE,
        area=Area.STYLEGUIDE,
    ),
    NavItem(
        href="/action-log",
        label="Logs",
        icon="list",
        place=NavPlace.MORE,
        area=Area.ACTION_LOG,
    ),
)


def nav(role: Role | None) -> list[NavItem]:
    """Every page someone of `role` may open, in menu order. None (nobody
    signed in: a public page's header) lists what a user has."""
    return [item for item in NAV if allowed(role or Role.USER, item.area)]
