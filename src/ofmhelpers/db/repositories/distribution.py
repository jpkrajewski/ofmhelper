from datetime import UTC, date, datetime, timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from ofmhelpers.db.models import (
    DistributionChannelRow,
    DistributionPostRow,
    DistributionPresetRow,
    ShortLinkRow,
    UserRow,
)
from ofmhelpers.db.session import session_scope
from ofmhelpers.schemas.distribution import (
    Channel,
    Failing,
    ModelLink,
    ModelPreset,
    NewPost,
    PinDay,
    Post,
    PostCounts,
    PostKind,
    PostSource,
    PostStatus,
)

_FINAL = 1_000_000
_C = DistributionChannelRow
_P = DistributionPostRow
_M = DistributionPresetRow


def _epoch(at: datetime | None) -> float | None:
    return at.timestamp() if at is not None else None


def _at(epoch: float | None) -> datetime | None:
    return datetime.fromtimestamp(epoch, UTC) if epoch is not None else None


def _channel(row: DistributionChannelRow) -> Channel:
    return Channel(
        id=row.id,
        user_id=row.user_id,
        platform=row.platform,
        account_name=row.account_name,
        paused=row.paused_at is not None,
        error=row.error,
        next_at=_epoch(row.next_at),
        resume_at=_epoch(row.resume_at),
        cookies_expire_at=_epoch(row.cookies_expire_at),
    )


def _post(row: DistributionPostRow) -> Post:
    return Post(
        id=row.id,
        channel_id=row.channel_id,
        file_id=row.file_id,
        name=row.name,
        kind=PostKind(row.kind),
        source=PostSource(row.source),
        preset=row.preset,
        status=PostStatus(row.status),
        attempts=row.attempts,
        job_id=row.job_id,
        remote_id=row.remote_id,
        title=row.title,
        description=row.description,
        link=row.link,
        error=row.error,
        created_at=row.created_at.timestamp(),
        started_at=_epoch(row.started_at),
        published_at=_epoch(row.published_at),
    )


