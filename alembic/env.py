"""
Alembic migration environment. Pulls the DB URL from the app's own config
(settings.infra.database_url) rather than alembic.ini, and targets the ORM's
Base.metadata so `alembic revision --autogenerate` sees the models.
"""

from __future__ import annotations

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context
from ofmhelpers.config import settings
from ofmhelpers.web.db.models import Base

config = context.config

if config.config_file_name is not None:
    # Not disable_existing_loggers=True (fileConfig's default): run in-process
    # (alembic.command, the migration tests) it would silence every logger
    # the app already made.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# One source of truth for the connection string (see alembic.ini comment).
config.set_main_option("sqlalchemy.url", settings.infra.database_url)

target_metadata = Base.metadata

# Tables whose feature was removed but whose data is kept on purpose. They
# are no longer in Base.metadata, so without this autogenerate would emit a
# DROP for each of them.
RETIRED_TABLES = frozenset(
    {
        "todos",
        "approval_tokens",
        "models",
        "instagram_accounts",
        "model_contacts",
        "competitor_profiles",
        "instagram_stats",
        # /drive's day folders, replaced by one POSTED folder per account
        # (2026-10-21). Kept: a rollback to the old code still finds them.
        "drive_day_folders",
    }
)


def include_object(obj, name, type_, _reflected, _compare_to):
    table = (
        name if type_ == "table" else getattr(getattr(obj, "table", None), "name", None)
    )
    return table not in RETIRED_TABLES


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        include_object=include_object,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_object=include_object,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
