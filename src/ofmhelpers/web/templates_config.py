from pathlib import Path

from fastapi.templating import Jinja2Templates

from ofmhelpers.config import settings

templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# A global rather than a per-route context value: base.html needs it on every
# page (it feeds static/js/session.js's idle-expiry timer), and threading it
# through ~20 routers' TemplateResponse calls would be noise.
#
# A callable, not the value: `settings.session` constructs fresh on every
# access by design (see config/__init__.py), so reading it at import time
# would both cache it across test monkeypatches and require SESSION_SECRET
# to be set merely to import this module.
templates.env.globals["session_max_age"] = lambda: settings.session.session_max_age_s