class DistributionRepository:
    def channels_of(self, user_id: int) -> list[Channel]:
        with session_scope() as s:
            rows = s.scalars(select(_C).where(_C.user_id == user_id).order_by(_C.id))
            return [_channel(r) for r in rows]

    def all_channels(self) -> list[Channel]:
        with session_scope() as s:
            rows = s.scalars(select(_C).order_by(_C.user_id, _C.id))
            return [_channel(r) for r in rows]

    def channel(self, channel_id: int) -> Channel | None:
        with session_scope() as s:
            row = s.get(_C, channel_id)
            return _channel(row) if row is not None else None

    def running(self) -> list[Channel]:
        with session_scope() as s:
            rows = s.scalars(
                select(_C)
                .join(UserRow, UserRow.id == _C.user_id)
                .where(
                    _C.paused_at.is_(None),
                    _C.error.is_(None),
                    _C.cookies != "",
                    UserRow.active.is_(True),
                )
            )
            return [_channel(r) for r in rows]

    def cookies(self, channel_id: int) -> str:
        with session_scope() as s:
            return s.scalar(select(_C.cookies).where(_C.id == channel_id)) or ""

    def connect(
        self,
        user_id: int,
        platform: str,
        account_name: str,
        cookies: str,
        expires_at: float | None,
    ) -> Channel:
        values = {
            "cookies": cookies,
            "cookies_expire_at": _at(expires_at),
            "error": None,
        }
        with session_scope() as s:
            channel_id = s.scalar(
                insert(_C)
                .values(
                    user_id=user_id,
                    platform=platform,
                    account_name=account_name,
                    **values,
                )
                .on_conflict_do_update(
                    constraint="uq_distribution_channels_account", set_=values
                )
                .returning(_C.id)
            )
        found = self.channel(int(channel_id or 0))
        if found is None:
            msg = f"distribution account {account_name} vanished"
            raise LookupError(msg)
        return found

    def set_cookies(
        self, channel_id: int, cookies: str, expires_at: float | None
    ) -> None:
        with session_scope() as s:
            s.execute(
                update(_C)
                .where(_C.id == channel_id, _C.error.is_(None))
                .values(cookies=cookies, cookies_expire_at=_at(expires_at))
            )

    def set_error(self, channel_id: int, error: str | None) -> None:
        self._set(channel_id, error=error)

    def set_next(self, channel_id: int, at: float) -> None:
        self._set(channel_id, next_at=_at(at))

    def set_resume(self, channel_id: int, at: float | None) -> None:
        self._set(channel_id, resume_at=_at(at))

    def _set(self, channel_id: int, **values: object) -> None:
        with session_scope() as s:
            s.execute(update(_C).where(_C.id == channel_id).values(**values))

    def presets_of(self, user_id: int) -> list[ModelPreset]:
        with session_scope() as s:
            rows = s.scalars(
                select(_M.data).where(_M.user_id == user_id).order_by(_M.key)
            )
            return [ModelPreset.model_validate(d) for d in rows]

    def preset_of(self, user_id: int, key: str) -> ModelPreset | None:
        with session_scope() as s:
            data = s.scalar(select(_M.data).where(_M.user_id == user_id, _M.key == key))
            return ModelPreset.model_validate(data) if data is not None else None

    def save_preset(self, user_id: int, preset: ModelPreset) -> None:
        data = preset.model_dump(mode="json")
        with session_scope() as s:
            s.execute(
                insert(_M)
                .values(user_id=user_id, key=preset.key, data=data)
                .on_conflict_do_update(
                    constraint="uq_distribution_presets_owner_key", set_={"data": data}
                )
            )

    def model_links(self, user_id: int) -> list[ModelLink]:
        with session_scope() as s:
            rows = s.execute(
                select(_M.key, ShortLinkRow.id, ShortLinkRow.url)
                .join(ShortLinkRow, ShortLinkRow.id == _M.link_id)
                .where(_M.user_id == user_id)
            )
            return [ModelLink(preset=k, link_id=i, url=u) for k, i, u in rows]

    def set_model_link(self, user_id: int, key: str, link_id: int) -> None:
        with session_scope() as s:
            s.execute(
                update(_M)
                .where(_M.user_id == user_id, _M.key == key)
                .values(link_id=link_id)
            )

    def add_posts(self, channel_id: int, found: list[NewPost]) -> int:
        if not found:
            return 0
        rows = [{"channel_id": channel_id, **f.model_dump()} for f in found]
        with session_scope() as s:
            added = s.execute(
                insert(_P)
                .values(rows)
                .on_conflict_do_nothing(constraint="uq_distribution_posts_channel_file")
                .returning(_P.id)
            )
            return len(added.all())

    def post(self, post_id: int) -> Post | None:
        with session_scope() as s:
            row = s.get(_P, post_id)
            return _post(row) if row is not None else None

    def next_post(self, channel: Channel, max_attempts: int) -> Post | None:
        other = aliased(_P)
        mates = select(_C.id).where(_C.user_id == channel.user_id)
        pinned_elsewhere = (
            select(func.count())
            .where(
                other.file_id == _P.file_id,
                other.status.in_([PostStatus.published, PostStatus.publishing]),
                other.channel_id.in_(mates),
            )
            .scalar_subquery()
        )
        retry = and_(_P.status == PostStatus.failed, _P.attempts < max_attempts)
        with session_scope() as s:
            row = s.scalars(
                select(_P)
                .where(
                    _P.channel_id == channel.id,
                    or_(_P.status == PostStatus.queued, retry),
                )
                .order_by(
                    pinned_elsewhere, _P.status != PostStatus.queued, func.random()
                )
                .limit(1)
            ).one_or_none()
            return _post(row) if row is not None else None

    def failing(self, channel_ids: list[int], since_s: float) -> list[Failing]:
        if not channel_ids:
            return []
        since = datetime.now(UTC) - timedelta(seconds=since_s)
        other = aliased(_P)
        went_up = (
            select(other.id)
            .where(
                other.channel_id == _P.channel_id,
                other.status == PostStatus.published,
                other.published_at >= since,
            )
            .exists()
        )
        with session_scope() as s:
            rows = s.execute(
                select(_P.channel_id, _P.error)
                .where(
                    _P.channel_id.in_(channel_ids),
                    _P.status == PostStatus.failed,
                    _P.started_at >= since,
                    ~went_up,
                )
                .distinct(_P.channel_id)
                .order_by(_P.channel_id, _P.started_at.desc())
            )
            return [Failing(channel_id=c, error=e or "") for c, e in rows]

    def last_published(self, channel_ids: list[int]) -> float | None:
        if not channel_ids:
            return None
        with session_scope() as s:
            at = s.scalar(
                select(func.max(_P.published_at)).where(_P.channel_id.in_(channel_ids))
            )
            return _epoch(at)

    def publishing(self, channel_id: int) -> bool:
        with session_scope() as s:
            found = s.scalar(
                select(_P.id)
                .where(_P.channel_id == channel_id, _P.status == PostStatus.publishing)
                .limit(1)
            )
            return found is not None

    def claim(self, post_id: int, title: str, description: str, link: str) -> bool:
        with session_scope() as s:
            took = s.execute(
                update(_P)
                .where(
                    _P.id == post_id,
                    _P.status.in_([PostStatus.queued, PostStatus.failed]),
                )
                .values(
                    status=PostStatus.publishing,
                    attempts=_P.attempts + 1,
                    started_at=func.now(),
                    title=title,
                    description=description,
                    link=link,
                    error=None,
                )
                .returning(_P.id)
            )
            return bool(took.all())

    def set_job(self, post_id: int, job_id: str) -> None:
        with session_scope() as s:
            s.execute(update(_P).where(_P.id == post_id).values(job_id=job_id))

    def published(self, post_id: int, remote_id: str) -> None:
        with session_scope() as s:
            s.execute(
                update(_P)
                .where(_P.id == post_id)
                .values(
                    status=PostStatus.published,
                    remote_id=remote_id,
                    published_at=func.now(),
                    error=None,
                )
            )

    def failed(self, post_id: int, error: str, *, final: bool = False) -> None:
        values: dict[str, object] = {"status": PostStatus.failed, "error": error}
        if final:
            values["attempts"] = _FINAL
        with session_scope() as s:
            s.execute(
                update(_P)
                .where(_P.id == post_id, _P.status == PostStatus.publishing)
                .values(**values)
            )

    def failed_in_a_row(self, channel_id: int, n: int) -> bool:
        with session_scope() as s:
            last = s.scalars(
                select(_P.status)
                .where(_P.channel_id == channel_id, _P.started_at.is_not(None))
                .order_by(_P.started_at.desc())
                .limit(n)
            ).all()
        return len(last) == n and all(x == PostStatus.failed for x in last)

    def requeue(self, post_id: int, error: str) -> None:
        with session_scope() as s:
            s.execute(
                update(_P)
                .where(_P.id == post_id, _P.status == PostStatus.publishing)
                .values(
                    status=PostStatus.queued,
                    attempts=func.greatest(_P.attempts - 1, 0),
                    error=error,
                )
            )

    def cut_off(self, older_than_s: float, error: str, alive: set[str]) -> int:
        before = datetime.now(UTC) - timedelta(seconds=older_than_s)
        with session_scope() as s:
            done = s.execute(
                update(_P)
                .where(
                    _P.status == PostStatus.publishing,
                    _P.started_at < before,
                    or_(_P.job_id.is_(None), _P.job_id.not_in(alive or {""})),
                )
                .values(status=PostStatus.failed, error=error)
                .returning(_P.id)
            )
            return len(done.all())

    def counts(self, channel_id: int) -> PostCounts:
        midnight = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
        found = PostCounts()
        with session_scope() as s:
            rows = s.execute(
                select(
                    _P.status,
                    func.count(),
                    func.count().filter(_P.published_at >= midnight),
                )
                .where(_P.channel_id == channel_id)
                .group_by(_P.status)
            )
            for status, n, today in rows:
                setattr(found, status, n)
                found.today += today
        return found

    def recent(self, channel_ids: list[int], limit: int) -> list[Post]:
        if not channel_ids:
            return []
        with session_scope() as s:
            rows = s.scalars(
                select(_P)
                .where(_P.channel_id.in_(channel_ids), _P.started_at.is_not(None))
                .order_by(_P.started_at.desc(), _P.id.desc())
                .limit(limit)
            )
            return [_post(r) for r in rows]

    def pins_by_day(self, channel_ids: list[int], since: date) -> list[PinDay]:
        if not channel_ids:
            return []
        start = datetime.combine(since, datetime.min.time(), UTC)
        no_link = func.coalesce(_P.link, "") == ""
        found: dict[tuple[int, str, date], PinDay] = {}
        with session_scope() as s:
            for column, status in (
                (_P.published_at, PostStatus.published),
                (_P.started_at, PostStatus.failed),
            ):
                day = func.date(func.timezone("UTC", column))
                rows = s.execute(
                    select(
                        _P.channel_id,
                        _P.preset,
                        day,
                        func.count(),
                        func.count().filter(no_link),
                    )
                    .where(
                        _P.channel_id.in_(channel_ids),
                        _P.status == status,
                        column >= start,
                    )
                    .group_by(_P.channel_id, _P.preset, day)
                )
                for channel_id, preset, on, n, ig in rows:
                    entry = found.setdefault(
                        (channel_id, preset, on),
                        PinDay(channel_id=channel_id, preset=preset, day=on),
                    )
                    if status is PostStatus.published:
                        entry.published, entry.ig = n, ig
                    else:
                        entry.failed = n
        return sorted(found.values(), key=lambda d: (d.day, d.channel_id, d.preset))
