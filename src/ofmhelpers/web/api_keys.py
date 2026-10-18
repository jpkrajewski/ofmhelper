"""Pre-fill values for the provider API-key field the generation forms show.

Not auth: these decide what a form field *starts out containing*, not who may
reach it. They only read the session because *which* key to pre-fill is a
per-role choice; each provider says which of its keys that is
(`Provider.default_key`).

Optional by design -- an unset var just means the field starts empty and the
user pastes a key in manually.
"""

from starlette.requests import Request

from ofmhelpers.aigenproviders.base import provider_class, provider_classes
from ofmhelpers.config import settings


def _is_admin(request: Request) -> bool:
    return request.session.get("role") == settings.web.role_admin


def get_api_key(request: Request, provider: str) -> str:
    """Pre-fill value for `provider`'s key field, by the logged-in role."""
    return provider_class(provider).default_key(_is_admin(request)) or ""


def get_api_keys(request: Request) -> dict[str, str]:
    """Every provider's pre-fill, for a form whose provider select swaps
    the key field's value."""
    admin = _is_admin(request)
    return {
        name: cls.default_key(admin) or "" for name, cls in provider_classes().items()
    }
