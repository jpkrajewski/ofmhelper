"""phone_revisions: every change to a person's phones leaves their state.

/phones holds what cannot be made again: which account is on which phone,
its password, its backup codes (owner 2026-11-13: "the data must be
protected ... enforced on db level ... go back to any state"). Nine
repository methods, two cascades and the Drive sweep write the three phone
tables (`devices`, `logins`, `device_logins`), so the record is made by the
database, where no code path can skip it.

`phone_revisions`, append-only:
- `id` identity key; the order of a person's revisions.
- `user_id` (FK users.id, ON DELETE RESTRICT): whose phones. Index
  `(user_id, id)`: the History list, newest first.
- `created_at` timestamptz = the transaction's start (`now()`).
- `txid` the transaction that made it. Unique `(user_id, txid,
  created_at)`: every write of one transaction (one action: each
  repository call is one) lands in ONE revision, holding the state after
  the last of them. `created_at` is in the key because transaction ids
  start over in a database restored from a dump.
- `change` what touched it ("devices DELETE, logins DELETE").
- `snapshot` JSONB, read back whole: that person's rows of the three
  tables exactly as stored. Passwords and backup codes are the columns'
  Fernet text, so a snapshot is as secret as the tables and a restore puts
  the same bytes back without the vault key.

Triggers (the first in this schema):
- `phone_revision()` AFTER INSERT/UPDATE/DELETE FOR EACH ROW on the three
  tables: finds the owner, writes their state.
- `phone_revisions_guard()` on `phone_revisions`: an UPDATE only by the
  transaction that made the row (the upsert above), never a DELETE or a
  TRUNCATE. For every client, psql included.

Written by the triggers and by `db/repositories/phone_revisions.py`
(`load`: a state read back from a backup file); read by that repository
(the History list, a restore, the daily backup).

The state every owner has now becomes their first revision ("baseline").

Revision ID: b7c8d9e0f1a3
Revises: a6b7c8d9e0f2
"""

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "b7c8d9e0f1a3"
down_revision = "a6b7c8d9e0f2"
branch_labels = None
depends_on = None

_PHONE_TABLES = ("devices", "logins", "device_logins")

_STATE = """
CREATE FUNCTION phone_state(whose integer) RETURNS jsonb
LANGUAGE sql STABLE AS $$
    SELECT jsonb_build_object(
        'devices', COALESCE((
            SELECT jsonb_agg(to_jsonb(d) ORDER BY d.id)
            FROM devices d WHERE d.user_id = whose), '[]'::jsonb),
        'logins', COALESCE((
            SELECT jsonb_agg(to_jsonb(l) ORDER BY l.id)
            FROM logins l WHERE l.user_id = whose), '[]'::jsonb),
        'device_logins', COALESCE((
            SELECT jsonb_agg(to_jsonb(dl) ORDER BY dl.device_id, dl.login_id)
            FROM device_logins dl
            JOIN devices d ON d.id = dl.device_id
            WHERE d.user_id = whose), '[]'::jsonb)
    )
$$
"""

_RECORD = """
CREATE FUNCTION phone_revision() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    changed jsonb;
    whose integer;
    what text := TG_TABLE_NAME || ' ' || TG_OP;
BEGIN
    IF TG_OP = 'DELETE' THEN
        changed := to_jsonb(OLD);
    ELSE
        changed := to_jsonb(NEW);
    END IF;
    IF TG_TABLE_NAME = 'device_logins' THEN
        -- A link has no owner of its own: its phone's, or (the phone just
        -- deleted, this row going with it) its login's.
        SELECT user_id INTO whose FROM devices
            WHERE id = (changed->>'device_id')::integer;
        IF whose IS NULL THEN
            SELECT user_id INTO whose FROM logins
                WHERE id = (changed->>'login_id')::integer;
        END IF;
    ELSE
        whose := (changed->>'user_id')::integer;
    END IF;
    IF whose IS NULL THEN
        RETURN NULL;
    END IF;
    INSERT INTO phone_revisions (user_id, txid, change, snapshot)
    VALUES (whose, txid_current(), what, phone_state(whose))
    ON CONFLICT ON CONSTRAINT uq_phone_revisions_transaction DO UPDATE
    SET snapshot = EXCLUDED.snapshot,
        change = CASE
            -- Whole entries: "logins UPDATE" is also the end of
            -- "device_logins UPDATE".
            WHEN EXCLUDED.change = ANY (string_to_array(phone_revisions.change, ', '))
                THEN phone_revisions.change
            ELSE phone_revisions.change || ', ' || EXCLUDED.change
        END;
    RETURN NULL;
END
$$
"""

_GUARD = """
CREATE FUNCTION phone_revisions_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'UPDATE' THEN
        IF OLD.txid = txid_current() AND OLD.created_at = now()
            AND NEW.id = OLD.id AND NEW.user_id = OLD.user_id
            AND NEW.txid = OLD.txid AND NEW.created_at = OLD.created_at
        THEN
            RETURN NEW;
        END IF;
    END IF;
    RAISE EXCEPTION 'phone_revisions is append-only (% refused)', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$
"""

_BASELINE = """
INSERT INTO phone_revisions (user_id, txid, change, snapshot)
SELECT o.user_id, txid_current(), 'baseline', phone_state(o.user_id)
FROM (SELECT user_id FROM devices UNION SELECT user_id FROM logins) o
ORDER BY o.user_id
"""


def upgrade() -> None:
    op.create_table(
        "phone_revisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("txid", sa.BigInteger(), nullable=False),
        sa.Column("change", sa.Text(), nullable=False),
        sa.Column("snapshot", JSONB(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "txid", "created_at", name="uq_phone_revisions_transaction"
        ),
    )
    op.create_index(
        "ix_phone_revisions_user_id_id", "phone_revisions", ["user_id", "id"]
    )
    op.execute(_STATE)
    op.execute(_RECORD)
    op.execute(_GUARD)
    op.execute(_BASELINE)
    for table in _PHONE_TABLES:
        op.execute(
            f"CREATE TRIGGER {table}_revision AFTER INSERT OR UPDATE OR DELETE "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION phone_revision()"
        )
    op.execute(
        "CREATE TRIGGER phone_revisions_append_only BEFORE UPDATE OR DELETE "
        "ON phone_revisions FOR EACH ROW EXECUTE FUNCTION phone_revisions_guard()"
    )
    op.execute(
        "CREATE TRIGGER phone_revisions_no_truncate BEFORE TRUNCATE "
        "ON phone_revisions FOR EACH STATEMENT "
        "EXECUTE FUNCTION phone_revisions_guard()"
    )


def downgrade() -> None:
    for table in _PHONE_TABLES:
        op.execute(f"DROP TRIGGER {table}_revision ON {table}")
    op.drop_table("phone_revisions")
    op.execute("DROP FUNCTION phone_revision()")
    op.execute("DROP FUNCTION phone_revisions_guard()")
    op.execute("DROP FUNCTION phone_state(integer)")
