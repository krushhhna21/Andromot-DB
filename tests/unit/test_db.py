from andromot.db import normalize_database_url


def test_normalize_neon_database_url() -> None:
    url = "postgresql://user:password@host/neondb?sslmode=require"

    assert normalize_database_url(url) == "postgresql+asyncpg://user:password@host/neondb?ssl=require"


def test_keep_async_database_url() -> None:
    url = "postgresql+asyncpg://user:password@host/neondb?ssl=require"

    assert normalize_database_url(url) == url
