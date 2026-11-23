# Copyright (C) 2026 jpkrajewski

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision = "a1b2c3d4e5f8"
down_revision = "f1a2b3c4d5e7"
branch_labels = None
depends_on = None


class _Table:
    def __init__(  # noqa: PLR0913
        self,
        name: str,
        *,
        keys: str,
        owner: str,
        rename: dict[str, str] | None = None,
        convert: tuple[str, ...] = (),
        add: tuple[str, str] | None = None,
        quiet: str = "",
        masked: str = "",
    ) -> None:
        self.name, self.keys, self.owner = name, keys, owner
        self.rename = rename or {}
        self.convert, self.add = convert, add
        self.quiet, self.masked = quiet, masked


_TABLES = (
    _Table(
        "users",
        keys="id",
        owner="id",
        convert=("created_at",),
        add=("updated_at", "created_at"),
        quiet="last_seen_at,last_login_at",
        masked="password_hash,google_refresh_token",
    ),
    _Table(
        "user_identities",
        keys="provider,subject",
        owner="user_id",
        rename={"linked_at": "created_at"},
        convert=("created_at",),
        add=("updated_at", "created_at"),
    ),
    _Table(
        "user_api_keys",
        keys="user_id,provider",
        owner="user_id",
        convert=("updated_at",),
        add=("created_at", "updated_at"),
        masked="api_key",
    ),
    _Table(
        "user_cookies",
        keys="user_id",
        owner="user_id",
        rename={"uploaded_at": "updated_at"},
        convert=("updated_at",),
        add=("created_at", "updated_at"),
        masked="cookies",
    ),
    _Table(
        "devices",
        keys="id",
        owner="user_id",
        rename={"added_at": "created_at"},
        convert=("created_at", "updated_at"),
    ),
    _Table(
        "logins",
        keys="id",
        owner="user_id",
        rename={"added_at": "created_at"},
        convert=("created_at", "updated_at"),
        masked="password,backup_codes",
    ),
    _Table(
        "device_logins",
        keys="device_id,login_id",
        owner="devices.device_id|logins.login_id",
        rename={"linked_at": "created_at"},
        convert=("created_at",),
        add=("updated_at", "created_at"),
    ),
    _Table(
        "tracked_accounts",
        keys="id",
        owner="user_id",
        rename={"added_at": "created_at"},
        convert=("created_at",),
        add=("updated_at", "created_at"),
        quiet=(
            "scanned_at,claimed_at,platform_account_id,failed_scans,error,"
            "profile,metrics,covered_since"
        ),
    ),
    _Table(
        "creators",
        keys="id",
        owner="user_id",
        convert=("created_at",),
        add=("updated_at", "created_at"),
    ),
    _Table(
        "watched_profile_groups",
        keys="user_id,name",
        owner="user_id",
        convert=("created_at",),
        add=("updated_at", "created_at"),
    ),
    _Table(
        "short_links",
        keys="id",
        owner="user_id",
        convert=("created_at", "updated_at"),
    ),
    _Table(
        "picture_presets",
        keys="id",
        owner="user_id",
        convert=("created_at", "updated_at"),
    ),
    _Table(
        "self_link_domains",
        keys="id",
        owner="owner_id",
        rename={"added_at": "created_at"},
        convert=("created_at",),
        add=("updated_at", "created_at"),
    ),
    _Table(
        "clone_examples",
        keys="id",
        owner="",
        add=("updated_at", "created_at"),
    ),
)

_PHONE_TRIGGERS = {
    "devices": "devices_revision",
    "logins": "logins_revision",
    "device_logins": "device_logins_revision",
}

_SETTING = """
CREATE FUNCTION audit_setting(name text) RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT nullif(current_setting('ofm.' || name, true), '')
$$
"""

_STAMP = """
CREATE FUNCTION stamp_row() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    quiet text[] := string_to_array(TG_ARGV[0], ',')
        || ARRAY['created_at', 'updated_at', 'created_by', 'updated_by'];
    actor integer := audit_setting('actor_id')::integer;
BEGIN
    IF TG_OP = 'INSERT' THEN
        NEW.created_by := coalesce(NEW.created_by, actor);
        NEW.updated_by := coalesce(NEW.updated_by, actor);
        RETURN NEW;
    END IF;
    -- A write that sets `updated_at` itself touches the row on purpose (a
    -- phone whose login changed).
    IF (to_jsonb(NEW) - quiet) IS DISTINCT FROM (to_jsonb(OLD) - quiet)
        OR NEW.updated_at IS DISTINCT FROM OLD.updated_at THEN
        NEW.updated_at := now();
        NEW.updated_by := actor;
    ELSE
        NEW.updated_at := OLD.updated_at;
        NEW.updated_by := OLD.updated_by;
    END IF;
    NEW.created_at := OLD.created_at;
    NEW.created_by := OLD.created_by;
    RETURN NEW;
END
$$
"""

_AUDIT = """
CREATE FUNCTION audit_row() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
    quiet text[] := string_to_array(TG_ARGV[0], ',')
        || ARRAY['created_at', 'updated_at', 'created_by', 'updated_by'];
    secret text[] := string_to_array(TG_ARGV[1], ',');
    keys text[] := string_to_array(TG_ARGV[2], ',');
    owners text[] := string_to_array(TG_ARGV[3], '|');
    old_row jsonb;
    new_row jsonb;
    row_data jsonb;
    diff jsonb := '{}'::jsonb;
    col text;
    via_owner text;
    whose integer;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        old_row := to_jsonb(OLD);
    END IF;
    IF TG_OP <> 'DELETE' THEN
        new_row := to_jsonb(NEW);
    END IF;
    row_data := coalesce(new_row, old_row);
    IF TG_OP = 'UPDATE' THEN
        FOR col IN SELECT jsonb_object_keys(new_row) LOOP
            CONTINUE WHEN col = ANY (quiet);
            CONTINUE WHEN (new_row -> col) IS NOT DISTINCT FROM (old_row -> col);
            IF col = ANY (secret) THEN
                diff := diff || jsonb_build_object(col, 'changed');
            ELSE
                diff := diff || jsonb_build_object(
                    col, jsonb_build_array(old_row -> col, new_row -> col));
            END IF;
        END LOOP;
        IF diff = '{}'::jsonb THEN
            RETURN NULL;
        END IF;
    ELSE
        diff := row_data;
        FOREACH col IN ARRAY secret LOOP
            IF jsonb_typeof(diff -> col) IS DISTINCT FROM 'null'
                AND diff ? col THEN
                diff := jsonb_set(diff, ARRAY[col], '"set"'::jsonb);
            END IF;
        END LOOP;
    END IF;
    -- Whose row: its owner column, or "table.column" -- that row's owner
    -- (a phone link: its phone's, else, the phone going with it, its
    -- login's).
    FOREACH via_owner IN ARRAY owners LOOP
        EXIT WHEN whose IS NOT NULL;
        IF position('.' IN via_owner) = 0 THEN
            whose := (row_data ->> via_owner)::integer;
        ELSE
            EXECUTE format(
                'SELECT user_id FROM %I WHERE id = $1',
                split_part(via_owner, '.', 1))
            INTO whose
            USING (row_data ->> split_part(via_owner, '.', 2))::integer;
        END IF;
    END LOOP;
    INSERT INTO audit_log (
        table_name, row_id, op, owner_id, actor_id, viewing_id,
        via, ref, trace_id, changes)
    VALUES (
        TG_TABLE_NAME,
        array_to_string(ARRAY(
            SELECT row_data ->> k
            FROM unnest(keys) WITH ORDINALITY AS u(k, n) ORDER BY n), ':'),
        TG_OP,
        whose,
        audit_setting('actor_id')::integer,
        audit_setting('viewing_id')::integer,
        coalesce(audit_setting('via'), 'sql:' || current_setting('application_name')),
        coalesce(audit_setting('ref'), ''),
        audit_setting('trace_id'),
        diff);
    RETURN NULL;
END
$$
"""

_GUARD = """
CREATE FUNCTION audit_log_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'audit_log is append-only (% refused)', TG_OP
        USING ERRCODE = 'restrict_violation';
END
$$
"""

_PHONE_ROW = """
CREATE FUNCTION phone_row_current(r jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN r ? 'added_at' OR r ? 'linked_at' THEN
            (r - 'added_at' - 'linked_at' - 'updated_at')
            || jsonb_build_object(
                'created_at', to_jsonb(to_timestamp(
                    coalesce(r ->> 'added_at', r ->> 'linked_at')::double precision)),
                'updated_at', to_jsonb(to_timestamp(coalesce(
                    r ->> 'updated_at', r ->> 'added_at', r ->> 'linked_at'
                )::double precision)))
        ELSE r
    END
$$
"""

_PHONE_SNAPSHOT = """
CREATE FUNCTION phone_snapshot_current(s jsonb) RETURNS jsonb
LANGUAGE sql IMMUTABLE AS $$
    SELECT jsonb_object_agg(part, coalesce((
        SELECT jsonb_agg(phone_row_current(e.value) ORDER BY e.n)
        FROM jsonb_array_elements(s -> part) WITH ORDINALITY AS e(value, n)
    ), '[]'::jsonb))
    FROM unnest(ARRAY['devices', 'logins', 'device_logins']) AS part
$$
"""


