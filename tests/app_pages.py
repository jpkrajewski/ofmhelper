from ofmhelpers.schemas.nav import NavPlace, nav
from ofmhelpers.schemas.users import Role

VA_PAGES = [item.href for item in nav(Role.USER)]
REAL_VA_PAGES = [item.href for item in nav(Role.VA)]
ADMIN_ONLY_PAGES = [item.href for item in nav(Role.ADMIN) if item.href not in VA_PAGES]

SHARED_BAR_LINKS = sum(item.place == NavPlace.BAR for item in nav(Role.USER))
ADMIN_HEADER = [item.href for item in nav(Role.ADMIN)]
