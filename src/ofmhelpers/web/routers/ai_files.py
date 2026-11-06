"""Reference files fetched by an AI provider that has no upload endpoint
(APIYI): `GET /files/ai/<token>/<name>`, one file per random token
(`integrations/ai_providers/hosting.py`). Public (`public_prefixes`): the
provider has no session; the token is what guards it, and an unknown or
expired one is a 404. HEAD too, for a provider that checks before it
fetches; ranges are FileResponse's own."""

import mimetypes

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import FileResponse

from ofmhelpers.integrations.ai_providers.hosting import HOSTED_PATH, hosted_file

router = APIRouter(prefix=HOSTED_PATH, tags=["ai-files"])


@router.api_route("/{token}/{name}", methods=["GET", "HEAD"])
def hosted(token: str, name: str) -> FileResponse:  # noqa: ARG001 -- for the provider
    path = hosted_file(token)
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(
        path,
        media_type=media_type,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
