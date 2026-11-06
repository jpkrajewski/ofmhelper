"""Every page of the header, in header order: the bar, then "More" (Uniqify,
Account, Cookies, then the admin pages). A new page = one entry here."""

VA_PAGES = [
    "/generate",
    "/clone",
    "/clone-long",
    "/clone-links",
    "/download-assets",
    "/accounts",
    "/links",
    "/phones",
    "/drive",
    "/viral",
    "/pictures",
    "/reels",
    "/uniqify",
    "/edit",
    "/trials",
    "/repurpose",
    "/account",
    "/cookies",
]
ADMIN_ONLY_PAGES = [
    "/chatting",
    "/users",
    "/character-sheet",
    "/applications",
    "/file-manager",
    "/styleguide",
    "/action-log",
]

# An admin's header: their one extra bar link comes right after the shared ones.
SHARED_BAR_LINKS = 9
ADMIN_HEADER = [
    *VA_PAGES[:SHARED_BAR_LINKS],
    "/chatting",
    *VA_PAGES[SHARED_BAR_LINKS:],
    *ADMIN_ONLY_PAGES[1:],
]
