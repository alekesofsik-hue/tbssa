from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tbssa.db.models import AuditLog


async def log_action(
    session: AsyncSession,
    telegram_id: int,
    username: str | None,
    action: str,
    details: str | None = None,
) -> None:
    """Write a single audit record inside the given session (caller commits)."""
    session.add(
        AuditLog(
            telegram_id=telegram_id,
            username=username,
            action=action,
            details=details,
        )
    )
