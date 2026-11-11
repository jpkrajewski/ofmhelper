"""
/reels: a watch list of Instagram profiles, each in an optional group
("asian", "gay", ...), and one feed of all their recent reels past
`min_views`, at most `max_duration_s` long, most viewed first -- filtered by
group, paged `feed_page_size` at a time (static/js/gallery-scroll.js loads
the rest). "Clone selected" downloads the picked reels into the asset store
and sends the user to /clone with them already in the videos picker;
"Download selected" hands them straight back (one mp4, or a zip of several).

Adding profiles queues their first scan on the worker (with any other
profile due for its 12 h rescan, features/reels/watch.py), so no request waits on
Instagram; the page polls `GET /reels/watchlist` until none is pending.

Reels play on the page straight from Instagram's CDN (it allows cross-origin
embedding); their covers come shrunk and kept from `GET /reels/thumb/{code}`
(features/reels/thumbs.py). Nothing is downloaded until Clone or Download
selected.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi import Path as PathParam
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from starlette.background import BackgroundTask

from ofmhelpers.features.downloads.service import Redirect
from ofmhelpers.features.reels import page
from ofmhelpers.features.reels.page import ProfileMoved, WatchAdded
from ofmhelpers.web.dependencies import AssetsRoot, OwnerId, Scope
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/reels", tags=["reels"])

GroupQuery = Annotated[str | None, Query(max_length=64)]
# A rank (VIEW_TIERS name): the feed narrowed to exactly that rank.
RankQuery = Annotated[str | None, Query(max_length=16)]
# A follower multiple: only reels with at least that many times their
# account's followers in views.
RatioQuery = Annotated[int | None, Query(ge=1, le=1000)]

# A shortcode: Instagram's base64 alphabet.
ReelCode = Annotated[str, PathParam(pattern=r"^[A-Za-z0-9_-]{5,64}$")]
# A kept thumbnail never changes.
_THUMB_CACHE = {"Cache-Control": "private, max-age=2592000, immutable"}

# A group as a form sends it; "" = no group.
GroupField = Annotated[str, Form()]
# The page's picks, as JSON.
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
    """The next page of cards, as the fragment gallery-scroll.js appends."""
    found = page.feed_page(scope, group, rank, ratio, offset)
    return render(request, "_reels_feed_page.html", **dict(found))


@router.get("/thumb/{code}")
def thumb(code: ReelCode, scope: Scope) -> FileResponse:
    """A feed card's cover, small and kept (features/reels/thumbs.py): of a
    profile the person watches."""
    return FileResponse(
        page.cover(code, scope), media_type="image/webp", headers=_THUMB_CACHE
    )


@router.get("/watchlist")
def watchlist(request: Request, scope: Scope, group: GroupQuery = None) -> Response:
    """The watch list rows, as the page polls them while scans run."""
    return render(
        request, "_reels_watch_rows.html", **dict(page.watch_rows(scope, group))
    )


@router.post("/groups", status_code=201)
def new_group(owner_id: OwnerId, name: Annotated[str, Form()] = "") -> GroupCreated:
    """ "+ New group": a group every dropdown offers before any profile is in
    it. Answers the stored name (trimmed, lower case)."""
    return GroupCreated(group=page.new_group(name, owner_id))


@router.post("/watch")
def add(
    owner_id: OwnerId,
    profiles: Annotated[str, Form()] = "",
    group: GroupField = "",
) -> WatchAdded:
    """Adds every profile on its own line to `group` and queues the first
    scan of the new ones. One bad line rejects the lot, before anything is
    added."""
    return page.add(profiles, group, owner_id)


@router.post("/group/{username}")
def move(username: str, owner_id: OwnerId, group: GroupField = "") -> ProfileMoved:
    """A watch-list row's group dropdown: moves the profile ("" = no group)."""
    return page.move(username, group, owner_id)


@router.post("/unwatch/{username}")
def remove(username: str, owner_id: OwnerId) -> ProfileRemoved:
    return ProfileRemoved(removed=page.remove(username, owner_id))


@router.post("/download")
def download(reels: PickedReels) -> Response:
    """The picked reels as a file: the mp4 itself for one, a zip for more.
    Nothing is kept: the job dir goes once the response is sent."""
    picked = page.picked_file(reels)
    return FileResponse(
        picked.path,
        media_type=picked.media_type,
        filename=picked.path.name,
        background=BackgroundTask(picked.forget),
    )


@router.post("/clone")
def clone(reels: PickedReels, assets_root: AssetsRoot) -> Redirect:
    """Downloads the picked reels into the asset store and answers with the
    /clone URL that opens with them picked."""
    return page.clone(reels, assets_root)
