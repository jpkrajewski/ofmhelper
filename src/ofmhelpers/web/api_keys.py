lazy from starlette.requests import Request

from ofmhelpers.features.account.keys import Keys
from ofmhelpers.integrations.ai_providers.base import provider_class, provider_classes
from ofmhelpers.web.middleware import AuthMiddleware


def get_api_keys(request: Request) -> dict[str, str]:
    viewer = AuthMiddleware.viewer(request)
    if not viewer.types_key:
        return {cls.name: "" for cls in provider_classes().all()}
    keys = Keys.of(viewer.user.id)
    return {cls.name: keys.prefill(cls.name) for cls in provider_classes().all()}


def get_api_key(request: Request, provider: str) -> str:
    provider_class(provider)
    return get_api_keys(request).get(provider, "")
