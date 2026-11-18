"""Is the Pinterest operation worth it: per UTC day and model, pins made
(of them `ig_pins`, the "ig: <handle>" kind with no link) and failed over
all accounts, and the model's self link's views (people who opened it from
a pin) and clicks (who went on to the model's page); then per model and
per account over the whole window. Read from the posts table and the
bridge's own click log (`self_link_clicks`), which keep everything for
good. The CLI is `__main__.py`."""

import datetime

from pydantic import BaseModel, computed_field

from ofmhelpers.db.stores import distribution as store
from ofmhelpers.db.stores import self_links


class ModelDay(BaseModel):
    day: datetime.date
    model: str
    pins: int = 0
    ig_pins: int = 0
    failed: int = 0
    link_views: int = 0
    link_clicks: int = 0


class ModelTotal(BaseModel):
    model: str
    days: int
    pins: int = 0
    ig_pins: int = 0
    failed: int = 0
    link_views: int = 0
    link_clicks: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def views_per_linked_pin(self) -> float:
        """Link views per pin that carried the link (an "ig:" pin has none)."""
        linked = self.pins - self.ig_pins
        return round(self.link_views / linked, 2) if linked else 0.0


class AccountTotal(BaseModel):
    account: str
    days: int
    pins: int = 0
    failed: int = 0
    stopped: str | None = None


class Report(BaseModel):
    days: list[ModelDay]
    models: list[ModelTotal]
    accounts: list[AccountTotal]


def report(days: int, owner: int | None = None) -> Report:
    """The last `days` UTC days (today included) of `owner`'s accounts and
    models (None: everyone's)."""
    repo = store.repository()
    channels = repo.all_channels() if owner is None else repo.channels_of(owner)
    end = datetime.datetime.now(datetime.UTC).date()
    start = end - datetime.timedelta(days=days - 1)
    by_day: dict[tuple[datetime.date, str], ModelDay] = {}
    per_account = {
        c.id: AccountTotal(account=c.account_name, days=days, stopped=c.error)
        for c in channels
    }
    for pins in repo.pins_by_day(list(per_account), start):
        row = by_day.setdefault(
            (pins.day, pins.preset), ModelDay(day=pins.day, model=pins.preset)
        )
        row.pins += pins.published
        row.ig_pins += pins.ig
        row.failed += pins.failed
        account = per_account[pins.channel_id]
        account.pins += pins.published
        account.failed += pins.failed
    owners = {c.user_id for c in channels}
    links = {m.link_id: m.preset for o in owners for m in repo.model_links(o)}
    for link in self_links.days_of(list(links), start, end):
        model = links[link.link_id]
        for hit in link.days:
            row = by_day.setdefault(
                (hit.day, model), ModelDay(day=hit.day, model=model)
            )
            row.link_views += hit.views
            row.link_clicks += hit.clicks
    ordered = sorted(by_day.values(), key=lambda r: (r.day, r.model))
    models = sorted({r.model for r in ordered})
    return Report(
        days=ordered,
        models=[_model_total(m, ordered, days) for m in models],
        accounts=list(per_account.values()),
    )


def _model_total(model: str, rows: list[ModelDay], days: int) -> ModelTotal:
    mine = [r for r in rows if r.model == model]
    return ModelTotal(
        model=model,
        days=days,
        pins=sum(r.pins for r in mine),
        ig_pins=sum(r.ig_pins for r in mine),
        failed=sum(r.failed for r in mine),
        link_views=sum(r.link_views for r in mine),
        link_clicks=sum(r.link_clicks for r in mine),
    )
