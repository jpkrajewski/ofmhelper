from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ofmhelpers.auth.access import Area
from ofmhelpers.features.downloads.service import Redirect
from ofmhelpers.features.reels import page
from ofmhelpers.features.reels.page import ProfileMoved, WatchAdded
from ofmhelpers.web.dependencies import Access, AssetsRoot, OwnerId, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/reels", tags=["reels"], dependencies=[Access(Area.REELS)])

GroupQuery = Annotated[str | None, Query(max_length=64)]
RankQuery = Annotated[str | None, Query(max_length=16)]
RatioQuery = Annotated[int | None, Query(ge=1, le=1000)]

ReelCode = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{5,64}$")]
_THUMB_CACHE = {"Cache-Control": "private, max-age=2592000, immutable"}

GroupField = Annotated[str, Form()]
PickedReels = Annotated[str, Form()]


class GroupCreated(BaseModel):
    group: str


class ProfileRemoved(BaseModel):
    removed: str


@router.get("")
def form(
    request: Request,
    scope: Scope,
    group: GroupQuery = None,
    rank: RankQuery = None,
    ratio: RatioQuery = None,
) -> Response:
    view = page.page_view(scope, group, rank, ratio)
    return render(request, "reels.html", **dict(view))


@router.get("/feed")
def feed_page(
    request: Request,
    scope: Scope,
    group: GroupQuery = None,
    rank: RankQuery = None,
    ratio: RatioQuery = None,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Response:
    found = page.feed_page(scope, group, rank, ratio, offset)
    return render(request, "_reels_feed_page.html", **dict(found))


@router.get("/thumb/{code}")
def thumb(code: ReelCode, scope: Scope) -> FileResponse:
    return FileResponse(
        page.cover(code, scope), media_type="image/webp", headers=_THUMB_CACHE
    )


@router.get("/watchlist")
def watchlist(request: Request, scope: Scope, group: GroupQuery = None) -> Response:
    return render(
        request, "_reels_watch_rows.html", **dict(page.watch_rows(scope, group))
    )


@router.post("/groups", status_code=201)
def new_group(owner_id: OwnerId, name: Annotated[str, Form()] = "") -> GroupCreated:
    return GroupCreated(group=page.new_group(name, owner_id))


@router.post("/watch")
def add(
    owner_id: OwnerId,
    profiles: Annotated[str, Form()] = "",
    group: GroupField = "",
) -> WatchAdded:
    return page.add(profiles, group, owner_id)


@router.post("/group/{username}")
def move(username: str, owner_id: OwnerId, group: GroupField = "") -> ProfileMoved:
    return page.move(username, group, owner_id)


@router.post("/unwatch/{username}")
def remove(username: str, owner_id: OwnerId) -> ProfileRemoved:
    return ProfileRemoved(removed=page.remove(username, owner_id))


@router.post("/download")
def download(reels: PickedReels, scope: Scope) -> Response:
    picked = page.picked_file(reels, scope)
    return FileResponse(
        picked.path,
        media_type=picked.media_type,
        filename=picked.path.name,
        background=BackgroundTask(picked.forget),
    )


@router.post("/clone")
def clone(reels: PickedReels, scope: Scope, assets_root: AssetsRoot) -> Redirect:
    return page.clone(reels, scope, assets_root)
