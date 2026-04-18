from __future__ import annotations

from telegram.ext import ApplicationBuilder, CommandHandler
from telegram.request import HTTPXRequest

from tbssa.admin.broadcast import get_broadcast_handlers
from tbssa.admin.handlers import get_admin_handlers
from tbssa.admin.journal import get_journal_handlers
from tbssa.admin.monitor import check_servers_job
from tbssa.admin.servers import get_server_handlers
from tbssa.admin.settings import get_settings_handlers
from tbssa.admin.users import get_user_handlers
from tbssa.config_service import ConfigService
from tbssa.error_handlers import telegram_error_handler
from tbssa.handlers import get_server_cmd_handlers, get_start_handlers, my_cmd, sos_cmd, start
from tbssa.settings import Settings


def build_app(settings: Settings, config_service: ConfigService):
    request = HTTPXRequest(
        connect_timeout=15.0,
        read_timeout=60.0,
        write_timeout=15.0,
        pool_timeout=10.0,
    )

    interval_minutes = config_service.get_int("PING_CHECK_INTERVAL_MINUTES", 5)

    async def _post_init(application) -> None:
        application.job_queue.run_repeating(
            check_servers_job,
            interval=interval_minutes * 60,
            first=30,  # first check 30s after bot starts
        )

    app = (
        ApplicationBuilder()
        .token(settings.TELEGRAM_BOT_TOKEN)
        .request(request)
        .post_init(_post_init)
        .build()
    )

    # ConfigService is accessible in all handlers via context.bot_data
    app.bot_data["config_service"] = config_service
    app.bot_data["settings"] = settings

    # ── Public commands ───────────────────────────────────────────────────────
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my", my_cmd))  # узнать свой ID

    # ── /sos command (same as SOS button: global shutdown) ───────────────────
    app.add_handler(CommandHandler("sos", sos_cmd))

    # ── /start SOS button callback ───────────────────────────────────────────
    for handler in get_start_handlers():
        app.add_handler(handler)

    # ── SOS button callbacks (start:sos, confirm, cancel) ────────────────────
    for handler in get_server_cmd_handlers():
        app.add_handler(handler)

    # ── Admin panel: ConversationHandlers first (priority matters) ───────────
    for handler in get_server_handlers():
        app.add_handler(handler)

    for handler in get_user_handlers():
        app.add_handler(handler)

    for handler in get_settings_handlers():
        app.add_handler(handler)

    for handler in get_broadcast_handlers():
        app.add_handler(handler)

    for handler in get_journal_handlers():
        app.add_handler(handler)

    # ── Admin panel: main menu ────────────────────────────────────────────────
    for handler in get_admin_handlers():
        app.add_handler(handler)

    app.add_error_handler(telegram_error_handler)

    return app
