from __future__ import annotations

import asyncio
import time
from functools import wraps
from typing import Awaitable, Callable, TypeVar

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes

from tbssa.config_service import ConfigService, ServerConfig
from tbssa.notifier import notify_admins
from tbssa.shared_actions import execute_sos_all, guest_start_text, sos_progress_text
from tbssa.ssh import ps, ssh_exec
from tbssa.ui_text import (
    ADMIN_ACCESS_DENIED_TEXT,
    ADMIN_QUICK_ACTIONS_TEXT,
    BOT_INITIALIZING_TEXT,
    BUTTON_CANCEL,
    BUTTON_CONFIRM,
    my_id_text,
    sos_confirm_text,
)

T = TypeVar("T")

# ── Helpers ───────────────────────────────────────────────────────────────────


def _svc(context: ContextTypes.DEFAULT_TYPE) -> ConfigService:
    return context.bot_data["config_service"]


# ── Guard decorator ───────────────────────────────────────────────────────────


def admin_required(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Deny access if user is not in config_service admin list (fail-closed)."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> T | None:
        uid = update.effective_user.id if update.effective_user else None
        svc = _svc(context)

        if not svc.is_ready():
            await update.effective_chat.send_message(
                BOT_INITIALIZING_TEXT
            )
            return None

        if not svc.is_admin(uid):
            await update.effective_chat.send_message(ADMIN_ACCESS_DENIED_TEXT)
            return None

        return await func(update, context)

    return wrapper


# ── /start ─────────────────────────────────────────────────────────────────────


def _sos_label(svc: ConfigService) -> str:
    return svc.get_str("SOS_BUTTON_LABEL", "SOS")


def _admin_start_keyboard(svc: ConfigService) -> InlineKeyboardMarkup:
    label = _sos_label(svc)
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(label, callback_data="start:sos")],
    ])


def _sos_confirm_keyboard(svc: ConfigService) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(BUTTON_CONFIRM, callback_data="start:sos:confirm"),
            InlineKeyboardButton(BUTTON_CANCEL, callback_data="start:sos:cancel"),
        ]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    uid = update.effective_user.id if update.effective_user else None
    svc = _svc(context)

    if svc.is_ready() and svc.is_admin(uid):
        from tbssa.admin.users import sync_admin_from_telegram
        u = update.effective_user
        if u:
            await sync_admin_from_telegram(uid, u.username, u.first_name)
        await update.message.reply_text(
            ADMIN_QUICK_ACTIONS_TEXT,
            reply_markup=_admin_start_keyboard(svc),
        )
    else:
        await update.message.reply_text(guest_start_text())


async def my_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /my — показать свой ID незарегистрированному пользователю."""
    uid = update.effective_user.id if update.effective_user else 0
    await update.message.reply_text(
        my_id_text(uid),
        parse_mode=ParseMode.HTML,
    )


# ── SOS execution helper ──────────────────────────────────────────────────────


def _get_sos_edit_fn(update: Update, context: ContextTypes.DEFAULT_TYPE, svc: ConfigService):
    """Return an async function to show progress/result; works with callback_query or message."""
    if update.callback_query:
        async def edit(text: str, **kwargs) -> None:
            await update.callback_query.edit_message_text(text, **kwargs)
        return edit
    # From message (command or "sos"/"сос" text)
    sent = []

    async def edit(text: str, **kwargs) -> None:
        if not sent:
            msg = await update.effective_chat.send_message(text, **kwargs)
            sent.append(msg)
        else:
            await sent[0].edit_text(text, **kwargs)

    return edit


async def _exec_sos_all(
    update: Update,
    uid: int,
    uname: str | None,
    svc: ConfigService,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Shut down all active servers and notify all admins."""
    edit_fn = _get_sos_edit_fn(update, context, svc)

    servers = svc.get_servers()
    if not servers:
        await edit_fn(
            "⚠️ Нет активных серверов.",
            reply_markup=_admin_start_keyboard(svc),
        )
        return

    await edit_fn(
        sos_progress_text(servers),
        parse_mode=ParseMode.HTML,
    )

    report = await execute_sos_all(svc, uid, uname, actor_platform="telegram")

    await edit_fn(
        report,
        reply_markup=_admin_start_keyboard(svc),
        parse_mode=ParseMode.HTML,
    )

    settings = context.bot_data["settings"]
    await notify_admins(settings, svc, report, exclude_telegram_user_id=uid)


# ── SOS-all callback (from /start button) ─────────────────────────────────────


async def sos_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """SOS button pressed: either ask for confirmation or execute immediately."""
    query = update.callback_query
    await query.answer()

    uid = update.effective_user.id if update.effective_user else 0
    svc = _svc(context)

    if not svc.is_ready() or not svc.is_admin(uid):
        await query.edit_message_text(ADMIN_ACCESS_DENIED_TEXT)
        return

    if svc.get_int("SOS_REQUIRE_CONFIRM", 0):
        label = _sos_label(svc)
        await query.edit_message_text(
            sos_confirm_text(label),
            reply_markup=_sos_confirm_keyboard(svc),
            parse_mode=ParseMode.HTML,
        )
        return

    uname = update.effective_user.username if update.effective_user else None
    await _exec_sos_all(update, uid, uname, svc, context)


async def sos_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmation button 'Подтвердить' — execute SOS."""
    query = update.callback_query
    await query.answer("🆘 SOS активирован!", show_alert=False)

    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None
    svc = _svc(context)

    if not svc.is_ready() or not svc.is_admin(uid):
        await query.edit_message_text(ADMIN_ACCESS_DENIED_TEXT)
        return

    await _exec_sos_all(update, uid, uname, svc, context)


async def sos_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmation button 'Отмена' — return to start menu."""
    query = update.callback_query
    await query.answer("Отменено.")

    uid = update.effective_user.id if update.effective_user else 0
    svc = _svc(context)

    if not svc.is_ready() or not svc.is_admin(uid):
        await query.edit_message_text(ADMIN_ACCESS_DENIED_TEXT)
        return

    await query.edit_message_text(
        ADMIN_QUICK_ACTIONS_TEXT,
        reply_markup=_admin_start_keyboard(svc),
    )


# ── /sos command (same as SOS button: global shutdown of all servers) ─────────


async def _trigger_sos_from_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Trigger SOS from message (command /sos or text "sos"/"сос").
    Same logic as SOS button: shutdown all active servers, with optional confirmation.
    """
    svc = _svc(context)
    servers = svc.get_servers()
    if not servers:
        await update.message.reply_text("⚠️ Нет активных серверов.")
        return

    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None

    if svc.get_int("SOS_REQUIRE_CONFIRM", 0):
        label = _sos_label(svc)
        await update.message.reply_text(
            sos_confirm_text(label),
            reply_markup=_sos_confirm_keyboard(svc),
            parse_mode=ParseMode.HTML,
        )
        return

    await _exec_sos_all(update, uid, uname, svc, context)


@admin_required
async def sos_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Command /sos — same as SOS button: global shutdown of all active servers."""
    await _trigger_sos_from_message(update, context)


def get_server_cmd_handlers() -> list:
    """No longer used: SOS is global, no server picker."""
    return []


def get_start_handlers() -> list:
    return [
        CallbackQueryHandler(sos_start_callback, pattern=r"^start:sos$"),
        CallbackQueryHandler(sos_confirm_callback, pattern=r"^start:sos:confirm$"),
        CallbackQueryHandler(sos_cancel_callback, pattern=r"^start:sos:cancel$"),
    ]
