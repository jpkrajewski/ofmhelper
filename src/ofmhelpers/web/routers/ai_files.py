"""Reference files fetched by an AI provider that has no upload endpoint
(APIYI): `GET /files/ai/<token>/<name>`, one file per random token
(`features/generate/hosted.py`). Public (`public_prefixes`): the
provider has no session; the token is what guards it, and an unknown or
expired one is a 404. HEAD too, for a provider that checks before it
fetches; ranges are FileResponse's own."""

from fastapi import APIRouter
from fastapi.responses import FileResponse

from ofmhelpers.features.generate import hosted as hosting

router = APIRouter(prefix=hosting.PREFIX, tags=["ai-files"])


@router.api_route("/{token}/{name}", methods=["GET", "HEAD"])
def hosted(token: str, name: str) -> FileResponse:  # noqa: ARG001 -- for the provider
    found = hosting.hosted(token)
    return FileResponse(
        found.path,
        media_type=found.media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
