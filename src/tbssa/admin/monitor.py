from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

from sqlalchemy import select
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from tbssa.config_service import ConfigService, ServerConfig
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import Server
from tbssa.ping import ping_status

log = logging.getLogger("tbssa")

# bot_data key for cooldown state: dict[server_id, last_alert_timestamp]
_COOLDOWN_KEY = "monitor:cooldown"


def _svc(context: ContextTypes.DEFAULT_TYPE) -> ConfigService:
    return context.bot_data["config_service"]


def _cooldown(context: ContextTypes.DEFAULT_TYPE) -> dict[int, float]:
    if _COOLDOWN_KEY not in context.bot_data:
        context.bot_data[_COOLDOWN_KEY] = {}
    return context.bot_data[_COOLDOWN_KEY]


def _valid_ping_template(tpl: str) -> bool:
    return bool(tpl and "{timeout}" in tpl and "{host}" in tpl)


async def _ping_one(server: ServerConfig, cmd_template: str | None) -> bool:
    """Returns True if reachable, False otherwise."""
    try:
        tpl = cmd_template if _valid_ping_template(cmd_template or "") else None
        ok, _ = await asyncio.to_thread(
            ping_status,
            server.ping_host,
            server.ping_count,
            server.ping_timeout,
            tpl,
        )
        return ok > 0
    except Exception:
        return False


async def _alert_admins(
    context: ContextTypes.DEFAULT_TYPE,
    server: ServerConfig,
    is_now_reachable: bool,
) -> None:
    svc = _svc(context)
    icon = "🟢" if is_now_reachable else "🔴"
    state = "снова доступен" if is_now_reachable else "недоступен"
    text = (
        f"{icon} <b>Сервер «{server.name}»</b> {state}!\n"
        f"Хост: <code>{server.ping_host}</code>\n"
        f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )
    for admin_id in svc.get_admin_ids():
        try:
            await context.bot.send_message(admin_id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            log.warning(f"[monitor] failed to notify admin {admin_id}: {e}")


async def check_servers_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Periodic job: ping all active servers, update DB state,
    notify admins on online → offline transition (with cooldown).
    """
    svc = _svc(context)
    if not svc.is_ready():
        return

    servers = svc.get_servers()
    if not servers:
        return

    cooldown_map = _cooldown(context)
    cooldown_secs = svc.get_int("REACHABILITY_ALERT_COOLDOWN_MINUTES", 60) * 60
    ping_template = svc.get_str("PING_CMD_TEMPLATE", "")

    for server in servers:
        reachable = await _ping_one(server, ping_template)
        now = datetime.utcnow()

        async with AsyncSessionLocal() as session:
            db_server = await session.get(Server, server.id)
            if db_server is None:
                continue

            prev_ok = db_server.last_ping_ok  # None = never checked

            db_server.last_ping_ok = reachable
            db_server.last_ping_at = now
            await session.commit()

        # Determine if we should send an alert
        if not reachable:
            # Transition online → offline
            if prev_ok is True:
                last_alert = cooldown_map.get(server.id, 0.0)
                if time.time() - last_alert >= cooldown_secs:
                    await _alert_admins(context, server, is_now_reachable=False)
                    cooldown_map[server.id] = time.time()
                    log.info(f"[monitor] server={server.name} went OFFLINE — admins notified")
        else:
            # Transition offline → online (reset cooldown, notify)
            if prev_ok is False:
                cooldown_map.pop(server.id, None)
                await _alert_admins(context, server, is_now_reachable=True)
                log.info(f"[monitor] server={server.name} is back ONLINE — admins notified")

    # Reload config_service cache to reflect updated last_ping_ok / last_ping_at
    await svc.reload()
