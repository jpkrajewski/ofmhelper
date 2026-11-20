from pydantic import BaseModel, ConfigDict

from ofmhelpers.config import settings
from ofmhelpers.db.stores import tracked_accounts as store
from ofmhelpers.db.stores.devices import phones_by_account
from ofmhelpers.db.stores.short_links import links_by_account
from ofmhelpers.features.accounts.tracker import queue_due_scans
from ofmhelpers.features.cookies import jar as cookie_jar
from ofmhelpers.features.refusals import NotFoundError, RefusedError
from ofmhelpers.integrations.socials import platforms
from ofmhelpers.integrations.socials.platforms.base import Platform
from ofmhelpers.schemas import (
    AccountPage,
    AccountQuery,
    AccountSort,
    AccountSummary,
    AddAccounts,
    AddResult,
    LinkPeriod,
    Period,
    ShortLink,
    TrackedAccount,
)
from ofmhelpers.schemas.accounts import FollowerChart, TopPosts
from ofmhelpers.schemas.devices import PhonesByAccount
from ofmhelpers.schemas.phone_catalog import tracked_services


class ListView(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    page: AccountPage
    phones: PhonesByAccount
    phone_platforms: list[str]
    links: dict[int, list[ShortLink]]
    link_period: LinkPeriod
    summary: AccountSummary
    ask_for_cookies: bool
    syncing: bool
    period: Period
    periods: list[Period]
    sorts: type[AccountSort]
    platforms: dict[str, Platform]
    creators: list[str]
    page_sizes: list[int]
    max_add: int


class AnalyticsView(BaseModel):
    account: TrackedAccount
    chart: FollowerChart
    top: TopPosts
    ask_for_cookies: bool
    period: Period
    periods: list[Period]


def _period(query: AccountQuery) -> Period:
    s = settings.accounts
    days = query.period if query.period in s.periods_days else s.default_period_days
    return Period(days=days)


def _periods() -> list[Period]:
    return [Period(days=d) for d in settings.accounts.periods_days]


def _ask_for_cookies(limited: bool, user_id: int) -> bool:
    return limited and cookie_jar.saved(user_id) is None


def list_view(query: AccountQuery, scope: int) -> ListView:
    s = settings.accounts
    period = _period(query)
    mine = store.list_accounts(scope)
    matching = [a for a in mine if query.matches(a)]
    accounts = query.ordered(matching, period.days)
    page = AccountPage.of(accounts, query.page, query.per_page or s.default_page_size)
    summary = AccountSummary.of(matching, period.days)
    return ListView(
        page=page,
        phones=phones_by_account(page.rows, scope),
        phone_platforms=tracked_services().names(),
        links={x.account_id: x.links for x in links_by_account(scope)},
        link_period=LinkPeriod(days=period.days),
        summary=summary,
        ask_for_cookies=_ask_for_cookies(summary.limited > 0, scope),
        syncing=any(a.syncing for a in mine),
        period=period,
        periods=_periods(),
        sorts=AccountSort,
        platforms=platforms().entries,
        creators=store.list_creators(scope),
        page_sizes=s.page_sizes,
        max_add=s.max_add,
    )


def analytics_view(account_id: int, query: AccountQuery, scope: int) -> AnalyticsView:
    account = store.get_account(account_id, scope)
    if account is None:
        msg = "Account not tracked"
        raise NotFoundError(msg)
    s = settings.accounts
    period = _period(query)
    history = store.get_history(account_id)
    return AnalyticsView(
        account=account,
        chart=history.follower_chart(period.days, s.rescan_interval_s),
        top=account.top_posts(period.days, s.top_posts),
        ask_for_cookies=_ask_for_cookies(
            bool(account.metrics and account.metrics.limited), scope
        ),
        period=period,
        periods=_periods(),
    )


def add(body: AddAccounts, owner_id: int) -> AddResult:
    try:
        handles = body.parsed()
    except ValueError as exc:
        raise RefusedError(str(exc)) from exc
    if not handles:
        msg = "Add at least one account"
        raise RefusedError(msg)
    if len(handles) > settings.accounts.max_add:
        msg = f"At most {settings.accounts.max_add} accounts at once"
        raise RefusedError(msg)
    added = [
        h
        for h in handles
        if store.track(body.platform, h, body.creator or None, user_id=owner_id)
    ]
    queue_due_scans()
    return AddResult(added=added, existing=len(handles) - len(added))


def set_notes(account_id: int, notes: str, scope: int) -> None:
    if not store.set_notes(account_id, notes, scope):
        msg = "Account not tracked"
        raise NotFoundError(msg)


def assign_creator(account_ids: list[int], creator: str, scope: int) -> int:
    return store.assign_creator(account_ids, creator or None, scope)
