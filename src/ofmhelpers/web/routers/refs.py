"""/refs: the reuse picker's files -- its list, the files themselves and
their small thumbnails, only ever from the session's own part of the asset
store (`features.assets.refs`)."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response

from ofmhelpers.config import settings
from ofmhelpers.features.assets import refs
from ofmhelpers.features.assets.store import strip_asset_hash_prefix
from ofmhelpers.schemas import MissingRefs, RefEntry
from ofmhelpers.web.dependencies import AssetsRoot, OwnerId
from ofmhelpers.web.routers.task_helpers import media_response

router = APIRouter(prefix="/refs", tags=["refs"])

# Restored files one Recreate checks at once (a run holds a handful).
MAX_CHECKED = 50


def _limit(limit: Annotated[int | None, Query(ge=1)] = None) -> int | None:
    """`?limit=`, at most `max_ref_limit`: a setting, read at each request
    (a `le=` on the query would keep what it said at import)."""
    if limit is not None and limit > settings.web.max_ref_limit:
        raise HTTPException(
            status_code=422, detail=f"limit: at most {settings.web.max_ref_limit}"
        )
    return limit


RefPath = Annotated[str, Query()]


@router.get("")
def list_refs(
    assets_root: AssetsRoot,
    owner_id: OwnerId,
    kind: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Depends(_limit)] = None,
) -> list[RefEntry]:
    """The reuse picker's list: the files last picked, then the newest
    uploads; an explicit `?limit=` is "show older", newest-uploaded first."""
    return refs.listing(assets_root, owner_id, kind, limit)


@router.get("/missing")
def missing_refs(
    assets_root: AssetsRoot, path: Annotated[list[str], Query(max_length=MAX_CHECKED)]
) -> MissingRefs:
    """Of `path`s (a past run's files, restored by Recreate), the ones this
    session can no longer use. One request, so a restore checks every file
    without a 404 per gone one."""
    return refs.missing(assets_root, path)


@router.get("/file")
def get_ref_file(assets_root: AssetsRoot, path: RefPath) -> Response:
    file_path = refs.visible(assets_root, path)
    # Display name (hash prefix stripped) decides the type, and non-media is
    # forced to a download -- see media_response.
    return media_response(file_path, strip_asset_hash_prefix(file_path.name))


@router.get("/thumb")
def get_ref_thumb(
    assets_root: AssetsRoot,
    path: RefPath,
    size: Annotated[int, Query(ge=16, le=1024)] = 200,
) -> Response:
    """Small cached preview for the reuse-picker/inputs grids -- never the
    original."""
    return FileResponse(
        refs.thumb(assets_root, path, size),
        media_type="image/webp",
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )
