"""
The VA task loop: `todo.py` (admin-managed task list; VAs see it and attach a
finished asset) and `approve.py` (the public, no-login magic-link a reviewer
taps from Discord to approve that asset and kick off the Drive upload).

These two are one flow split across an authenticated and a public surface --
approve.py is deliberately outside AuthMiddleware, so read web/auth.py's
settings.web.public_prefixes before adding anything to it.
"""
