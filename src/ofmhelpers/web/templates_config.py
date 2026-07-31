from functools import lru_cache
from pathlib import Path

from fastapi.templating import Jinja2Templates

from ofmhelpers.config import settings


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    """The one Jinja2Templates instance every router renders through.

    Built on first use rather than at import: the environment it configures
    reads settings, and an import-time instance is a module-level global that
    every test then has to work around.
    """
    templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

    # A global rather than a per-route context value: base.html needs it on
    # every page (it feeds static/js/session.js's idle-expiry timer), and
    # threading it through ~20 routers' TemplateResponse calls would be noise.
    #
    # A callable, not the value: `settings.session` constructs fresh on every
    # access by design (see config/__init__.py), so reading it here would both
    # cache it across test monkeypatches and require SESSION_SECRET to be set
    # merely to build the environment.
    templates.env.globals["session_max_age"] = lambda: (
        settings.session.session_max_age_s
    )
    return templates
