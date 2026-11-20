from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse, Response
from pydantic import StringConstraints

from ofmhelpers.config import settings
from ofmhelpers.features.assets import refs
from ofmhelpers.features.assets.ref_schema import MissingRefs, RefEntry, StoredRefs
from ofmhelpers.features.assets.store import strip_asset_hash_prefix
from ofmhelpers.web.access import Area
from ofmhelpers.web.dependencies import Access, Actor, AssetsRoot, OwnerId
from ofmhelpers.web.routers.task_helpers import media_response

router = APIRouter(prefix="/refs", tags=["refs"], dependencies=[Access(Area.REFS)])

MAX_CHECKED = 50


def _limit(limit: Annotated[int | None, Query(ge=1)] = None) -> int | None:
    if limit is not None and limit > settings.web.max_ref_limit:
        raise HTTPException(
            status_code=422, detail=f"limit: at most {settings.web.max_ref_limit}"
        )
    return limit


RefPath = Annotated[str, Query()]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


@router.get("")
def list_refs(
    assets_root: AssetsRoot,
    owner_id: OwnerId,
    kind: Annotated[str | None, Query()] = None,
    limit: Annotated[int | None, Depends(_limit)] = None,
) -> list[RefEntry]:
    return refs.listing(assets_root, owner_id, kind, limit)


@router.get("/missing")
def missing_refs(
    assets_root: AssetsRoot,
    actor: Actor,
    path: Annotated[list[str], Query(max_length=MAX_CHECKED)],
) -> MissingRefs:
    return refs.missing(assets_root, path, actor)


@router.get("/stored")
def stored_refs(
    assets_root: AssetsRoot,
    digest: Annotated[list[Sha256], Query(max_length=MAX_CHECKED)],
) -> StoredRefs:
    return refs.stored(assets_root, digest)


@router.get("/file")
def get_ref_file(assets_root: AssetsRoot, path: RefPath) -> Response:
    file_path = refs.visible(assets_root, path)
    return media_response(file_path, strip_asset_hash_prefix(file_path.name))


@router.get("/thumb")
def get_ref_thumb(
    assets_root: AssetsRoot,
    path: RefPath,
    size: Annotated[int, Query(ge=16, le=1024)] = 200,
) -> Response:
    return FileResponse(
        refs.thumb(assets_root, path, size),
        media_type="image/webp",
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )
