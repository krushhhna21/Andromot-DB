from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from .config import get_settings

_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def normalize_database_url(url: str) -> str:
    """Adapt standard PostgreSQL/Neon URLs for SQLAlchemy's asyncpg driver."""
    if url.startswith("postgresql://"):
        url = "postgresql+asyncpg://" + url.removeprefix("postgresql://")
    parts = urlsplit(url)
    query = [
        ("ssl" if key == "sslmode" else key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key != "channel_binding"
    ]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    global _engine, _session_factory
    if _session_factory is None:
        settings = get_settings()
        database_url = normalize_database_url(settings.database_url.get_secret_value())
        _engine = create_async_engine(database_url, pool_pre_ping=True)
        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _session_factory


async def get_db_session() -> AsyncIterator[AsyncSession]:
    async with get_session_factory()() as session:
        yield session


async def dispose_engine() -> None:
    if _engine is not None:
        await _engine.dispose()
