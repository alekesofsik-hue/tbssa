"""
Seed script: populates the database with initial data from .env / Settings.

Usage:
    python -m tbssa.scripts.seed
    tbssa-seed            (after pip install -e .)

Safe to re-run: uses upsert (insert-or-update), no duplicates.
"""
from __future__ import annotations

import asyncio

from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from tbssa.db.engine import AsyncSessionLocal, init_db
from tbssa.db.models import Config, Server, User
from tbssa.settings import Settings, parse_admin_ids


async def _seed() -> None:
    settings = Settings()
    admin_ids = parse_admin_ids(settings.ADMIN_IDS)

    if not admin_ids and not settings.SSH_HOST.strip():
        print("Ошибка: для bootstrap нужны ADMIN_IDS и SSH_HOST в .env.")
        print("После заполнения запустите: tbssa-seed")
        raise SystemExit(1)

    await init_db()

    async with AsyncSessionLocal() as session:
        # --- Users ---
        for tid in admin_ids:
            stmt = (
                sqlite_insert(User)
                .values(telegram_id=tid, username=None, first_name=None, is_active=True)
                .on_conflict_do_update(
                    index_elements=["telegram_id"],
                    set_={"is_active": True},
                )
            )
            await session.execute(stmt)
        print(f"  users:   seeded {len(admin_ids)} admin(s)")

        # --- Server (skip if no SSH_HOST — bootstrap not run) ---
        if not settings.SSH_HOST.strip():
            print("  servers: skipped (SSH_HOST empty)")
        else:
            server_vals = dict(
                name="default",
                ssh_host=settings.SSH_HOST,
                ssh_user=settings.SSH_USER,
                ssh_key_path=settings.SSH_KEY_PATH,
                ssh_known_hosts_path=settings.SSH_KNOWN_HOSTS_PATH,
                ssh_fingerprint=settings.SSH_HOST_KEY_FINGERPRINT or None,
                ssh_connect_timeout=settings.SSH_CONNECT_TIMEOUT,
                ssh_command_timeout=settings.SSH_COMMAND_TIMEOUT,
                ping_host=settings.PING_HOST or settings.SSH_HOST,  # один адрес для SSH и ping
                ping_count=settings.PING_COUNT,
                ping_timeout=settings.PING_TIMEOUT,
                is_active=True,
            )
            stmt = (
                sqlite_insert(Server)
                .values(**server_vals)
                .on_conflict_do_update(
                    index_elements=["name"],
                    set_={k: v for k, v in server_vals.items() if k != "name"},
                )
            )
            await session.execute(stmt)
            print("  servers: seeded 1 server ('default')")

        # --- Config ---
        config_pairs = {
            "CONFIRM_TTL_SECONDS": str(settings.CONFIRM_TTL_SECONDS),
            "PING_COUNT": str(settings.PING_COUNT),
            "PING_TIMEOUT": str(settings.PING_TIMEOUT),
            "SSH_CONNECT_TIMEOUT": str(settings.SSH_CONNECT_TIMEOUT),
            "SSH_COMMAND_TIMEOUT": str(settings.SSH_COMMAND_TIMEOUT),
            "SSH_DEFAULT_USER": settings.SSH_USER,
            "SSH_DEFAULT_KEY_PATH": settings.SSH_KEY_PATH,
        }
        for key, value in config_pairs.items():
            stmt = (
                sqlite_insert(Config)
                .values(key=key, value=value)
                .on_conflict_do_update(
                    index_elements=["key"],
                    set_={"value": value},
                )
            )
            await session.execute(stmt)
        print(f"  config:  seeded {len(config_pairs)} key(s)")

        await session.commit()
    print("Seed complete.")


def main() -> None:
    print("Running tbssa-seed…")
    asyncio.run(_seed())


if __name__ == "__main__":
    main()
