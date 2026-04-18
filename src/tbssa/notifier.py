from __future__ import annotations

import logging

from tbssa.config_service import ConfigService
from tbssa.max_api import MaxApiClient
from tbssa.settings import Settings
from tbssa.telegram_api import TelegramApi

log = logging.getLogger("tbssa")


async def notify_admins(
    settings: Settings,
    svc: ConfigService,
    text: str,
    *,
    telegram_parse_mode: str = "HTML",
    max_format: str = "html",
    exclude_telegram_user_id: int | None = None,
    exclude_max_user_id: int | None = None,
) -> tuple[int, int]:
    sent = 0
    failed = 0

    tg_api = TelegramApi(settings.TELEGRAM_BOT_TOKEN)
    for admin_id in svc.get_admin_ids():
        if exclude_telegram_user_id is not None and admin_id == exclude_telegram_user_id:
            continue
        try:
            await tg_api.send_message(admin_id, text, parse_mode=telegram_parse_mode)
            sent += 1
        except Exception as exc:
            failed += 1
            log.warning("[notify] failed to send Telegram message to %s: %s", admin_id, exc)

    if settings.MAX_BOT_TOKEN:
        max_api = MaxApiClient(settings.MAX_BOT_TOKEN, settings.MAX_BASE_URL)
        for admin_id in svc.get_max_admin_ids():
            if exclude_max_user_id is not None and admin_id == exclude_max_user_id:
                continue
            try:
                await max_api.send_message_to_user(admin_id, text, format=max_format)
                sent += 1
            except Exception as exc:
                failed += 1
                log.warning("[notify] failed to send MAX message to %s: %s", admin_id, exc)

    return sent, failed


def actor_display(actor_id: int, actor_username: str | None, actor_platform: str) -> str:
    who = f"@{actor_username}" if actor_username else f"id:{actor_id}"
    return f"[{actor_platform.upper()}] {who}"
