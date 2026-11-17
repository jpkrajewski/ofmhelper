"""user_event_targets: a person's own place for their events (their Telegram).

Owner 2026-11-15 (backlog: "is it easy for new users to connect their
telegram bot?"): one global bot sent every owner's VA notices to the same
agency chats, set by hand in the server's .env. Now each person links their
own chat from /account (the bot's /start link) and their events go there.

`user_event_targets`, one row per person and sender:
- `user_id` FK users.id ON DELETE CASCADE (a person removed takes where
  their messages went), `sender` ("telegram", a sender's name in
  `integrations/senders`): the key. Generic on purpose: a second sender is
  a row, not a table.
- `target` text: where the message goes (a Telegram chat id). Never logged;
  masked in `audit_log` like a secret.
- `created_at` / `updated_at` / `created_by` / `updated_by`: the row stamps
  of a1b2c3d4e5f8, audited (`<table>_stamp`, `<table>_audit`).

Written by `db/repositories/event_targets.py` (the bot's /start, /account's
Disconnect); read by the event dispatch (`features/events/dispatch.py`,
which sends an event with `owner_id` to its owner's target) and /account.
Starts empty: until someone links, nothing changes (an admin's events still
go to the agency's chats).

Revision ID: b2c3d4e5f6a9
Revises: a1b2c3d4e5f8
"""

import sqlalchemy as sa

from alembic import op

revision = "b2c3d4e5f6a9"
down_revision = "a1b2c3d4e5f8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_event_targets",
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("sender", sa.Text(), primary_key=True),
        sa.Column("target", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "created_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "updated_by",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.execute(
        "CREATE TRIGGER user_event_targets_stamp BEFORE INSERT OR UPDATE "
        "ON user_event_targets FOR EACH ROW EXECUTE FUNCTION stamp_row('')"
    )
    op.execute(
        "CREATE TRIGGER user_event_targets_audit AFTER INSERT OR UPDATE OR DELETE "
        "ON user_event_targets FOR EACH ROW EXECUTE FUNCTION "
        "audit_row('', 'target', 'user_id,sender', 'user_id')"
    )


def downgrade() -> None:
    op.drop_table("user_event_targets")
