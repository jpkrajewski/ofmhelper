from logging.config import fileConfig

from sqlalchemy import Column, Index, engine_from_config, pool
from sqlalchemy.schema import Constraint, SchemaItem

from alembic import context
from ofmhelpers.config import settings
from ofmhelpers.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

config.set_main_option("sqlalchemy.url", settings.infra.database_url)

target_metadata = Base.metadata

RETIRED_TABLES = frozenset({"drive_day_folders"})


def include_object(obj: SchemaItem, name, type_, _reflected, _compare_to):
    owner = obj.table if isinstance(obj, Column | Index | Constraint) else None
    table = name if type_ == "table" else owner.name if owner is not None else None
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
        connection.exec_driver_sql(
            "SELECT set_config('ofm.via', 'migration', false), "
            "set_config('ofm.ref', 'alembic', false)"
        )
        connection.commit()
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
