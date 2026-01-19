from __future__ import annotations

import time

from telegram.error import NetworkError, TimedOut

from tbssa.app import build_app
from tbssa.logging_setup import setup_logging
from tbssa.settings import Settings


def main() -> None:
    log = setup_logging()
    settings = Settings()

    if not settings.ADMIN_IDS.strip():
        log.critical("[tbssa] ADMIN_IDS is empty: admin commands will be denied (fail-closed).")

    log.info("[tbssa] starting polling…")

    backoff = 3
    app = build_app(settings)
    while True:
        try:
            app.run_polling(close_loop=False)
            backoff = 3
        except (TimedOut, NetworkError) as e:
            log.warning(f"[tbssa] network issue: {e}; retry in {backoff}s")
            time.sleep(backoff)
            backoff = min(backoff * 2, 60)
            app = build_app(settings)
        except Exception as e:
            log.exception(f"[tbssa] fatal: {e}")
            raise


if __name__ == "__main__":
    main()

