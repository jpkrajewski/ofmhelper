"""Every page of the header, in header order: the bar, then "More" (Uniqify,
Account, Cookies, then the admin pages). Read from the menu (`schemas/nav.py`)
and the access map behind it (`auth/access.py`): a new page = one `NavItem`
there, nothing here."""

from ofmhelpers.schemas.nav import NavPlace, nav
from ofmhelpers.schemas.users import Role

# The name is historic (the first non-admin role was called VA): these are the
# pages a plain user (`Role.USER`) sees. A real VA's are `REAL_VA_PAGES`.
VA_PAGES = [item.href for item in nav(Role.USER)]
# What role `va` may open of the menu.
REAL_VA_PAGES = [item.href for item in nav(Role.VA)]
ADMIN_ONLY_PAGES = [item.href for item in nav(Role.ADMIN) if item.href not in VA_PAGES]

# An admin's header: their one extra bar link comes right after the shared ones.
SHARED_BAR_LINKS = sum(item.place == NavPlace.BAR for item in nav(Role.USER))
ADMIN_HEADER = [item.href for item in nav(Role.ADMIN)]
