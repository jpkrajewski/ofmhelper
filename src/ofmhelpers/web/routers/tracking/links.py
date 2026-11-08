"""
/links: short links on link providers (Oopsie, Bouncy; `ofmhelpers.integrations.short_links`)
with their views, clicks and CTR over one period
(`settings.links.periods_days`, picked on the page), the Instagram account
each bridges from, its creator (model) and target page; KPI totals over
the filtered list and each provider's quota on top. Filters, period, sort
and paging are the query string (`schemas.LinkQuery`), so every view is a
link.

"Generate links" makes one 18+ gated link per picked Instagram account of
a model, all to one target page. Making, syncing, the 18+ fix and deleting
run on the worker (features/links/sync.py); static/js/links.js reloads while a
row is still being made or deleted.
"""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores import self_links, short_links
from ofmhelpers.db.stores.tracked_accounts import list_accounts, list_creators
from ofmhelpers.features.links import sync as link_sync
from ofmhelpers.integrations.short_links import provider_infos, targets
from ofmhelpers.schemas import (
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
from ofmhelpers.web.dependencies import OwnerId, Scope
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


def _infos_with_self_domains(owner: int):
    """`provider_infos()` with the self provider's domains filled from the
    live list (it has none in the static config)."""
    infos = dict(provider_infos())
    if "self" in infos:
        hosts = self_links.active_hosts(owner)
        infos["self"] = infos["self"].model_copy(update={"domains": hosts})
    return infos


@router.get("")
def index(
    request: Request, query: Annotated[LinkQuery, Query()], scope: Scope
) -> Response:
    s = settings.links
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
        infos=_infos_with_self_domains(scope),
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
def generate(body: GenerateLinks, owner_id: OwnerId) -> LinksQueued:
    """One link per picked account, queued; the rows show while being
    made."""
    if len(body.account_ids) > settings.links.max_generate:
        raise HTTPException(
            status_code=400,
            detail=f"At most {settings.links.max_generate} links at once",
        )
    if short_links.api_key(owner_id, body.provider) is None:
        label = provider_infos()[body.provider].label
        raise HTTPException(
            status_code=400, detail=f"Add your {label} API key on /account first"
        )
    if body.provider == "self" and body.domain not in self_links.active_hosts(owner_id):
        raise HTTPException(
            status_code=400,
            detail="Add this domain on Self links first",
        )
    [state] = [
        p for p in short_links.provider_states(owner_id) if p.key == body.provider
    ]
    if problem := _no_room(state, len(set(body.account_ids))):
        raise HTTPException(status_code=400, detail=problem)
    mine = {a.id: a for a in short_links.source_accounts(list_accounts(owner_id))}
    unknown = [i for i in body.account_ids if i not in mine]
    if unknown:
        raise HTTPException(status_code=404, detail="Account not tracked")
    accounts = [mine[i] for i in dict.fromkeys(body.account_ids)]
    planned = short_links.plan(owner_id, body, accounts)
    ids = short_links.add_creating(owner_id, body.provider, planned)
    enqueue(link_sync.create_links, owner_id, body.provider, ids)
    return LinksQueued(count=len(ids))


@router.post("/sync")
def sync(scope: Scope) -> LinksQueued:
    """The signed-in person's links synced now, on every provider they have
    a key for."""
    return LinksQueued(count=len(link_sync.sync_now(scope)))


@router.post("/creator")
def creator(body: AssignLinkCreator, scope: Scope) -> _Count:
    """Assigns the links to one of the person's creators ("" = none)."""
    if body.creator and body.creator not in list_creators(scope):
        raise HTTPException(status_code=404, detail="Unknown creator")
    return _Count(
        count=short_links.assign_creator(body.ids, body.creator or None, scope)
    )


def _own_live(scope: int, ids: list[int]) -> list[int]:
    """Those of `ids` that are the person's and exist on their provider."""
    found = short_links.get_links(ids, scope)
    return [x.id for x in found if x.remote_id and not x.busy]


@router.post("/adult")
def adult(body: SelectedLinks, scope: Scope) -> LinksQueued:
    """Turns the 18+ confirmation on for the links."""
    ids = _own_live(scope, body.ids)
    if ids:
        enqueue(link_sync.enable_adult, scope, ids)
    return LinksQueued(count=len(ids))


@router.post("/delete")
def delete(body: SelectedLinks, scope: Scope) -> LinksQueued:
    """Deletes the links on their provider (their address stops working),
    then here; a failed one (never made) is only removed here."""
    found = short_links.get_links(body.ids, scope)
    dead = [x.id for x in found if x.status == "failed" and x.remote_id is None]
    live = [x.id for x in found if x.remote_id and not x.busy]
    short_links.removed(dead)
    if live:
        short_links.set_status(live, "deleting")
        enqueue(link_sync.delete_links, scope, live)
    return LinksQueued(count=len(dead) + len(live))
