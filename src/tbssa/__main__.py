from __future__ import annotations

import asyncio
import logging
import time

from telegram.error import NetworkError, TimedOut

from tbssa.app import build_app
from tbssa.config_service import ConfigService
from tbssa.db.engine import init_db
from tbssa.logging_setup import setup_logging
from tbssa.settings import Settings


async def _init() -> ConfigService:
    await init_db()
    svc = ConfigService()
    await svc.load()
    return svc


def main() -> None:
    log = setup_logging()
    settings = Settings()

    # Create a persistent event loop so PTB 20.x can reuse it.
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    config_service = loop.run_until_complete(_init())

    if not config_service.get_servers():
        log.critical("[tbssa] No active servers in DB — run: tbssa-seed")
    if not config_service.is_ready():
        log.critical("[tbssa] ConfigService not ready — admin commands will be denied.")

    log.info("[tbssa] starting polling…")

    backoff = 3
    while True:
        app = build_app(settings, config_service)
        try:
            app.run_polling(close_loop=False)
            backoff = 3
        except (TimedOut, NetworkError) as e:
            log.warning(f"[tbssa] network issue: {e}; retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
        except Exception as e:
            log.exception(f"[tbssa] fatal: {e}")
            raise


if __name__ == "__main__":
    main()
