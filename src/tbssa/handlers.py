from __future__ import annotations

import asyncio
import time
from datetime import datetime
from functools import wraps
from typing import Awaitable, Callable, TypeVar

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes

from tbssa.config_service import ConfigService, ServerConfig
from tbssa.ssh import ps, ssh_exec

T = TypeVar("T")

_RU_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


# ── Helpers ───────────────────────────────────────────────────────────────────


def _svc(context: ContextTypes.DEFAULT_TYPE) -> ConfigService:
    return context.bot_data["config_service"]


def _fmt_date(dt: datetime) -> str:
    return f"{dt.day} {_RU_MONTHS[dt.month]} {dt.year} г."


# ── Guard decorator ───────────────────────────────────────────────────────────


def admin_required(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """Deny access if user is not in config_service admin list (fail-closed)."""

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> T | None:
        uid = update.effective_user.id if update.effective_user else None
        svc = _svc(context)

        if not svc.is_ready():
            await update.effective_chat.send_message(
                "⛔ Бот ещё не готов. Попробуйте через несколько секунд."
            )
            return None

        if not svc.is_admin(uid):
            await update.effective_chat.send_message("⛔ Доступ запрещён.")
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
            InlineKeyboardButton("✅ Подтвердить", callback_data="start:sos:confirm"),
            InlineKeyboardButton("❌ Отмена", callback_data="start:sos:cancel"),
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
            "🛠 Управление",
            reply_markup=_admin_start_keyboard(svc),
        )
    else:
        now = datetime.now()
        await update.message.reply_text(
            f"Здравствуйте! Сегодня {_fmt_date(now)}, "
            f"текущее время {now.strftime('%H:%M')}. До свидания!"
        )


async def my_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Команда /my — показать свой ID незарегистрированному пользователю."""
    uid = update.effective_user.id if update.effective_user else 0
    await update.message.reply_text(
        f"Ваш ID: <code>{uid}</code>\n\n"
        "Сообщите его владельцу бота для получения доступа.",
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

    names = ", ".join(f"<b>{s.name}</b>" for s in servers)
    await edit_fn(
        f"🆘 <b>SOS — выключаю {len(servers)} сервер(а):</b> {names}…",
        parse_mode=ParseMode.HTML,
    )

    poweroff_cmd = ps(svc.get_str("SSH_CMD_POWEROFF", "shutdown /p /f"))

    async def _shutdown_one(s: ServerConfig) -> tuple[str, int, str | None]:
        try:
            rc, _, err = await asyncio.to_thread(
                ssh_exec,
                host=s.ssh_host,
                user=s.ssh_user,
                key_path=s.ssh_key_path,
                known_hosts_path=s.ssh_known_hosts_path,
                pinned_fingerprint_md5=s.ssh_fingerprint,
                connect_timeout=s.ssh_connect_timeout,
                command_timeout=s.ssh_command_timeout,
                cmd=poweroff_cmd,
            )
            return s.name, rc, err
        except Exception as exc:
            return s.name, -1, str(exc)

    results = await asyncio.gather(*[_shutdown_one(s) for s in servers])

    header = svc.get_str("SOS_MSG_HEADER", "SOS выполнен")
    lines: list[str] = [f"🆘 <b>{header}</b>\n"]
    for name, rc, err in results:
        if rc == 0:
            lines.append(f"🖥 {name}: ✅ команда принята")
        elif rc == -1:
            lines.append(f"🖥 {name}: ❌ ошибка: {err}")
        else:
            lines.append(f"🖥 {name}: ⚠️ rc={rc}")
        await svc.write_audit(uid, uname, "sos:all", f"server={name} rc={rc}")

    who = f"@{uname}" if uname else f"id:{uid}"
    lines.append(f"\nИнициировал: {who}")
    report = "\n".join(lines)

    await edit_fn(
        report,
        reply_markup=_admin_start_keyboard(svc),
        parse_mode=ParseMode.HTML,
    )

    for admin_id in svc.get_admin_ids():
        if admin_id == uid:
            continue
        try:
            await context.bot.send_message(admin_id, report, parse_mode=ParseMode.HTML)
        except Exception:
            pass


# ── SOS-all callback (from /start button) ─────────────────────────────────────


async def sos_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """SOS button pressed: either ask for confirmation or execute immediately."""
    query = update.callback_query
    await query.answer()

    uid = update.effective_user.id if update.effective_user else 0
    svc = _svc(context)

    if not svc.is_ready() or not svc.is_admin(uid):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    if svc.get_int("SOS_REQUIRE_CONFIRM", 0):
        label = _sos_label(svc)
        await query.edit_message_text(
            f"⚠️ <b>Вы уверены, что хотите выполнить {label}?</b>\n\n"
            "Все активные серверы будут немедленно выключены.",
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
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    await _exec_sos_all(update, uid, uname, svc, context)


async def sos_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Confirmation button 'Отмена' — return to start menu."""
    query = update.callback_query
    await query.answer("Отменено.")

    uid = update.effective_user.id if update.effective_user else 0
    svc = _svc(context)

    if not svc.is_ready() or not svc.is_admin(uid):
        await query.edit_message_text("⛔ Доступ запрещён.")
        return

    await query.edit_message_text(
        "🛠 Управление",
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
            f"⚠️ <b>Вы уверены, что хотите выполнить {label}?</b>\n\n"
            "Все активные серверы будут немедленно выключены.",
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
