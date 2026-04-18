from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from tbssa.db.models import AuditLog


async def log_action(
    session: AsyncSession,
    actor_id: int,
    username: str | None,
    action: str,
    details: str | None = None,
    platform: str = "telegram",
) -> None:
    """Write a single audit record inside the given session (caller commits)."""
    session.add(
        AuditLog(
            actor_id=actor_id,
            platform=platform,
            username=username,
            action=action,
            details=details,
        )
    )
