"""
/links: short links on link providers (Oopsie, Bouncy; `ofmhelpers.links`)
with their views, clicks and CTR over one period
(`settings.links.periods_days`, picked on the page), the Instagram account
each bridges from, its creator (model) and target page; KPI totals over
the filtered list and each provider's quota on top. Filters, period, sort
and paging are the query string (`schemas.LinkQuery`), so every view is a
link.

"Generate links" makes one 18+ gated link per picked Instagram account of
a model, all to one target page. Making, syncing, the 18+ fix and deleting
run on the worker (web/link_sync.py); static/js/links.js reloads while a
row is still being made or deleted.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.links import provider_infos, targets
from ofmhelpers.web import link_sync
from ofmhelpers.web.middleware import AuthMiddleware
from ofmhelpers.web.schemas import (
    AssignLinkCreator,
    GenerateLinks,
    LinkPage,
    LinkPeriod,
    LinkQuery,
    LinkSort,
    LinksQueued,
    LinkSummary,
    ProviderState,
    SelectedLinks,
)
from ofmhelpers.web.stores import short_links
from ofmhelpers.web.stores.tracked_accounts import list_accounts, list_creators
from ofmhelpers.web.templates_config import render

router = APIRouter(prefix="/links", tags=["links"])


def _period(query: LinkQuery) -> LinkPeriod:
    s = settings.links
    days = query.period if query.period in s.periods_days else s.default_period_days
    return LinkPeriod(days=days)


def _no_room(state: ProviderState, wanted: int) -> str | None:
    """Why `wanted` new links would not all go live on the provider, as its
    last sync saw the plan (a full plan refuses them; past Bouncy's active
    limit they start inactive); None when they fit, or nothing is known."""
    usage = state.usage
    if usage is None or usage.room is None or wanted <= usage.room:
        return None
    if usage.full:
        return (
            f"{state.label} plan is full ({usage.used} / {usage.limit}):"
            " delete links there or upgrade first"
        )
    active = (
        f", {usage.active} / {usage.active_limit} active"
        if usage.active_limit is not None
        else ""
    )
    return (
        f"{state.label} has room for {usage.room} more live link"
        f"{'' if usage.room == 1 else 's'} ({usage.used} / {usage.limit} made{active}):"
        " pick fewer accounts, or free links there first"
    )


class _Count(BaseModel):
    count: int


@router.get("")
def index(request: Request, query: Annotated[LinkQuery, Query()]):
    s = settings.links
    scope = AuthMiddleware.scope(request)
    period = _period(query)
    matching = [x for x in short_links.list_links(scope) if query.matches(x)]
    page = LinkPage.of(
        query.ordered(matching, period.days),
        query.page,
        query.per_page or s.default_page_size,
    )
    return render(
        request,
        "links.html",
        page=page,
        summary=LinkSummary.of(matching, period.days),
        providers=short_links.provider_states(scope),
        infos=provider_infos(),
        targets=targets(),
        accounts=short_links.source_accounts(list_accounts(scope)),
        source_label=short_links.source_label(),
        query=query,
        period=period,
        periods=[LinkPeriod(days=d) for d in s.periods_days],
        sorts=LinkSort,
        creators=list_creators(scope),
        page_sizes=s.page_sizes,
        max_generate=s.max_generate,
    )


@router.post("/generate")
def generate(request: Request, body: GenerateLinks) -> LinksQueued:
    """One link per picked account, queued; the rows show while being
    made."""
    user_id = AuthMiddleware.owner_id(request)
    if len(body.account_ids) > settings.links.max_generate:
        raise HTTPException(
            status_code=400,
            detail=f"At most {settings.links.max_generate} links at once",
        )
    if short_links.api_key(user_id, body.provider) is None:
        label = provider_infos()[body.provider].label
        raise HTTPException(
            status_code=400, detail=f"Add your {label} API key on /account first"
        )
    [state] = [
        p for p in short_links.provider_states(user_id) if p.key == body.provider
    ]
    if problem := _no_room(state, len(set(body.account_ids))):
        raise HTTPException(status_code=400, detail=problem)
    mine = {a.id: a for a in short_links.source_accounts(list_accounts(user_id))}
    unknown = [i for i in body.account_ids if i not in mine]
    if unknown:
        raise HTTPException(status_code=404, detail="Account not tracked")
    accounts = [mine[i] for i in dict.fromkeys(body.account_ids)]
    planned = short_links.plan(user_id, body, accounts)
    ids = short_links.add_creating(user_id, body.provider, planned)
    enqueue(link_sync.create_links, user_id, body.provider, ids)
    return LinksQueued(count=len(ids))


@router.post("/sync")
def sync(request: Request) -> LinksQueued:
    """The signed-in person's links synced now, on every provider they have
    a key for."""
    user_id = AuthMiddleware.scope(request)
    return LinksQueued(count=len(link_sync.sync_now(user_id)))


@router.post("/creator")
def creator(request: Request, body: AssignLinkCreator) -> _Count:
    """Assigns the links to one of the person's creators ("" = none)."""
    scope = AuthMiddleware.scope(request)
    if body.creator and body.creator not in list_creators(scope):
        raise HTTPException(status_code=404, detail="Unknown creator")
    return _Count(
        count=short_links.assign_creator(body.ids, body.creator or None, scope)
    )


def _own_live(request: Request, ids: list[int]) -> list[int]:
    """Those of `ids` that are the person's and exist on their provider."""
    found = short_links.get_links(ids, AuthMiddleware.scope(request))
    return [x.id for x in found if x.remote_id and not x.busy]


@router.post("/adult")
def adult(request: Request, body: SelectedLinks) -> LinksQueued:
    """Turns the 18+ confirmation on for the links."""
    ids = _own_live(request, body.ids)
    if ids:
        enqueue(link_sync.enable_adult, AuthMiddleware.scope(request), ids)
    return LinksQueued(count=len(ids))


@router.post("/delete")
def delete(request: Request, body: SelectedLinks) -> LinksQueued:
    """Deletes the links on their provider (their address stops working),
    then here; a failed one (never made) is only removed here."""
    user_id = AuthMiddleware.scope(request)
    found = short_links.get_links(body.ids, user_id)
    dead = [x.id for x in found if x.status == "failed" and x.remote_id is None]
    live = [x.id for x in found if x.remote_id and not x.busy]
    short_links.removed(dead)
    if live:
        short_links.set_status(live, "deleting")
        enqueue(link_sync.delete_links, user_id, live)
    return LinksQueued(count=len(dead) + len(live))
