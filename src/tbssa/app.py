from __future__ import annotations

from telegram.ext import ApplicationBuilder, CommandHandler
from telegram.request import HTTPXRequest

from tbssa.handlers import (
    admin_only,
    me,
    reboot_cmd,
    sos_cmd,
    start,
    status_cmd,
)
from tbssa.settings import Settings


def build_app(settings: Settings):
    request = HTTPXRequest(
        connect_timeout=15.0,
        read_timeout=60.0,
        write_timeout=15.0,
        pool_timeout=10.0,
    )
    app = ApplicationBuilder().token(settings.TELEGRAM_BOT_TOKEN).request(request).build()

    # Public
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("me", me))

    # Admin
    admin = admin_only(admin_ids_raw=settings.ADMIN_IDS)

    async def _status(update, context):
        return await status_cmd(
            update=update,
            context=context,
            ping_host=settings.PING_HOST,
            ping_count=settings.PING_COUNT,
            ping_timeout=settings.PING_TIMEOUT,
        )

    async def _reboot(update, context):
        return await reboot_cmd(
            update=update,
            context=context,
            ttl_seconds=settings.CONFIRM_TTL_SECONDS,
            ssh_host=settings.SSH_HOST,
            ssh_user=settings.SSH_USER,
            ssh_key_path=settings.SSH_KEY_PATH,
            ssh_known_hosts_path=settings.SSH_KNOWN_HOSTS_PATH,
            ssh_pinned_fingerprint=settings.SSH_HOST_KEY_FINGERPRINT,
            ssh_connect_timeout=settings.SSH_CONNECT_TIMEOUT,
            ssh_command_timeout=settings.SSH_COMMAND_TIMEOUT,
        )

    async def _sos(update, context):
        return await sos_cmd(
            update=update,
            context=context,
            ttl_seconds=settings.CONFIRM_TTL_SECONDS,
            ssh_host=settings.SSH_HOST,
            ssh_user=settings.SSH_USER,
            ssh_key_path=settings.SSH_KEY_PATH,
            ssh_known_hosts_path=settings.SSH_KNOWN_HOSTS_PATH,
            ssh_pinned_fingerprint=settings.SSH_HOST_KEY_FINGERPRINT,
            ssh_connect_timeout=settings.SSH_CONNECT_TIMEOUT,
            ssh_command_timeout=settings.SSH_COMMAND_TIMEOUT,
        )

    app.add_handler(CommandHandler("status", admin(_status)))
    app.add_handler(CommandHandler("reboot", admin(_reboot)))
    app.add_handler(CommandHandler("sos", admin(_sos)))

    return app