def _convert(t: _Table) -> None:
    for old, new in t.rename.items():
        op.alter_column(t.name, old, new_column_name=new)
    for column in t.convert:
        op.execute(
            f"ALTER TABLE {t.name} ALTER COLUMN {column} TYPE timestamptz "
            f"USING to_timestamp({column}), "
            f"ALTER COLUMN {column} SET DEFAULT now()",
        )
    if t.add is not None:
        column, source = t.add
        op.add_column(t.name, sa.Column(column, sa.DateTime(timezone=True)))
        trigger = _PHONE_TRIGGERS.get(t.name)
        if trigger:
            op.execute(f"ALTER TABLE {t.name} DISABLE TRIGGER {trigger}")
        op.execute(f"UPDATE {t.name} SET {column} = {source}")  # noqa: S608
        if trigger:
            op.execute(f"ALTER TABLE {t.name} ENABLE TRIGGER {trigger}")
        op.alter_column(t.name, column, nullable=False, server_default=sa.func.now())
    for column in ("created_by", "updated_by"):
        op.add_column(
            t.name,
            sa.Column(
                column,
                sa.Integer(),
                sa.ForeignKey("users.id", ondelete="SET NULL"),
                nullable=True,
            ),
        )


def _triggers(t: _Table) -> None:
    op.execute(
        f"CREATE TRIGGER {t.name}_stamp BEFORE INSERT OR UPDATE ON {t.name} "
        f"FOR EACH ROW EXECUTE FUNCTION stamp_row('{t.quiet}')",
    )
    op.execute(
        f"CREATE TRIGGER {t.name}_audit AFTER INSERT OR UPDATE OR DELETE "
        f"ON {t.name} FOR EACH ROW EXECUTE FUNCTION "
        f"audit_row('{t.quiet}', '{t.masked}', '{t.keys}', '{t.owner}')",
    )


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("table_name", sa.Text(), nullable=False),
        sa.Column("row_id", sa.Text(), nullable=False),
        sa.Column("op", sa.Text(), nullable=False),
        sa.Column("owner_id", sa.Integer(), nullable=True),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("viewing_id", sa.Integer(), nullable=True),
        sa.Column("via", sa.Text(), nullable=False),
        sa.Column("ref", sa.Text(), nullable=False, server_default=""),
        sa.Column("trace_id", sa.Text(), nullable=True),
        sa.Column(
            "txid",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("txid_current()"),
        ),
        sa.Column("changes", JSONB(), nullable=False),
        sa.CheckConstraint(
            "op IN ('INSERT', 'UPDATE', 'DELETE')",
            name="ck_audit_log_op",
        ),
    )
    op.create_index(
        "ix_audit_log_table_name_row_id_id",
        "audit_log",
        ["table_name", "row_id", "id"],
    )
    op.create_index("ix_audit_log_actor_id_id", "audit_log", ["actor_id", "id"])
    op.create_index("ix_audit_log_owner_id_id", "audit_log", ["owner_id", "id"])
    for t in _TABLES:
        _convert(t)
    for sql in (_SETTING, _STAMP, _AUDIT, _GUARD, _PHONE_ROW, _PHONE_SNAPSHOT):
        op.execute(sql)
    for t in _TABLES:
        _triggers(t)
    op.execute(
        "CREATE TRIGGER jobs_audit AFTER UPDATE OF hidden_at ON jobs FOR EACH ROW "
        "WHEN (OLD.hidden_at IS DISTINCT FROM NEW.hidden_at) "
        "EXECUTE FUNCTION audit_row('', '', 'id', 'user_id')",
    )
    op.execute(
        "CREATE TRIGGER audit_log_append_only BEFORE UPDATE OR DELETE "
        "ON audit_log FOR EACH ROW EXECUTE FUNCTION audit_log_guard()",
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_guard()",
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER jobs_audit ON jobs")
    for t in _TABLES:
        op.execute(f"DROP TRIGGER {t.name}_audit ON {t.name}")
        op.execute(f"DROP TRIGGER {t.name}_stamp ON {t.name}")
    op.drop_table("audit_log")
    for t in reversed(_TABLES):
        for column in ("updated_by", "created_by"):
            op.drop_column(t.name, column)
        if t.add is not None:
            op.drop_column(t.name, t.add[0])
        for column in t.convert:
            op.execute(
                f"ALTER TABLE {t.name} ALTER COLUMN {column} DROP DEFAULT, "
                f"ALTER COLUMN {column} TYPE double precision "
                f"USING extract(epoch FROM {column})",
            )
        for old, new in t.rename.items():
            op.alter_column(t.name, new, new_column_name=old)
    for name in (
        "phone_snapshot_current(jsonb)",
        "phone_row_current(jsonb)",
        "audit_log_guard()",
        "audit_row()",
        "stamp_row()",
        "audit_setting(text)",
    ):
        op.execute(f"DROP FUNCTION {name}")
