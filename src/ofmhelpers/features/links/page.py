from types import MappingProxyType
lazy from collections.abc import Callable

from pydantic import BaseModel, ConfigDict

from ofmhelpers.cache import enqueue
from ofmhelpers.config import settings
from ofmhelpers.db.jobs.store import create_job
from ofmhelpers.db.self_links import store as self_links
from ofmhelpers.db.short_links import store as short_links
from ofmhelpers.db.short_links.models import (
    GenerateLinks,
    LinkPage,
    LinkQuery,
    LinkSummary,
    ProviderState,
)
from ofmhelpers.db.short_links.schema import LinkPeriod, LinkSort, LinksQueued
from ofmhelpers.db.tracked_accounts.schema import TrackedAccount
from ofmhelpers.db.tracked_accounts.store import list_accounts, list_creators
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


class ListView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    page: LinkPage
    summary: LinkSummary
    providers: list[ProviderState]
    infos: MappingProxyType[str, ProviderInfo]
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
    dead: int
    live: list[int]


CREATE_TASK = "links_create"
ADULT_TASK = "links_adult"
DELETE_TASK = "links_delete"


def _start(
    task: str, run: Callable[..., None], user_id: int, what: dict[str, object]
) -> None:
    job_id = create_job(task, dict(what), actor=str(user_id))
    enqueue(run_job, (job_id, run, {"user_id": user_id, **what}))


def _period(query: LinkQuery) -> LinkPeriod:
    s = settings.links
    days = query.period if query.period in s.periods_days else s.default_period_days
    return LinkPeriod(days=days)


def _infos_with_self_domains(owner: int) -> MappingProxyType[str, ProviderInfo]:
    infos = dict(provider_infos())
    if "self" in infos:
        hosts = self_links.active_hosts(owner)
        infos["self"] = infos["self"].model_copy(update={"domains": hosts})
    return MappingProxyType(infos)


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
        targets=dict(targets()),
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
    if creator and creator not in list_creators(scope):
        msg = "Unknown creator"
        raise NotFoundError(msg)
    return short_links.assign_creator(ids, creator or None, scope)


def own_live(scope: int, ids: list[int]) -> list[int]:
    found = short_links.get_links(ids, scope)
    return [x.id for x in found if x.remote_id and not x.busy]


def start_delete(ids: list[int], scope: int) -> Deleting:
    found = short_links.get_links(ids, scope)
    dead = [x.id for x in found if x.status == "failed" and x.remote_id is None]
    live = [x.id for x in found if x.remote_id and not x.busy]
    short_links.removed(dead)
    if live:
        short_links.set_status(live, "deleting")
    return Deleting(dead=len(dead), live=live)


def generate(body: GenerateLinks, owner_id: int) -> LinksQueued:
    ids = planned(body, owner_id)
    _start(
        CREATE_TASK,
        sync.create_links,
        owner_id,
        {"provider": body.provider, "link_ids": ids},
    )
    return LinksQueued(count=len(ids))


def gate(ids: list[int], scope: int) -> LinksQueued:
    live = own_live(scope, ids)
    if live:
        _start(ADULT_TASK, sync.enable_adult, scope, {"link_ids": live})
    return LinksQueued(count=len(live))


def delete(ids: list[int], scope: int) -> LinksQueued:
    found = start_delete(ids, scope)
    if found.live:
        _start(DELETE_TASK, sync.delete_links, scope, {"link_ids": found.live})
    return LinksQueued(count=found.dead + len(found.live))


def sync_now(scope: int) -> LinksQueued:
    return LinksQueued(count=len(sync.sync_now(scope)))
