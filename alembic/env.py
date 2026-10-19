import asyncio
import os
import sys
from logging.config import fileConfig

from dotenv import load_dotenv
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import context

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import api.v1.models  # noqa: F401  registers every table on Base.metadata
from api.db.database import Base
from api.db.url import build_async_url

load_dotenv()

config = context.config

if config.config_file_name:
    fileConfig(config.config_file_name)

db_url_raw = os.getenv("DB_URL")
if not db_url_raw:
    raise RuntimeError("DB_URL not found in environment.")

db_url, ssl_mode = build_async_url(db_url_raw)
config.set_main_option("sqlalchemy.url", db_url.replace("%", "%%"))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=db_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        # Server defaults are compared too, so `alembic check` catches a model
        # that expects Postgres to fill a column the migration gave no default.
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    kwargs = {"connect_args": {"ssl": ssl_mode}} if ssl_mode else {}
    connectable = create_async_engine(db_url, poolclass=pool.NullPool, **kwargs)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
