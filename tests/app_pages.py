# Copyright (C) 2026 jpkrajewski

from ofmhelpers.db.users.schema import Role
from ofmhelpers.web.nav import NavPlace, nav

VA_PAGES = [item.href for item in nav(Role.USER)]
REAL_VA_PAGES = [item.href for item in nav(Role.VA)]
ADMIN_ONLY_PAGES = [item.href for item in nav(Role.ADMIN) if item.href not in VA_PAGES]

SHARED_BAR_LINKS = sum(item.place == NavPlace.BAR for item in nav(Role.USER))
ADMIN_HEADER = [item.href for item in nav(Role.ADMIN)]
