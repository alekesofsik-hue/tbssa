from __future__ import annotations

import asyncio

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes

from tbssa.admin.audit import log_action
from tbssa.admin.guard import _svc
from tbssa.admin.menu import ADM_HOME
from tbssa.config_service import ServerConfig
from tbssa.db.engine import AsyncSessionLocal
from tbssa.ssh import ps, ssh_exec

# ── callback_data constants ────────────────────────────────────────────────────
_OPS_REBOOT_PICK = "adm:ops:reboot:pick:{id}"
_OPS_REBOOT_CONFIRM = "adm:ops:reboot:confirm:{id}"


# ── Shared keyboards ───────────────────────────────────────────────────────────


def _back_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)]])


def _no_servers_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)]])


def _reboot_picker_keyboard(servers: list[ServerConfig]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(f"🖥 {s.name}", callback_data=_OPS_REBOOT_PICK.format(id=s.id))]
        for s in servers
    ]
    rows.append([InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)])
    return InlineKeyboardMarkup(rows)


def _reboot_confirm_keyboard(server_id: int, server_name: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(
            f"✅ Перезагрузить «{server_name}»",
            callback_data=_OPS_REBOOT_CONFIRM.format(id=server_id),
        )],
        [InlineKeyboardButton("❌ Отмена", callback_data=ADM_HOME)],
    ])


# ── Reboot ─────────────────────────────────────────────────────────────────────


async def show_reboot_picker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point from admin_callback (ADM_REBOOT button)."""
    query = update.callback_query
    svc = _svc(context)
    servers = svc.get_servers()

    if not servers:
        await query.edit_message_text(
            "⚠️ Нет активных серверов.",
            reply_markup=_no_servers_keyboard(),
        )
        return

    if len(servers) == 1:
        s = servers[0]
        await query.edit_message_text(
            f"🔄 <b>Перезагрузка</b>\n\nСервер: <b>{s.name}</b> (<code>{s.ssh_host}</code>)\n\n"
            "Подтвердить выполнение?",
            reply_markup=_reboot_confirm_keyboard(s.id, s.name),
            parse_mode=ParseMode.HTML,
        )
        return

    await query.edit_message_text(
        "🔄 <b>Перезагрузка</b>\n\nВыберите сервер:",
        reply_markup=_reboot_picker_keyboard(servers),
        parse_mode=ParseMode.HTML,
    )


async def reboot_pick_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    server_id = int(query.data.split(":")[4])
    svc = _svc(context)
    server = svc.get_server(server_id)

    if not server:
        await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=_back_keyboard())
        return

    await query.edit_message_text(
        f"🔄 <b>Перезагрузка</b>\n\nСервер: <b>{server.name}</b> (<code>{server.ssh_host}</code>)\n\n"
        "Подтвердить выполнение?",
        reply_markup=_reboot_confirm_keyboard(server_id, server.name),
        parse_mode=ParseMode.HTML,
    )


async def reboot_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    server_id = int(query.data.split(":")[4])
    svc = _svc(context)
    server = svc.get_server(server_id)
    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None

    if not server:
        await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=_back_keyboard())
        return

    await query.edit_message_text(
        f"Перезагружаю *{server.name}*… ♻️",
        parse_mode=ParseMode.MARKDOWN,
    )

    try:
        rc, _, err = await asyncio.to_thread(
            ssh_exec,
            host=server.ssh_host,
            user=server.ssh_user,
            key_path=server.ssh_key_path,
            known_hosts_path=server.ssh_known_hosts_path,
            pinned_fingerprint_md5=server.ssh_fingerprint,
            connect_timeout=server.ssh_connect_timeout,
            command_timeout=server.ssh_command_timeout,
            cmd=ps(svc.get_str("SSH_CMD_REBOOT", "shutdown /r /t 0 /f")),
        )
    except Exception as exc:
        await query.edit_message_text(
            f"❌ SSH-ошибка: {exc}",
            reply_markup=_back_keyboard(),
        )
        async with AsyncSessionLocal() as session:
            await log_action(session, uid, uname, "reboot", f"server={server.name} error={exc}")
            await session.commit()
        return

    async with AsyncSessionLocal() as session:
        await log_action(session, uid, uname, "reboot", f"server={server.name} rc={rc}")
        await session.commit()

    if rc != 0:
        await query.edit_message_text(
            f"⚠️ Команда вернула rc={rc}\nstderr:\n{(err or '')[:400]}",
            reply_markup=_back_keyboard(),
        )
    else:
        await query.edit_message_text(
            f"✅ Команда принята. *{server.name}* уходит в перезагрузку.",
            reply_markup=_back_keyboard(),
            parse_mode=ParseMode.MARKDOWN,
        )


# ── Handler list ───────────────────────────────────────────────────────────────


def get_ops_handlers() -> list:
    return [
        CallbackQueryHandler(reboot_pick_callback, pattern=r"^adm:ops:reboot:pick:\d+$"),
        CallbackQueryHandler(reboot_confirm_callback, pattern=r"^adm:ops:reboot:confirm:\d+$"),
    ]
