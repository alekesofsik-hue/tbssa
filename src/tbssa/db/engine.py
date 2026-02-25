from __future__ import annotations

import os

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tbssa.db.models import Base

# DB file lives next to the project root: data/tbssa.db
# Override with DB_URL env var if needed (e.g. for PostgreSQL).
_DEFAULT_DB_PATH = os.path.join(os.path.dirname(__file__), "../../../data/tbssa.db")
_DB_URL = os.environ.get(
    "DB_URL",
    f"sqlite+aiosqlite:///{os.path.abspath(_DEFAULT_DB_PATH)}",
)

engine = create_async_engine(_DB_URL, echo=False)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)


async def init_db() -> None:
    """Create all tables if they don't exist (used on first run / dev)."""
    os.makedirs(os.path.dirname(os.path.abspath(_DEFAULT_DB_PATH)), exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
