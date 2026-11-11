"""
/clone-links: reel links + subject image(s) -> one Wan 3.0 run per link,
made exactly like a /clone run (`clone._run_clone`: provider, export,
mirror), each result then checked for glitches and uploaded into the
clicker's Drive (features/clone_links/).

Check (`POST /check`, instant, nothing downloaded): the pasted text read as
links (`clone_links.links.read_pasted`: one post per line, cleaned, each
once). Any line that is not exactly one post of a video site refuses the
paste -- "Please put one link per line", every such line and why, the box
kept as pasted; else the box gets the cleaned list.

Generate: the paste checked again, then the page's dialog previews every link
(`POST /previews`, one request streaming a line per link, read in
parallel: `clone_links.preview` -- cover and length read by yt-dlp without
downloading, cached in Redis per link, priced like
/clone's panel: length x credits per second x $ per credit). One over the
provider's limit (Wan 3.0: 15 s) or unreadable is shown and cannot be
ticked; the VA unticks what they do not want and confirms the total.
`/run` checks the settings and the links again and queues one run per
ticked link on the clone-links queue (its own worker pool: a 120-link
Generate never holds the shared queue): the worker downloads it, refuses
one over the limit before anything is paid, then runs the clone
(`clone_links.run`). Google Drive must be connected (checked before any
preview is read, and again at `/run`), since that is where every result
goes (`clone_links.review`): GOOD REELS or TRIAL REELS.

A Generate is one `batch` (the page's id for it, sent with `/run`): a
resend of the same batch -- the answer lost, a retry after an error --
returns the runs already queued and pays for nothing again. Stop
(`POST /stop/{job}`) stops a card's whole batch: every run not yet paid
fails "Stopped" when it reaches the worker; a paid one finishes.
"""

from typing import Annotated

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel, Field

from ofmhelpers.config import settings
from ofmhelpers.features.clone.targets import DEFAULT_RESOLUTION
from ofmhelpers.features.clone_links import service
from ofmhelpers.features.clone_links.job import run_clone_link
from ofmhelpers.features.clone_links.models import (
    CheckedLinks,
    PreviewsRequest,
    RecreateLinks,
)
from ofmhelpers.features.clone_links.page import form_view
from ofmhelpers.features.clone_links.service import (
    BATCH,
    FILES_PREFIX,
    LABEL,
    NOT_QUEUED,
    PREFIX,
    TASK,
    THUMBS_PREFIX,
    BatchStopped,
    Generate,
    Sent,
)
from ofmhelpers.schemas import CloneQueued
from ofmhelpers.web.dependencies import (
    Actor,
    ApiKeys,
    AssetsRoot,
    CurrentUser,
    OwnerId,
    Scope,
)
from ofmhelpers.web.forms import Files, PaidForm
from ofmhelpers.web.gallery import Gallery, GalleryParams
from ofmhelpers.web.responses import PRIVATE_FOR_A_DAY
from ofmhelpers.web.routers.generation.clone import default_prompt
from ofmhelpers.web.routers.task_helpers import add_job_routes
from ofmhelpers.web.templates_config import render, render_partial, render_polled

# What other modules and the tests read here (same objects as the feature's).
__all__ = [
    "BATCH",
    "FILES_PREFIX",
    "LABEL",
    "NOT_QUEUED",
    "TASK",
    "THUMBS_PREFIX",
    "router",
    "run_clone_link",
]

router = APIRouter(prefix=PREFIX, tags=["clone-links"])

# The preview stream line by line through the host's nginx, never buffered.
_UNBUFFERED = {"X-Accel-Buffering": "no"}

gallery = Gallery({TASK})


class CloneLinksForm(PaidForm):
    prompt: str = Field(default_factory=default_prompt, min_length=1)
    resolution: str = DEFAULT_RESOLUTION
    audio: bool = False
    export: str = Field(default_factory=lambda: settings.exports.default)
    mirror: bool = False
    image: Files
    image_manifest: str = "[]"
    links: list[str] = Field(default_factory=list)
    batch: str | None = Field(None, pattern=BATCH)


class ReviewState(BaseModel):
    """A card's review block, rendered, and whether it can still change on
    its own (the page stops polling a settled one)."""

    job_id: str
    settled: bool
    html: str


# --- Check and preview -----------------------------------------------------------


@router.post("/check")
def check(links: Annotated[str, Form()] = "") -> CheckedLinks:
    """The paste cleaned (each link once), or refused naming every bad
    line. Nothing is downloaded."""
    return CheckedLinks(links=service.read(links))


