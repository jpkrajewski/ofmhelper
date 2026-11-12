"""Pre-fill values for the provider API-key field the generation forms show.

Not auth: these decide what a form field *starts out containing*, not who may
reach it. A user's form gets a key they saved on /account -- their runs
bill their own provider account. An admin with none saved for a provider
gets the .env one (`Provider.default_key`). Empty: they paste one in. Which
of a provider's keys a run really goes out on is decided at the submit
(`features/account/keys.py` `Keys.for_run`: the slot of the model picked).
"""

lazy from starlette.requests import Request

from ofmhelpers.features.account.keys import Keys
from ofmhelpers.integrations.ai_providers.base import provider_class, provider_classes
from ofmhelpers.web.middleware import AuthMiddleware


def get_api_keys(request: Request) -> dict[str, str]:
    """Every provider's pre-fill, for a form whose provider select swaps
    the key field's value."""
    viewer = AuthMiddleware.viewer(request)
    if not viewer.types_key:
        # A VA runs on the owner's keys, an admin viewing as someone on
        # nobody's: neither browser is ever sent a key.
        return {cls.name: "" for cls in provider_classes().all()}
    keys = Keys.of(viewer.user.id)
    return {cls.name: keys.prefill(cls.name) for cls in provider_classes().all()}


def get_api_key(request: Request, provider: str) -> str:
    """Pre-fill value for `provider`'s key field."""
    provider_class(provider)  # an unknown provider raises, as before
    return get_api_keys(request).get(provider, "")
