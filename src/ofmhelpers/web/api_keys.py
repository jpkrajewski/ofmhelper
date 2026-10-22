"""Pre-fill values for the provider API-key field the generation forms show.

Not auth: these decide what a form field *starts out containing*, not who may
reach it. A user's form gets the keys they saved on /account -- their runs
bill their own provider account. An admin with none saved for a provider
gets the .env one (`Provider.default_key`). Empty: they paste one in.
"""

from starlette.requests import Request

from ofmhelpers.aigenproviders.base import provider_class, provider_classes
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.stores.users import api_keys


def get_api_keys(request: Request) -> dict[str, str]:
    """Every provider's pre-fill, for a form whose provider select swaps
    the key field's value."""
    user = AuthMiddleware.user(request)
    saved = api_keys(user.id)
    return {
        name: saved.get(name) or (cls.default_key() if user.is_admin else None) or ""
        for name, cls in provider_classes().items()
    }


def get_api_key(request: Request, provider: str) -> str:
    """Pre-fill value for `provider`'s key field."""
    provider_class(provider)  # an unknown provider raises, as before
    return get_api_keys(request).get(provider, "")