@router.post("/previews")
def previews(
    body: PreviewsRequest, user: CurrentUser, owner_id: OwnerId
) -> StreamingResponse:
    """Generate's dialog: one JSON line (`LinkPreview`) per link as each is
    done, cached ones first. The page fills each tile as its line arrives."""
    found = service.previews(
        body.urls, body.provider, body.resolution, user.id, owner_id
    )
    return StreamingResponse(
        (p.model_dump_json() + "\n" for p in found),
        media_type="application/x-ndjson",
        headers=_UNBUFFERED,
    )


@router.get("/thumb/{key}")
def thumb(key: str) -> FileResponse:
    """A previewed link's cover, kept on disk under its link's hash: fine to
    cache a day."""
    return FileResponse(
        service.cover(key), media_type="image/webp", headers=PRIVATE_FOR_A_DAY
    )


# --- Generate ------------------------------------------------------------------


def _queued(sent: Sent, user_id: int) -> CloneQueued:
    """`/run`'s answer: the runs' ids in paste order, their cards newest
    first like the gallery."""
    html = "".join(
        render_partial("_clone_links_card.html", job=card)
        for card in service.cards(list(reversed(sent.jobs)), user_id)
    )
    return CloneQueued(job_ids=[j.id for j in sent.jobs], html=html, batch=sent.batch)


@router.post("/run", status_code=202)
def run(
    form: Annotated[CloneLinksForm, Form()],
    user: CurrentUser,
    actor: Actor,
    owner_id: OwnerId,
    assets_root: AssetsRoot,
) -> CloneQueued:
    """One run per ticked link; their job ids and cards, newest first like
    the gallery. `batch`: the page's id of this Generate -- one already sent
    answers with its runs and queues nothing (none: a new id)."""
    generate = Generate(
        actor=actor,
        user_id=user.id,
        owner_id=owner_id,
        assets_root=assets_root,
        run=run_clone_link,
    )
    return _queued(generate.start(form), user.id)


# --- Gallery and reviews -------------------------------------------------------


@router.get("")
def form(
    request: Request, user: CurrentUser, actor: Actor, scope: Scope, api_keys: ApiKeys
) -> Response:
    first = gallery.page(actor)
    return render(
        request,
        "clone_links.html",
        gallery=service.cards(first.jobs, scope),
        next_offset=first.next_offset,
        hidden_count=gallery.hidden_count(actor),
        api_keys=api_keys,
        **dict(form_view(actor, user.id)),
    )


@router.get("/gallery")
def gallery_page(
    request: Request, scope: Scope, actor: Actor, query: GalleryParams
) -> Response:
    """The next page of cards (gallery-scroll.js); `hidden`: only the hidden
    cards (hidden-cards.js's "Show hidden")."""
    found = gallery.page(actor, query)
    return render(
        request,
        "clone_links_gallery_fragment.html",
        gallery=service.cards(found.jobs, scope),
        next_offset=found.next_offset,
    )


@router.get("/gallery/latest")
def gallery_latest(request: Request, actor: Actor, scope: Scope) -> Response:
    """The newest page of cards, no sentinel: what live-gallery.js polls."""
    return render_polled(
        request,
        "clone_links_gallery_fragment.html",
        gallery=service.cards(gallery.page(actor).jobs, scope),
        next_offset=None,
    )


@router.get("/recreate/{job_id}")
def recreate_links(job_id: str, actor: Actor) -> RecreateLinks:
    """The links a card's Recreate puts back in the box (none: nothing to
    redo)."""
    return service.recreate(job_id, actor)


@router.post("/stop/{job_id}")
def stop(job_id: str, actor: Actor) -> BatchStopped:
    """Stop: the Generate of this card stopped (`clone_links.stop`)."""
    return service.stop(job_id, actor)


@router.get("/reviews")
def reviews(
    job: Annotated[list[str], Query(max_length=200)], actor: Actor, scope: Scope
) -> list[ReviewState]:
    """These runs' review blocks: what the page polls while reviews run."""
    return [
        ReviewState(
            job_id=card.job_id,
            settled=card.review_settled,
            html=render_partial("_clone_from_link.html", job=card),
        )
        for card in service.cards_of(job, actor, scope)
    ]


add_job_routes(
    router,
    LABEL,
    lambda _job: "Generating video… this can take a few minutes.",
    back_url="/clone-links",
    back_label="clone more",
    show_inputs=True,
)
