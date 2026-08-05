import asyncio
from logging.config import fileConfig

from sqlalchemy import pool, text
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from app.config import get_migration_settings
from app.db.models import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)
migration_url = get_migration_settings().migration_database_url
# ConfigParser treats percent signs as interpolation markers. Passwords in a
# SQLAlchemy URL must remain URL encoded, so escape only for ConfigParser; its
# read path restores the original single percent signs before engine creation.
config.set_main_option("sqlalchemy.url", migration_url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: object) -> None:
    session_user = connection.scalar(text("SELECT session_user"))
    membership = connection.scalar(
        text("SELECT pg_has_role(session_user, 'rag_owner', 'MEMBER')")
    )
    if session_user != "rag_migrator" or membership is not True:
        raise RuntimeError(
            "Alembic requires the rag_migrator login with rag_owner membership"
        )
    connection.execute(text("SET ROLE rag_owner"))
    context.configure(
        connection=connection, target_metadata=target_metadata, compare_type=True
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.begin() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    if hasattr(asyncio, "SelectorEventLoop"):
        asyncio.run(run_async_migrations(), loop_factory=asyncio.SelectorEventLoop)
    else:
        asyncio.run(run_async_migrations())
