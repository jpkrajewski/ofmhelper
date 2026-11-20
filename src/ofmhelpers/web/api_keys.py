lazy from types import MappingProxyType

lazy from starlette.requests import Request

from ofmhelpers.features.account.keys import known_provider, prefilled
from ofmhelpers.web.middleware import AuthMiddleware


def get_api_keys(request: Request) -> MappingProxyType[str, str]:
    viewer = AuthMiddleware.viewer(request)
    return prefilled(viewer.user.id if viewer.types_key else None)


def get_api_key(request: Request, provider: str) -> str:
    known_provider(provider)
    return get_api_keys(request).get(provider) or ""
