"""What /links shows and does behind its routes
(web/routers/tracking/links.py): the list over one period, a Generate checked
and its rows made, a creator assigned, which links an action may touch. The
worker side is `sync.py`."""

from collections.abc import Callable

from pydantic import BaseModel

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.stores import self_links, short_links
from ofmhelpers.db.stores.jobs import create_job
from ofmhelpers.db.stores.tracked_accounts import list_accounts, list_creators
from ofmhelpers.features.jobs.runner import run_job
from ofmhelpers.features.links import keys as link_keys
from ofmhelpers.features.links import sync
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.integrations.short_links import (
    ProviderInfo,
    Target,
    provider_infos,
    targets,
)
from ofmhelpers.schemas import (
    GenerateLinks,
    LinkPage,
    LinkPeriod,
    LinkQuery,
    LinkSort,
    LinksQueued,
    LinkSummary,
    ProviderState,
    TrackedAccount,
)


class ListView(BaseModel):
    """/links: each field is a name `links.html` reads."""

    page: LinkPage
    summary: LinkSummary
    providers: list[ProviderState]
    infos: dict[str, ProviderInfo]
    targets: dict[str, Target]
    accounts: list[TrackedAccount]
    source_label: str
    period: LinkPeriod
    periods: list[LinkPeriod]
    sorts: type[LinkSort]
    creators: list[str]
    page_sizes: list[int]
    max_generate: int


class Deleting(BaseModel):
    """A Delete: `dead` links (never made) removed here at once, `live` ones
    marked `deleting` for the worker to delete on their provider."""

    dead: int
    live: list[int]


# The job rows of the three actions that run on the worker (`sync.py`).
CREATE_TASK = "links_create"
ADULT_TASK = "links_adult"
DELETE_TASK = "links_delete"


def _start(task: str, run: Callable[..., None], user_id: int, **what: object) -> None:
    """The action as a job of `user_id`: its row says which links, `run`
    does it on the worker."""
    job_id = create_job(task, dict(what), actor=str(user_id))
    enqueue(run_job, job_id, run, {"user_id": user_id, **what})


def _period(query: LinkQuery) -> LinkPeriod:
    s = settings.links
    days = query.period if query.period in s.periods_days else s.default_period_days
    return LinkPeriod(days=days)


def _infos_with_self_domains(owner: int) -> dict[str, ProviderInfo]:
    """`provider_infos()` with the self provider's domains filled from the
    live list (it has none in the static config)."""
    infos = dict(provider_infos().entries)
    if "self" in infos:
        hosts = self_links.active_hosts(owner)
        infos["self"] = infos["self"].model_copy(update={"domains": hosts})
    return infos


def list_view(query: LinkQuery, scope: int) -> ListView:
    s = settings.links
    period = _period(query)
    matching = [x for x in short_links.list_links(scope) if query.matches(x)]
    page = LinkPage.of(
        query.ordered(matching, period.days),
        query.page,
        query.per_page or s.default_page_size,
    )
    return ListView(
        page=page,
        summary=LinkSummary.of(matching, period.days),
        providers=link_keys.provider_states(scope),
        infos=_infos_with_self_domains(scope),
        targets=targets().entries,
        accounts=short_links.source_accounts(list_accounts(scope)),
        source_label=short_links.source_label(),
        period=period,
        periods=[LinkPeriod(days=d) for d in s.periods_days],
        sorts=LinkSort,
        creators=list_creators(scope),
        page_sizes=s.page_sizes,
        max_generate=s.max_generate,
    )


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


def _require_provider(body: GenerateLinks, owner_id: int) -> None:
    """The person has the provider's key, the domain and room for the links."""
    if link_keys.api_key(owner_id, body.provider) is None:
        label = provider_infos()[body.provider].label
        msg = f"Add your {label} API key on /account first"
        raise RefusedError(msg)
    if body.provider == "self" and body.domain not in self_links.active_hosts(owner_id):
        msg = "Add this domain on Self links first"
        raise RefusedError(msg)
    [state] = [p for p in link_keys.provider_states(owner_id) if p.key == body.provider]
    if problem := _no_room(state, len(set(body.account_ids))):
        raise RefusedError(problem)


def planned(body: GenerateLinks, owner_id: int) -> list[int]:
    """One link row per picked account, `creating` (they show while being
    made): their ids, for the worker to make on the provider."""
    if len(body.account_ids) > settings.links.max_generate:
        msg = f"At most {settings.links.max_generate} links at once"
        raise RefusedError(msg)
    _require_provider(body, owner_id)
    mine = {a.id: a for a in short_links.source_accounts(list_accounts(owner_id))}
    if any(i not in mine for i in body.account_ids):
        msg = "Account not tracked"
        raise NotFoundError(msg)
    accounts = [mine[i] for i in dict.fromkeys(body.account_ids)]
    links = short_links.plan(owner_id, body, accounts)
    return short_links.add_creating(owner_id, body.provider, links)


def assign_creator(ids: list[int], creator: str, scope: int) -> int:
    """Assigns the links to one of the person's creators ("" = none): how
    many were."""
    if creator and creator not in list_creators(scope):
        msg = "Unknown creator"
        raise NotFoundError(msg)
    return short_links.assign_creator(ids, creator or None, scope)


def own_live(scope: int, ids: list[int]) -> list[int]:
    """Those of `ids` that are the person's and exist on their provider."""
    found = short_links.get_links(ids, scope)
    return [x.id for x in found if x.remote_id and not x.busy]


def start_delete(ids: list[int], scope: int) -> Deleting:
    """A failed link (never made) is only removed here; one on its provider
    is marked `deleting` (its address stops working once the worker did)."""
    found = short_links.get_links(ids, scope)
    dead = [x.id for x in found if x.status == "failed" and x.remote_id is None]
    live = [x.id for x in found if x.remote_id and not x.busy]
    short_links.removed(dead)
    if live:
        short_links.set_status(live, "deleting")
    return Deleting(dead=len(dead), live=live)


def generate(body: GenerateLinks, owner_id: int) -> LinksQueued:
    """One link per picked account, queued; the rows show while being made."""
    ids = planned(body, owner_id)
    _start(
        CREATE_TASK, sync.create_links, owner_id, provider=body.provider, link_ids=ids
    )
    return LinksQueued(count=len(ids))


def gate(ids: list[int], scope: int) -> LinksQueued:
    """The 18+ confirmation turned on for the person's links among `ids`."""
    live = own_live(scope, ids)
    if live:
        _start(ADULT_TASK, sync.enable_adult, scope, link_ids=live)
    return LinksQueued(count=len(live))


def delete(ids: list[int], scope: int) -> LinksQueued:
    """The links deleted on their provider (their address stops working),
    then here; a failed one (never made) is only removed here."""
    found = start_delete(ids, scope)
    if found.live:
        _start(DELETE_TASK, sync.delete_links, scope, link_ids=found.live)
    return LinksQueued(count=found.dead + len(found.live))


def sync_now(scope: int) -> LinksQueued:
    """The person's links synced now, on every provider they have a key for."""
    return LinksQueued(count=len(sync.sync_now(scope)))
