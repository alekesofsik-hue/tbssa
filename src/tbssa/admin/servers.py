from __future__ import annotations

import asyncio
import re

from sqlalchemy import select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from tbssa.admin.audit import log_action
from tbssa.admin.guard import _svc
from tbssa.admin.menu import ADM_HOME, ADM_SERVERS, MAIN_MENU_TEXT, main_menu_keyboard
from tbssa.admin.monitor import (
    format_confirmed_reachability_report,
    run_confirmed_reachability_check,
)
from tbssa.ssh import ps, ssh_exec
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import Server

# ── callback_data constants ────────────────────────────────────────────────────
_SRV_LIST = "adm:srv:list"
_SRV_VIEW = "adm:srv:view:{id}"
_SRV_ADD = "adm:srv:add"
_SRV_EDIT = "adm:srv:edit:{id}"
_SRV_EDIT_FIELD = "adm:srv:edit:{id}:{field}"
_SRV_TOGGLE = "adm:srv:toggle:{id}"
_SRV_LIST_INACTIVE = "adm:srv:list:inactive"
_SRV_DEL_CONFIRM = "adm:srv:del:{id}"
_SRV_DEL_YES = "adm:srv:del:{id}:yes"
_SRV_CHECK = "adm:srv:check:{id}"
_SRV_POWEROFF = "adm:srv:poweroff:{id}"
_SRV_POWEROFF_YES = "adm:srv:poweroff:{id}:yes"
_SRV_REBOOT = "adm:srv:reboot:{id}"
_SRV_REBOOT_YES = "adm:srv:reboot:{id}:yes"

# ── FSM states ────────────────────────────────────────────────────────────────
(
    _S_NAME,
    _S_SSH_HOST,
    _S_CONFIRM,
    _S_EDIT_VALUE,
) = range(4)

_CTX_DRAFT = "adm:srv:draft"       # new server draft
_CTX_EDIT_ID = "adm:srv:edit_id"   # server id being edited
_CTX_EDIT_FIELD = "adm:srv:edit_f" # field being edited

# ── Validation helpers ─────────────────────────────────────────────────────────
_HOSTNAME_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
)
_IP_RE = re.compile(
    r"^(\d{1,3}\.){3}\d{1,3}$"
)


def _valid_host(value: str) -> bool:
    return bool(_IP_RE.match(value) or _HOSTNAME_RE.match(value))


def _valid_name(value: str) -> bool:
    return bool(value) and len(value) <= 64 and re.match(r"^[\w\-]+$", value)


def _valid_path(value: str) -> bool:
    return bool(value) and len(value) <= 512


# ── Keyboards ─────────────────────────────────────────────────────────────────


def _reach_icon(s: Server) -> str:
    """Icon reflecting confirmed SSH availability of an active server."""
    if not s.is_active:
        return "⛔"
    if s.last_ping_ok is None:
        return "🔵"  # active, SSH state not confirmed yet
    return "🟢" if s.last_ping_ok else "🟡"  # 🟡 = active, but SSH unavailable


async def _load_servers_split() -> tuple[list[Server], list[Server]]:
    async with AsyncSessionLocal() as session:
        servers = (await session.execute(select(Server).order_by(Server.name))).scalars().all()
    active = [s for s in servers if s.is_active]
    inactive = [s for s in servers if not s.is_active]
    return active, inactive


def _servers_list_text(*, active_count: int, inactive_count: int, show_inactive: bool) -> str:
    if show_inactive:
        text = "🖥 <b>Неактивные серверы</b>"
        if not inactive_count:
            text += "\n\n<i>Неактивных серверов пока нет.</i>"
        else:
            text += f"\n\nВсего: <b>{inactive_count}</b>"
        return text

    text = "🖥 <b>Серверы</b>\n\n"
    text += f"Активные: <b>{active_count}</b>"
    if inactive_count:
        text += f"\nНеактивные: <b>{inactive_count}</b>"
    if not active_count:
        text += "\n\n<i>Активных серверов пока нет. Нажмите «Добавить».</i>"
    return text


