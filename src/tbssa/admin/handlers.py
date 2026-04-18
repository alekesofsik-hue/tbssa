from __future__ import annotations

import time

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from tbssa.admin.audit import log_action
from tbssa.admin.guard import admin_required, _svc
from tbssa.admin.menu import (
    ADM_AUDIT,
    ADM_HOME,
    ADM_SERVERS,
    ADM_SETTINGS,
    ADM_USERS,
    MAIN_MENU_TEXT,
    main_menu_keyboard,
)
from tbssa.admin.journal import show_journal
from tbssa.admin.servers import show_servers_list
from tbssa.admin.settings import show_settings
from tbssa.admin.users import show_users_list, sync_admin_from_telegram
from tbssa.db.engine import AsyncSessionLocal
from tbssa.ui_text import ADMIN_ACCESS_DENIED_TEXT, SESSION_EXPIRED_TEXT

# Session timeout: close menu after 5 minutes of inactivity.
_SESSION_TTL = 5 * 60  # seconds
_SESSION_KEY = "adm:last_active"


def _touch(context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data[_SESSION_KEY] = time.time()


def _session_expired(context: ContextTypes.DEFAULT_TYPE) -> bool:
    last = context.user_data.get(_SESSION_KEY)
    if last is None:
        return False
    return (time.time() - last) > _SESSION_TTL


# ── /admin command ─────────────────────────────────────────────────────────────


@admin_required
async def admin_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _touch(context)
    u = update.effective_user
    if u:
        await sync_admin_from_telegram(u.id, u.username, u.first_name)
    await update.message.reply_text(
        MAIN_MENU_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# ── Callback router ────────────────────────────────────────────────────────────


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    uid = update.effective_user.id if update.effective_user else None
    svc = _svc(context)
    u = update.effective_user
    if u:
        await sync_admin_from_telegram(u.id, u.username, u.first_name)

    # Always re-check access on every callback (fail-closed).
    if not svc.is_ready() or not svc.is_admin(uid):
        await query.edit_message_text(ADMIN_ACCESS_DENIED_TEXT)
        return

    # Session expiry check.
    if _session_expired(context):
        await query.edit_message_text(
            SESSION_EXPIRED_TEXT,
            reply_markup=None,
        )
        context.user_data.pop(_SESSION_KEY, None)
        return

    _touch(context)
    data = query.data

    if data == ADM_HOME:
        await query.edit_message_text(
            MAIN_MENU_TEXT,
            reply_markup=main_menu_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    # Servers section — real implementation (Sprint 2)
    if data == ADM_SERVERS:
        uname = update.effective_user.username if update.effective_user else None
        async with AsyncSessionLocal() as session:
            await log_action(session, uid, uname, "open:admin:servers")
            await session.commit()
        await show_servers_list(update, context)
        return

    # Users section — real implementation (Sprint 3)
    if data == ADM_USERS:
        uname = update.effective_user.username if update.effective_user else None
        async with AsyncSessionLocal() as session:
            await log_action(session, uid, uname, "open:admin:users")
            await session.commit()
        await show_users_list(update, context)
        return

    # Settings section — real implementation (Sprint 4)
    if data == ADM_SETTINGS:
        uname = update.effective_user.username if update.effective_user else None
        async with AsyncSessionLocal() as session:
            await log_action(session, uid, uname, "open:admin:settings")
            await session.commit()
        await show_settings(update, context)
        return

    # Audit log section — real implementation (Sprint 5)
    if data == ADM_AUDIT:
        uname = update.effective_user.username if update.effective_user else None
        async with AsyncSessionLocal() as session:
            await log_action(session, uid, uname, "open:admin:audit")
            await session.commit()
        await show_journal(update, context)
        return

    # Unknown callback — refresh main menu.
    await query.edit_message_text(
        MAIN_MENU_TEXT,
        reply_markup=main_menu_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# ── Handler list for app.py ────────────────────────────────────────────────────


def get_admin_handlers() -> list:
    return [
        CommandHandler("admin", admin_cmd),
        CallbackQueryHandler(admin_callback, pattern=r"^adm:(?!srv:)"),
    ]
