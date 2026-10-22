"""The site's pages as navigation knows them: the header bar, its "More"
menu and the home page's tool cards all render from `NAV`, so a new page
is one `NavItem` here, never an edit to three templates."""

from enum import StrEnum

from pydantic import BaseModel


class NavPlace(StrEnum):
    BAR = "bar"  # always visible in the header
    MORE = "more"  # folded under "More"


class NavItem(BaseModel):
    href: str
    label: str
    icon: str  # a name in templates/_icons.html
    place: NavPlace
    admin_only: bool = False

    def is_current(self, path: str) -> bool:
        """Whether `path` is this page or one under it."""
        return path == self.href or path.startswith(f"{self.href}/")


NAV = (
    NavItem(href="/generate", label="Gen AI", icon="sparkles", place=NavPlace.BAR),
    NavItem(href="/clone", label="Clone", icon="film", place=NavPlace.BAR),
    NavItem(href="/reels", label="Viral", icon="flame", place=NavPlace.BAR),
    NavItem(
        href="/download-assets", label="Download", icon="download", place=NavPlace.BAR
    ),
    NavItem(href="/accounts", label="Accounts", icon="chart", place=NavPlace.BAR),
    NavItem(href="/phones", label="Phones", icon="smartphone", place=NavPlace.BAR),
    NavItem(href="/drive", label="Drive", icon="folder", place=NavPlace.BAR),
    NavItem(href="/uniqify", label="Uniqify", icon="shuffle", place=NavPlace.MORE),
    NavItem(href="/account", label="Account", icon="user", place=NavPlace.MORE),
    NavItem(href="/cookies", label="Cookies", icon="cookie", place=NavPlace.MORE),
    NavItem(
        href="/users", label="Users", icon="users", place=NavPlace.MORE, admin_only=True
    ),
    NavItem(
        href="/character-sheet",
        label="Character Sheet",
        icon="grid",
        place=NavPlace.MORE,
        admin_only=True,
    ),
    NavItem(
        href="/applications",
        label="Leads",
        icon="inbox",
        place=NavPlace.MORE,
        admin_only=True,
    ),
    NavItem(
        href="/file-manager",
        label="Files",
        icon="file",
        place=NavPlace.MORE,
        admin_only=True,
    ),
    NavItem(
        href="/styleguide",
        label="Styleguide",
        icon="grid",
        place=NavPlace.MORE,
        admin_only=True,
    ),
    NavItem(
        href="/action-log",
        label="Logs",
        icon="list",
        place=NavPlace.MORE,
        admin_only=True,
    ),
)


def nav(is_admin: bool) -> list[NavItem]:
    """Every page this person may open, in menu order."""
    return [item for item in NAV if is_admin or not item.admin_only]
