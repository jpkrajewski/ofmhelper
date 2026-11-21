from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB

from ofmhelpers.db.cached_repository import CachedRepository, invalidates_cache
from ofmhelpers.db.models import Base
from ofmhelpers.db.phone_revisions.schema import PhoneRevision, PhoneState, Restored
from ofmhelpers.db.session import session_scope

_SNAPSHOT = "(SELECT snapshot FROM phone_revisions WHERE id = :revision)"

_LIST = text(
    """
    SELECT id, created_at, change,
           jsonb_array_length(snapshot->'devices') AS phones,
           jsonb_array_length(snapshot->'logins') AS accounts
    FROM phone_revisions
    WHERE user_id = :owner
    ORDER BY created_at DESC, id DESC
    LIMIT :limit
    """
)

_OWNS = text("SELECT 1 FROM phone_revisions WHERE id = :revision AND user_id = :owner")

_BACK = text(
    """
    SELECT x.id
    FROM jsonb_to_recordset(
        (SELECT snapshot FROM phone_revisions WHERE id = :revision)->'logins'
    ) AS x(id integer)
    WHERE NOT EXISTS (SELECT 1 FROM logins l WHERE l.id = x.id)
    ORDER BY x.id
    """
)

_DROP_ADDED = (
    text(
        """
        DELETE FROM device_logins dl USING devices d
        WHERE d.id = dl.device_id AND d.user_id = :owner AND NOT EXISTS (
            SELECT 1 FROM jsonb_to_recordset(
                (SELECT snapshot FROM phone_revisions WHERE id = :revision)
                    ->'device_logins'
            ) AS x(device_id integer, login_id integer)
            WHERE x.device_id = dl.device_id AND x.login_id = dl.login_id)
        """
    ),
    text(
        """
        DELETE FROM devices d WHERE d.user_id = :owner AND NOT EXISTS (
            SELECT 1 FROM jsonb_to_recordset(
                (SELECT snapshot FROM phone_revisions WHERE id = :revision)->'devices'
            ) AS x(id integer)
            WHERE x.id = d.id)
        """
    ),
    text(
        """
        DELETE FROM logins l WHERE l.user_id = :owner AND NOT EXISTS (
            SELECT 1 FROM jsonb_to_recordset(
                (SELECT snapshot FROM phone_revisions WHERE id = :revision)->'logins'
            ) AS x(id integer)
            WHERE x.id = l.id)
        """
    ),
    text("UPDATE devices SET label = '~' || id WHERE user_id = :owner"),
    text("UPDATE logins SET username = '~' || id WHERE user_id = :owner"),
)

_HISTORY = text(
    """
    SELECT id AS revision, user_id, created_at AS taken_at, change, snapshot
    FROM phone_revisions
    ORDER BY id
    """
)

_NEWEST = text("SELECT max(id) FROM phone_revisions")

_LOAD = text(
    """
    INSERT INTO phone_revisions (user_id, created_at, txid, change, snapshot)
    SELECT :owner, :taken_at, txid_current(), :change, :snapshot
    WHERE NOT EXISTS (
        SELECT 1 FROM phone_revisions
        WHERE user_id = :owner AND created_at = :taken_at AND change = :change)
    RETURNING id
    """
).bindparams(bindparam("snapshot", type_=JSONB))


def _put_back(name: str, owned: str) -> str:
    table = Base.metadata.tables[name]
    columns = [c.name for c in table.columns]
    keys = [c.name for c in table.primary_key.columns]
    changed = ", ".join(f"{c} = EXCLUDED.{c}" for c in columns if c not in keys)
    return f"""
        INSERT INTO {name} ({", ".join(columns)})
        SELECT {", ".join(f"r.{c}" for c in columns)}
        FROM jsonb_populate_recordset(
            NULL::{name}, phone_snapshot_current({_SNAPSHOT})->'{name}'
        ) r
        WHERE {owned}
        ON CONFLICT ({", ".join(keys)}) DO UPDATE SET {changed}
        """  # noqa: S608


_OWNED_LINK = (
    "EXISTS (SELECT 1 FROM devices d WHERE d.id = r.device_id AND d.user_id = :owner)"
)


_PUT_BACK = (
    ("logins", "r.user_id = :owner"),
    ("devices", "r.user_id = :owner"),
    ("device_logins", _OWNED_LINK),
)


class PhoneRevisionRepository(CachedRepository):
    cache_namespace = "device"

    def page(self, owner: int, limit: int) -> list[PhoneRevision]:
        with session_scope() as s:
            rows = s.execute(_LIST, {"owner": owner, "limit": limit}).mappings()
            return [PhoneRevision.model_validate(dict(r)) for r in rows]

    @invalidates_cache
    def restore(self, revision_id: int, owner: int) -> Restored | None:
        asked = {"revision": revision_id, "owner": owner}
        with session_scope() as s:
            if s.execute(_OWNS, asked).first() is None:
                return None
            back = [row.id for row in s.execute(_BACK, {"revision": revision_id})]
            for statement in _DROP_ADDED:
                s.execute(statement, asked)
            for name, owned in _PUT_BACK:
                s.execute(text(_put_back(name, owned)), asked)
            return Restored(revision_id=revision_id, logins_back=back)

    def history(self) -> list[PhoneState]:
        with session_scope() as s:
            rows = s.execute(_HISTORY).mappings()
            return [PhoneState.model_validate(dict(r)) for r in rows]

    def newest_id(self) -> int:
        with session_scope() as s:
            return s.execute(_NEWEST).scalar_one() or 0

    def load(self, state: PhoneState) -> int | None:
        with session_scope() as s:
            row = s.execute(
                _LOAD,
                {
                    "owner": state.user_id,
                    "taken_at": state.taken_at,
                    "change": state.change,
                    "snapshot": state.snapshot,
                },
            ).first()
            return row.id if row is not None else None
