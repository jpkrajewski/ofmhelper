lazy from types import MappingProxyType

lazy from starlette.requests import Request

from ofmhelpers.features.account.keys import prefilled
from ofmhelpers.web.middleware import AuthMiddleware


def get_api_keys(request: Request) -> MappingProxyType[str, str]:
    viewer = AuthMiddleware.viewer(request)
    return prefilled(viewer.user.id if viewer.types_key else None)
