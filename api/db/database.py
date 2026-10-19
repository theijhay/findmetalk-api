from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from api.db.url import build_async_url
from api.utils.settings import settings

_DB_URL, _SSL_MODE = build_async_url(settings.DB_URL)


def get_db_engine() -> AsyncEngine:
    kwargs = {"connect_args": {"ssl": _SSL_MODE}} if _SSL_MODE else {}
    return create_async_engine(
        _DB_URL,
        # Keep connection use well under the DB's max_connections.
        pool_size=settings.DB_POOL_SIZE,
        max_overflow=settings.DB_MAX_OVERFLOW,
        pool_timeout=settings.DB_POOL_TIMEOUT,
        pool_recycle=settings.DB_POOL_RECYCLE,  # drop idle conns before the server does
        pool_pre_ping=True,  # avoid handing out dead connections
        **kwargs,
    )


# Creating the engine opens no connection; the first query does.
engine = get_db_engine()

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session