def _servers_list_keyboard(
    servers: list[Server],
    *,
    inactive_count: int = 0,
    show_inactive: bool = False,
) -> InlineKeyboardMarkup:
    rows = []
    for s in servers:
        icon = _reach_icon(s)
        rows.append([
            InlineKeyboardButton(f"{icon} {s.name}", callback_data=_SRV_VIEW.format(id=s.id)),
            InlineKeyboardButton("Вкл." if not s.is_active else "Откл.", callback_data=_SRV_TOGGLE.format(id=s.id)),
        ])
    if show_inactive:
        rows.append([InlineKeyboardButton("◀️ Активные серверы", callback_data=_SRV_LIST)])
    elif inactive_count:
        rows.append([
            InlineKeyboardButton(f"📦 Неактивные ({inactive_count})", callback_data=_SRV_LIST_INACTIVE)
        ])
    rows.append([InlineKeyboardButton("➕ Добавить сервер", callback_data=_SRV_ADD)])
    rows.append([InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)])
    return InlineKeyboardMarkup(rows)


def _server_card_keyboard(server_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔍 Проверить SSH", callback_data=_SRV_CHECK.format(id=server_id)),
            InlineKeyboardButton("⚡ Выключить", callback_data=_SRV_POWEROFF.format(id=server_id)),
        ],
        [InlineKeyboardButton("🔄 Перезагрузка", callback_data=_SRV_REBOOT.format(id=server_id))],
        [
            InlineKeyboardButton("✏️ Редактировать", callback_data=_SRV_EDIT.format(id=server_id)),
            InlineKeyboardButton("🗑 Удалить", callback_data=_SRV_DEL_CONFIRM.format(id=server_id)),
        ],
        [InlineKeyboardButton("◀️ Список серверов", callback_data=_SRV_LIST)],
    ])


def _edit_fields_keyboard(server_id: int) -> InlineKeyboardMarkup:
    fields = [
        ("Имя", "name"),
        ("Host", "ssh_host"),  # единственный адрес для SSH и проверки доступности
        ("Fingerprint", "ssh_fingerprint"),
    ]
    rows = []
    row: list[InlineKeyboardButton] = []
    for label, field in fields:
        row.append(InlineKeyboardButton(label, callback_data=_SRV_EDIT_FIELD.format(id=server_id, field=field)))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=_SRV_VIEW.format(id=server_id))])
    return InlineKeyboardMarkup(rows)


def _delete_confirm_keyboard(server_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, удалить", callback_data=_SRV_DEL_YES.format(id=server_id)),
            InlineKeyboardButton("❌ Отмена", callback_data=_SRV_VIEW.format(id=server_id)),
        ],
    ])


def _poweroff_confirm_keyboard(server_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, выключить", callback_data=_SRV_POWEROFF_YES.format(id=server_id)),
            InlineKeyboardButton("❌ Отмена", callback_data=_SRV_VIEW.format(id=server_id)),
        ],
    ])


def _reboot_confirm_keyboard(server_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Да, перезагрузить", callback_data=_SRV_REBOOT_YES.format(id=server_id)),
            InlineKeyboardButton("❌ Отмена", callback_data=_SRV_VIEW.format(id=server_id)),
        ],
    ])


# ── Server card text ──────────────────────────────────────────────────────────


def _reach_text(s: Server) -> str:
    if s.last_ping_ok is None:
        return "⚪ не подтверждался"
    ts = s.last_ping_at.strftime("%d.%m %H:%M") if s.last_ping_at else "—"
    if s.last_ping_ok:
        return f"🟢 доступен ({ts})"
    return f"🔴 недоступен ({ts})"


def _server_card_text(
    s: Server, ssh_user: str = "bot-admin", ssh_key_path: str = "~/.ssh/id_ed25519_bot"
) -> str:
    in_work = "В работе: Да ✅" if s.is_active else "В работе: Нет ⛔"
    fp = s.ssh_fingerprint or "—"
    return (
        f"🖥 <b>{s.name}</b>\n\n"
        f"Host: <code>{s.ssh_host}</code>\n"
        f"SSH: <code>{ssh_user}@{s.ssh_host}</code>\n"
        f"Ключ: <code>{ssh_key_path}</code>\n"
        f"Known hosts: <code>{s.ssh_known_hosts_path}</code>\n"
        f"Fingerprint: <code>{fp}</code>\n"
        f"{in_work}\n"
        f"Подтверждённый SSH-статус мониторинга: {_reach_text(s)}"
    )


# ── Section entry: list all servers ──────────────────────────────────────────


async def show_servers_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: called from admin_callback when adm:servers is pressed."""
    query = update.callback_query
    text, keyboard = _servers_list_payload(await _load_servers_split(), show_inactive=False)
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


async def show_inactive_servers_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    text, keyboard = _servers_list_payload(await _load_servers_split(), show_inactive=True)
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


def _servers_list_payload(
    split_servers: tuple[list[Server], list[Server]],
    *,
    show_inactive: bool,
) -> tuple[str, InlineKeyboardMarkup]:
    active_servers, inactive_servers = split_servers
    visible_servers = inactive_servers if show_inactive else active_servers
    text = _servers_list_text(
        active_count=len(active_servers),
        inactive_count=len(inactive_servers),
        show_inactive=show_inactive,
    )
    keyboard = _servers_list_keyboard(
        list(visible_servers),
        inactive_count=len(inactive_servers),
        show_inactive=show_inactive,
    )
    return text, keyboard


# ── View server card ──────────────────────────────────────────────────────────


async def show_server_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    server_id = int(query.data.split(":")[3])
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=None)
        return
    svc = _svc(context)
    ssh_user = svc.get_str("SSH_DEFAULT_USER", "bot-admin")
    ssh_key_path = svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
    await query.edit_message_text(
        _server_card_text(server, ssh_user, ssh_key_path),
        reply_markup=_server_card_keyboard(server_id),
        parse_mode=ParseMode.HTML,
    )


# ── Toggle active/inactive ────────────────────────────────────────────────────


async def toggle_server(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    server_id = int(query.data.split(":")[3])
    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await query.answer("Сервер не найден.", show_alert=True)
            return
        server.is_active = not server.is_active
        is_active = server.is_active
        action = "activate" if server.is_active else "deactivate"
        await log_action(session, uid, uname, f"server:{action}", f"server={server.name}")
        await session.commit()

    await _svc(context).reload()
    await query.answer(f"{'Включён ✅' if is_active else 'Отключён 🔴'}")
    text, keyboard = _servers_list_payload(await _load_servers_split(), show_inactive=not is_active)
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


# ── Delete server: confirm screen ─────────────────────────────────────────────


async def delete_server_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    server_id = int(query.data.split(":")[3])
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    name = server.name if server else str(server_id)
    await query.edit_message_text(
        f"🗑 Удалить сервер <b>{name}</b>?\n\n"
        "Сервер будет <b>полностью удалён</b> из системы. Действие необратимо.",
        reply_markup=_delete_confirm_keyboard(server_id),
        parse_mode=ParseMode.HTML,
    )


async def delete_server_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    server_id = int(query.data.split(":")[3])
    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await query.answer("Сервер не найден.", show_alert=True)
            return
        was_active = server.is_active
        server_name = server.name
        await log_action(session, uid, uname, "server:delete", f"server={server_name}")
        await session.delete(server)
        await session.commit()

    await _svc(context).reload()
    await query.answer("Сервер удалён навсегда.")
    text, keyboard = _servers_list_payload(await _load_servers_split(), show_inactive=not was_active)
    await query.edit_message_text(
        text=text,
        reply_markup=keyboard,
        parse_mode=ParseMode.HTML,
    )


# ── Poweroff server: confirm + execute ─────────────────────────────────────────


async def poweroff_server_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    server_id = int(query.data.split(":")[3])
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=None)
        return
    await query.edit_message_text(
        f"⚡ Выключить сервер <b>{server.name}</b>?\n\n"
        "Будет отправлена команда жёсткого выключения.",
        reply_markup=_poweroff_confirm_keyboard(server_id),
        parse_mode=ParseMode.HTML,
    )


async def poweroff_server_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("⚡ Выключаю…")
    server_id = int(query.data.split(":")[3])
    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=None)
            return
        name = server.name
        # Copy SSH params for use outside session
        host = server.ssh_host
        svc = _svc(context)
        user = svc.get_str("SSH_DEFAULT_USER", "bot-admin")
        key_path = svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
        known_hosts = server.ssh_known_hosts_path
        fingerprint = server.ssh_fingerprint or ""
        connect_to = server.ssh_connect_timeout
        cmd_to = server.ssh_command_timeout

    poweroff_cmd = ps(_svc(context).get_str("SSH_CMD_POWEROFF", "shutdown /p /f"))
    try:
        rc, _, err = await asyncio.to_thread(
            ssh_exec,
            host=host,
            user=user,
            key_path=key_path,
            known_hosts_path=known_hosts,
            pinned_fingerprint_md5=fingerprint or "",
            connect_timeout=connect_to,
            command_timeout=cmd_to,
            cmd=poweroff_cmd,
        )
    except Exception as exc:
        async with AsyncSessionLocal() as session:
            await log_action(session, uid, uname, "poweroff", f"server={name} error={exc}")
            await session.commit()
        await query.edit_message_text(
            f"❌ SSH-ошибка: {exc}",
            reply_markup=_server_card_keyboard(server_id),
            parse_mode=ParseMode.HTML,
        )
        return

    async with AsyncSessionLocal() as session:
        await log_action(session, uid, uname, "poweroff", f"server={name} rc={rc}")
        await session.commit()

    if rc != 0:
        await query.edit_message_text(
            f"⚠️ Команда вернула rc={rc}\nstderr:\n{(err or '')[:400]}",
            reply_markup=_server_card_keyboard(server_id),
        )
    else:
        await query.edit_message_text(
            f"✅ Команда принята. Сервер <b>{name}</b> сейчас выключится.",
            reply_markup=_server_card_keyboard(server_id),
            parse_mode=ParseMode.HTML,
        )


# ── Reboot server: confirm + execute ───────────────────────────────────────────


async def reboot_server_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    server_id = int(query.data.split(":")[3])
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=None)
        return
    await query.edit_message_text(
        f"🔄 Перезагрузить сервер <b>{server.name}</b>?\n\n"
        "Будет отправлена команда перезагрузки.",
        reply_markup=_reboot_confirm_keyboard(server_id),
        parse_mode=ParseMode.HTML,
    )


async def reboot_server_yes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("🔄 Перезагружаю…")
    server_id = int(query.data.split(":")[3])
    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None
    svc = _svc(context)

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=None)
            return
        name = server.name
        host = server.ssh_host
        user = svc.get_str("SSH_DEFAULT_USER", "bot-admin")
        key_path = svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
        known_hosts = server.ssh_known_hosts_path
        fingerprint = server.ssh_fingerprint or ""
        connect_to = server.ssh_connect_timeout
        cmd_to = server.ssh_command_timeout

    reboot_cmd = ps(svc.get_str("SSH_CMD_REBOOT", "shutdown /r /t 0 /f"))
    try:
        rc, _, err = await asyncio.to_thread(
            ssh_exec,
            host=host,
            user=user,
            key_path=key_path,
            known_hosts_path=known_hosts,
            pinned_fingerprint_md5=fingerprint or "",
            connect_timeout=connect_to,
            command_timeout=cmd_to,
            cmd=reboot_cmd,
        )
    except Exception as exc:
        async with AsyncSessionLocal() as session:
            await log_action(session, uid, uname, "reboot", f"server={name} error={exc}")
            await session.commit()
        await query.edit_message_text(
            f"❌ SSH-ошибка: {exc}",
            reply_markup=_server_card_keyboard(server_id),
            parse_mode=ParseMode.HTML,
        )
        return

    async with AsyncSessionLocal() as session:
        await log_action(session, uid, uname, "reboot", f"server={name} rc={rc}")
        await session.commit()

    if rc != 0:
        await query.edit_message_text(
            f"⚠️ Команда вернула rc={rc}\nstderr:\n{(err or '')[:400]}",
            reply_markup=_server_card_keyboard(server_id),
        )
    else:
        await query.edit_message_text(
            f"✅ Команда принята. Сервер <b>{name}</b> уходит в перезагрузку.",
            reply_markup=_server_card_keyboard(server_id),
            parse_mode=ParseMode.HTML,
        )


# ── Show edit-fields menu ──────────────────────────────────────────────────────


async def show_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    server_id = int(query.data.split(":")[3])
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=None)
        return
    await query.edit_message_text(
        f"✏️ <b>Редактировать: {server.name}</b>\n\nЧто изменить?",
        reply_markup=_edit_fields_keyboard(server_id),
        parse_mode=ParseMode.HTML,
    )


# ── Edit single field (ConversationHandler) ────────────────────────────────────


async def edit_field_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    """Entry to edit-field conversation from callback adm:srv:edit:{id}:{field}."""
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    server_id = int(parts[3])
    field = parts[4]

    if field in ("ssh_user", "ssh_key_path", "ping_host"):
        msgs = {"ssh_user": "SSH user", "ssh_key_path": "Путь к SSH ключу", "ping_host": "Host"}
        msg = msgs.get(field, field)
        await query.edit_message_text(
            f"⚠️ {msg} задаётся в глобальных настройках (⚙️ Настройки).",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("◀️ Назад", callback_data=_SRV_VIEW.format(id=server_id))]
            ]),
            parse_mode=ParseMode.HTML,
        )
        return ConversationHandler.END

    context.user_data[_CTX_EDIT_ID] = server_id
    context.user_data[_CTX_EDIT_FIELD] = field

    labels = {
        "name": "Имя сервера (латиница, цифры, дефис)",
        "ssh_host": "Host (IP или hostname — для SSH и проверки доступности)",
        "ssh_fingerprint": "Fingerprint (MD5, или пустую строку чтобы убрать)",
    }
    await query.edit_message_text(
        f"✏️ Введите новое значение для <b>{labels.get(field, field)}</b>:\n"
        "Или /cancel для отмены.",
        parse_mode=ParseMode.HTML,
    )
    return _S_EDIT_VALUE


async def edit_field_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.message.text or "").strip()
    field = context.user_data.get(_CTX_EDIT_FIELD, "")
    server_id = context.user_data.get(_CTX_EDIT_ID)

    if field in ("ssh_user", "ssh_key_path", "ping_host"):
        await update.message.reply_text("⚠️ Это поле недоступно для редактирования.")
        return ConversationHandler.END

    # Validate
    if field == "name":
        if not _valid_name(value):
            await update.message.reply_text("⚠️ Имя должно содержать только латиницу, цифры и дефис (до 64 символов).")
            return _S_EDIT_VALUE
        async with AsyncSessionLocal() as session:
            existing = (
                await session.execute(
                    select(Server).where(
                        Server.name == value,
                        Server.id != server_id,
                    )
                )
            ).scalar_one_or_none()
            if existing:
                await update.message.reply_text("⚠️ Сервер с таким именем уже существует.")
                return _S_EDIT_VALUE
    elif field == "ssh_host" and not _valid_host(value):
        await update.message.reply_text("⚠️ Некорректный IP или hostname. Попробуйте ещё раз:")
        return _S_EDIT_VALUE

    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await update.message.reply_text("⚠️ Сервер не найден.")
            return ConversationHandler.END
        setattr(server, field, value if value else None)
        if field == "ssh_host":
            server.ping_host = value  # host один для SSH и проверки доступности
        await log_action(session, uid, uname, "server:edit", f"server={server.name} field={field}")
        await session.commit()

    await _svc(context).reload()
    await update.message.reply_text(
        f"✅ Поле <b>{field}</b> обновлено.",
        parse_mode=ParseMode.HTML,
    )

    # Show updated card
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if server:
        svc = _svc(context)
        ssh_user = svc.get_str("SSH_DEFAULT_USER", "bot-admin")
        ssh_key_path = svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
        await update.message.reply_text(
            _server_card_text(server, ssh_user, ssh_key_path),
            reply_markup=_server_card_keyboard(server_id),
            parse_mode=ParseMode.HTML,
        )
    return ConversationHandler.END


# ── Add server (ConversationHandler) ─────────────────────────────────────────


async def add_server_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    context.user_data[_CTX_DRAFT] = {}
    await query.edit_message_text(
        "➕ <b>Новый сервер</b>\n\n"
        "Шаг 1/2 — Введите <b>имя</b> сервера (латиница, цифры, дефис):\n"
        "/cancel для отмены.",
        parse_mode=ParseMode.HTML,
    )
    return _S_NAME


async def add_recv_name(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.message.text or "").strip()
    if not _valid_name(value):
        await update.message.reply_text("⚠️ Имя должно содержать только латиницу, цифры и дефис, не длиннее 64 символов. Повторите:")
        return _S_NAME
    async with AsyncSessionLocal() as session:
        exists = (await session.execute(select(Server).where(Server.name == value))).scalar_one_or_none()
    if exists:
        await update.message.reply_text(f"⚠️ Сервер с именем <b>{value}</b> уже существует. Введите другое имя:", parse_mode=ParseMode.HTML)
        return _S_NAME
    context.user_data[_CTX_DRAFT]["name"] = value
    await update.message.reply_text("Шаг 2/2 — Введите <b>Host</b> (IP или hostname):", parse_mode=ParseMode.HTML)
    return _S_SSH_HOST


async def add_recv_ssh_host(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.message.text or "").strip()
    if not _valid_host(value):
        await update.message.reply_text("⚠️ Некорректный IP или hostname. Попробуйте ещё раз:")
        return _S_SSH_HOST
    context.user_data[_CTX_DRAFT]["ssh_host"] = value

    draft = context.user_data[_CTX_DRAFT]
    svc = _svc(context)
    ssh_user = svc.get_str("SSH_DEFAULT_USER", "bot-admin")
    ssh_key_path = svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
    text = (
        "📋 <b>Проверьте данные нового сервера:</b>\n\n"
        f"Имя: <code>{draft['name']}</code>\n"
        f"Host: <code>{draft['ssh_host']}</code>\n"
        f"SSH user: <code>{ssh_user}</code>\n"
        f"Ключ: <code>{ssh_key_path}</code>\n\n"
        "Всё верно?"
    )
    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Сохранить", callback_data="adm:srv:add:save"),
            InlineKeyboardButton("❌ Отмена", callback_data="adm:srv:add:cancel"),
        ]
    ])
    await update.message.reply_text(text, reply_markup=keyboard, parse_mode=ParseMode.HTML)
    return _S_CONFIRM


async def add_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    uid = update.effective_user.id if update.effective_user else 0
    uname = update.effective_user.username if update.effective_user else None

    if query.data == "adm:srv:add:cancel":
        context.user_data.pop(_CTX_DRAFT, None)
        await query.edit_message_text(MAIN_MENU_TEXT, reply_markup=main_menu_keyboard(), parse_mode=ParseMode.HTML)
        return ConversationHandler.END

    draft = context.user_data.get(_CTX_DRAFT, {})
    async with AsyncSessionLocal() as session:
        svc = _svc(context)
        known_hosts = svc.get_first_server().ssh_known_hosts_path if svc.get_first_server() else "~/.ssh/known_hosts"
        ssh_user = svc.get_str("SSH_DEFAULT_USER", "bot-admin")
        ssh_key_path = svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
        server = Server(
            name=draft["name"],
            ssh_host=draft["ssh_host"],
            ssh_user=ssh_user,
            ssh_key_path=ssh_key_path,
            ssh_known_hosts_path=known_hosts,
            ping_host=draft["ssh_host"],  # тот же адрес для SSH и проверки доступности
            is_active=True,
        )
        session.add(server)
        await log_action(session, uid, uname, "server:add", f"server={draft['name']}")
        await session.commit()

    context.user_data.pop(_CTX_DRAFT, None)
    await _svc(context).reload()
    await query.edit_message_text(
        f"✅ Сервер <b>{draft['name']}</b> добавлен и активирован.",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def conv_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_CTX_DRAFT, None)
    context.user_data.pop(_CTX_EDIT_ID, None)
    context.user_data.pop(_CTX_EDIT_FIELD, None)
    await update.message.reply_text("❌ Действие отменено.")
    return ConversationHandler.END


# ── ConversationHandlers ───────────────────────────────────────────────────────


def add_server_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(add_server_start, pattern=r"^adm:srv:add$")],
        states={
            _S_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recv_name)],
            _S_SSH_HOST: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_recv_ssh_host)],
            _S_CONFIRM: [CallbackQueryHandler(add_confirm_callback, pattern=r"^adm:srv:add:(save|cancel)$")],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


def edit_server_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_field_start, pattern=r"^adm:srv:edit:\d+:\w+$")],
        states={
            _S_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_receive)],
        },
        fallbacks=[CommandHandler("cancel", conv_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


# ── Check server now ──────────────────────────────────────────────────────────


async def check_server_now(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer("🔍 Проверяю SSH по логике мониторинга…")
    server_id = int(query.data.split(":")[3])
    svc = _svc(context)

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await query.edit_message_text("⚠️ Сервер не найден.", reply_markup=None)
            return
        runtime_server = svc.to_server_config(server)
        confirmed_ok = server.last_ping_ok

    ping_template = svc.get_str("PING_CMD_TEMPLATE", "")
    report = format_confirmed_reachability_report(
        await run_confirmed_reachability_check(
            runtime_server,
            confirmed_ok=confirmed_ok,
            ping_template=ping_template,
        )
    )

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)

    ssh_user = svc.get_str("SSH_DEFAULT_USER", "bot-admin")
    ssh_key_path = svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
    await query.edit_message_text(
        f"{report}\n\n{_server_card_text(server, ssh_user, ssh_key_path)}",
        reply_markup=_server_card_keyboard(server_id),
        parse_mode=ParseMode.HTML,
    )


# ── Flat callback handlers list ────────────────────────────────────────────────


def get_server_handlers() -> list:
    """Return all server-related handlers for registration in app.py."""
    return [
        # ConversationHandlers must come first (higher priority)
        add_server_conversation(),
        edit_server_conversation(),
        # Flat callbacks
        CallbackQueryHandler(show_inactive_servers_list, pattern=r"^adm:srv:list:inactive$"),
        CallbackQueryHandler(show_servers_list, pattern=r"^adm:srv:list$"),
        CallbackQueryHandler(show_server_card, pattern=r"^adm:srv:view:\d+$"),
        CallbackQueryHandler(show_edit_menu, pattern=r"^adm:srv:edit:\d+$"),
        CallbackQueryHandler(toggle_server, pattern=r"^adm:srv:toggle:\d+$"),
        CallbackQueryHandler(delete_server_confirm, pattern=r"^adm:srv:del:\d+$"),
        CallbackQueryHandler(delete_server_yes, pattern=r"^adm:srv:del:\d+:yes$"),
        CallbackQueryHandler(check_server_now, pattern=r"^adm:srv:check:\d+$"),
        CallbackQueryHandler(poweroff_server_confirm, pattern=r"^adm:srv:poweroff:\d+$"),
        CallbackQueryHandler(poweroff_server_yes, pattern=r"^adm:srv:poweroff:\d+:yes$"),
        CallbackQueryHandler(reboot_server_confirm, pattern=r"^adm:srv:reboot:\d+$"),
        CallbackQueryHandler(reboot_server_yes, pattern=r"^adm:srv:reboot:\d+:yes$"),
    ]
