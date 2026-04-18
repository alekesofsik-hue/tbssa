from __future__ import annotations

from sqlalchemy import func, select
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
from tbssa.admin.menu import ADM_HOME
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import User

# ── Sync admin from Telegram ────────────────────────────────────────────────────


async def sync_admin_from_telegram(telegram_id: int, username: str | None, first_name: str | None) -> None:
    """Обновляет username и first_name админа из данных Telegram при его взаимодействии с ботом."""
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.telegram_id == telegram_id))
        ).scalar_one_or_none()
        if user:
            user.username = username
            user.first_name = first_name
            await session.commit()


async def sync_admin_from_max(max_user_id: int, username: str | None, first_name: str | None) -> None:
    """Обновляет username и first_name админа из данных MAX при его взаимодействии с ботом."""
    async with AsyncSessionLocal() as session:
        user = (
            await session.execute(select(User).where(User.max_user_id == max_user_id))
        ).scalar_one_or_none()
        if user:
            user.max_username = username
            user.max_first_name = first_name
            await session.commit()


# ── callback_data constants ────────────────────────────────────────────────────
_USR_LIST = "adm:usr:list"
_USR_CARD = "adm:usr:card:{id}"
_USR_ADD = "adm:usr:add"
_USR_ADD_TG = "adm:usr:add:tg"
_USR_ADD_MAX = "adm:usr:add:max"
_USR_TOGGLE = "adm:usr:toggle:{id}"
_USR_EDIT = "adm:usr:edit:{id}"
_USR_EDIT_FIELD = "adm:usr:edit:{id}:{field}"

# ── FSM state ─────────────────────────────────────────────────────────────────
_S_ADD_PLATFORM = 0
_S_ADD_ID = 1
_S_EDIT_VALUE = 2
_CTX_ADD_FIELD = "adm:usr:add_field"
_CTX_EDIT_ID = "adm:usr:edit_id"
_CTX_EDIT_FIELD = "adm:usr:edit_field"


# ── Keyboards ─────────────────────────────────────────────────────────────────


def _truncate_button_text(text: str, max_bytes: int = 64) -> str:
    """Обрезает текст до max_bytes (лимит Telegram для текста кнопки)."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    while encoded and len(encoded) > max_bytes:
        text = text[:-1]
        encoded = text.encode("utf-8")
    return text


def _user_ref(u: User) -> str:
    parts: list[str] = []
    if u.telegram_id is not None:
        parts.append(f"tg:{u.telegram_id}")
    if u.max_user_id is not None:
        parts.append(f"max:{u.max_user_id}")
    return " / ".join(parts) if parts else f"user:{u.id}"


def _user_title(u: User) -> str:
    if u.display_name:
        return u.display_name
    if u.first_name:
        return u.first_name
    if u.max_first_name:
        return u.max_first_name
    return _user_ref(u)


def _platform_field_label(field: str) -> str:
    return {
        "telegram_id": "Telegram ID",
        "max_user_id": "MAX ID",
        "display_name": "Реальное имя",
    }.get(field, field)


def _platform_field_hint(field: str) -> str:
    if field == "telegram_id":
        return (
            "Введите число или пустую строку для очистки.\n"
            "<i>Подсказка: попросите пользователя отправить боту команду /my.</i>\n\n"
        )
    if field == "max_user_id":
        return (
            "Введите число или пустую строку для очистки.\n"
            "<i>Подсказка: в MAX пользователь может узнать ID через команду /my.</i>\n\n"
        )
    return ""


def _add_user_platform_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Telegram ID", callback_data=_USR_ADD_TG)],
        [InlineKeyboardButton("MAX ID", callback_data=_USR_ADD_MAX)],
        [InlineKeyboardButton("◀️ Назад", callback_data=_USR_LIST)],
    ])


def _user_list_label(u: User) -> str:
    """Метка для списка: ID · Реальное имя · @username."""
    icon = "✅" if u.is_active else "🚫"
    name = _user_title(u)
    handles: list[str] = []
    if u.username:
        handles.append(f"tg:@{u.username}")
    if u.max_username:
        handles.append(f"max:@{u.max_username}")
    parts = [_user_ref(u), name, " | ".join(handles)]
    label = " · ".join(p for p in parts if p)
    return _truncate_button_text(f"{icon} {label}")


def _users_list_keyboard(users: list[User]) -> InlineKeyboardMarkup:
    rows = []
    for u in users:
        # Метка пользователя в отдельной строке — полная ширина, текст не обрезается
        rows.append([
            InlineKeyboardButton(_user_list_label(u), callback_data=_USR_CARD.format(id=u.id)),
        ])
        rows.append([
            InlineKeyboardButton(
                "Откл." if u.is_active else "Вкл.",
                callback_data=_USR_TOGGLE.format(id=u.id),
            ),
        ])
    rows.append([InlineKeyboardButton("➕ Добавить", callback_data=_USR_ADD)])
    rows.append([InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)])
    return InlineKeyboardMarkup(rows)


def _user_card_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Редактировать", callback_data=_USR_EDIT.format(id=user_id))],
        [InlineKeyboardButton("◀️ Список", callback_data=_USR_LIST)],
    ])


# ── Card text ──────────────────────────────────────────────────────────────────


def _user_card_text(u: User) -> str:
    status = "Активен ✅" if u.is_active else "Отключён 🚫"
    tg_uname = f"@{u.username}" if u.username else "—"
    max_uname = f"@{u.max_username}" if u.max_username else "—"
    dname = u.display_name or "—"
    return (
        f"👤 <b>Пользователь</b>\n\n"
        f"Telegram ID: <code>{u.telegram_id if u.telegram_id is not None else '—'}</code>\n"
        f"Telegram username: {tg_uname}\n"
        f"MAX ID: <code>{u.max_user_id if u.max_user_id is not None else '—'}</code>\n"
        f"MAX username: {max_uname}\n"
        f"Реальное имя: {dname}\n"
        f"Статус: {status}\n"
        f"Добавлен: {u.created_at.strftime('%d.%m.%Y %H:%M')}"
    )


# ── List ──────────────────────────────────────────────────────────────────────


async def show_users_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()

    text = "👥 <b>Пользователи</b>"
    if not users:
        text += "\n\n<i>Нет зарегистрированных пользователей.</i>"

    await query.edit_message_text(
        text,
        reply_markup=_users_list_keyboard(list(users)),
        parse_mode=ParseMode.HTML,
    )


# ── Edit user ──────────────────────────────────────────────────────────────────


def _edit_fields_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("Реальное имя", callback_data=_USR_EDIT_FIELD.format(id=user_id, field="display_name"))],
        [InlineKeyboardButton("Telegram ID", callback_data=_USR_EDIT_FIELD.format(id=user_id, field="telegram_id"))],
        [InlineKeyboardButton("MAX ID", callback_data=_USR_EDIT_FIELD.format(id=user_id, field="max_user_id"))],
        [InlineKeyboardButton("◀️ Назад", callback_data=_USR_CARD.format(id=user_id))],
    ])


async def show_user_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split(":")[3])
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if not user:
        await query.edit_message_text("⚠️ Пользователь не найден.", reply_markup=None)
        return
    await query.edit_message_text(
        f"✏️ <b>Редактировать: {_user_title(user)}</b>\n\n"
        "Что изменить?",
        reply_markup=_edit_fields_keyboard(user_id),
        parse_mode=ParseMode.HTML,
    )


async def edit_field_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":")
    user_id = int(parts[3])
    field = parts[4]
    context.user_data[_CTX_EDIT_ID] = user_id
    context.user_data[_CTX_EDIT_FIELD] = field
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    current = ""
    title = _platform_field_label(field)
    if user and field == "display_name":
        current = (user.display_name or "").strip()
    elif user and field == "telegram_id":
        current = str(user.telegram_id) if user.telegram_id is not None else ""
    elif user and field == "max_user_id":
        current = str(user.max_user_id) if user.max_user_id is not None else ""
    extra_hint = _platform_field_hint(field)
    await query.edit_message_text(
        f"✏️ Введите <b>{title}</b> администратора:\n\n"
        f"Текущее значение: <code>{current or '—'}</code>\n\n"
        f"{extra_hint}"
        "/cancel для отмены.",
        parse_mode=ParseMode.HTML,
    )
    return _S_EDIT_VALUE


async def edit_field_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.message.text or "").strip()[:128]
    user_id = context.user_data.get(_CTX_EDIT_ID)
    field = context.user_data.get(_CTX_EDIT_FIELD)
    actor_uid = update.effective_user.id if update.effective_user else 0
    actor_uname = update.effective_user.username if update.effective_user else None

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            await update.message.reply_text("⚠️ Пользователь не найден.")
            return ConversationHandler.END
        if field == "display_name":
            user.display_name = value if value else None
        elif field in {"telegram_id", "max_user_id"}:
            if field == "telegram_id" and user.telegram_id == actor_uid and user.is_active and value != str(actor_uid):
                await update.message.reply_text("⛔ Нельзя менять собственный Telegram ID из Telegram-сессии.")
                return _S_EDIT_VALUE
            if value:
                if not value.isdigit():
                    await update.message.reply_text(f"⚠️ {_platform_field_label(field)} должен быть целым числом.")
                    return _S_EDIT_VALUE
                parsed = int(value)
                column = User.telegram_id if field == "telegram_id" else User.max_user_id
                existing = (
                    await session.execute(
                        select(User).where(column == parsed, User.id != user_id)
                    )
                ).scalar_one_or_none()
                if existing:
                    await update.message.reply_text(
                        f"⚠️ Такой {_platform_field_label(field)} уже привязан к другому пользователю."
                    )
                    return _S_EDIT_VALUE
                setattr(user, field, parsed)
                if field == "telegram_id":
                    user.username = None
                    user.first_name = None
                else:
                    user.max_username = None
                    user.max_first_name = None
            else:
                other_id = user.max_user_id if field == "telegram_id" else user.telegram_id
                if other_id is None:
                    await update.message.reply_text(
                        "⛔ У пользователя должен остаться хотя бы один platform ID."
                    )
                    return _S_EDIT_VALUE
                setattr(user, field, None)
                if field == "telegram_id":
                    user.username = None
                    user.first_name = None
                else:
                    user.max_username = None
                    user.max_first_name = None
        else:
            await update.message.reply_text("⚠️ Неизвестное поле редактирования.")
            return ConversationHandler.END
        await log_action(
            session, actor_uid, actor_uname,
            "user:edit", f"target={_user_ref(user)} field={field} value={value!r}"
        )
        await session.commit()

    await _svc(context).reload()
    context.user_data.pop(_CTX_EDIT_ID, None)
    context.user_data.pop(_CTX_EDIT_FIELD, None)
    label = _platform_field_label(field)
    await update.message.reply_text(f"✅ {label} обновлено: <code>{value or '—'}</code>", parse_mode=ParseMode.HTML)

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if user:
        await update.message.reply_text(
            _user_card_text(user),
            reply_markup=_user_card_keyboard(user_id),
            parse_mode=ParseMode.HTML,
        )
    return ConversationHandler.END


async def edit_field_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_CTX_EDIT_ID, None)
    context.user_data.pop(_CTX_EDIT_FIELD, None)
    await update.message.reply_text("❌ Редактирование отменено.")
    return ConversationHandler.END


# ── Card ──────────────────────────────────────────────────────────────────────


async def show_user_card(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = int(query.data.split(":")[3])
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if not user:
        await query.edit_message_text("⚠️ Пользователь не найден.", reply_markup=None)
        return
    await query.edit_message_text(
        _user_card_text(user),
        reply_markup=_user_card_keyboard(user_id),
        parse_mode=ParseMode.HTML,
    )


# ── Toggle active / inactive ───────────────────────────────────────────────────


async def toggle_user(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    user_id = int(query.data.split(":")[3])
    actor_uid = update.effective_user.id if update.effective_user else 0
    actor_uname = update.effective_user.username if update.effective_user else None

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            await query.answer("Пользователь не найден.", show_alert=True)
            return

        # Cannot deactivate self
        if user.telegram_id is not None and user.telegram_id == actor_uid and user.is_active:
            await query.answer("⛔ Нельзя отключить самого себя.", show_alert=True)
            return

        # Cannot deactivate the last active user
        if user.is_active:
            active_count = (
                await session.execute(
                    select(func.count()).where(User.is_active == True)  # noqa: E712
                )
            ).scalar_one()
            if active_count <= 1:
                await query.answer(
                    "⛔ Нельзя отключить последнего активного пользователя.",
                    show_alert=True,
                )
                return

        user.is_active = not user.is_active
        action = "activate" if user.is_active else "deactivate"
        await log_action(
            session, actor_uid, actor_uname,
            f"user:{action}", f"target={_user_ref(user)}"
        )
        await session.commit()

    await _svc(context).reload()
    status = "Включён ✅" if user.is_active else "Отключён 🚫"
    await query.answer(status)

    # Refresh list
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(User).order_by(User.created_at))).scalars().all()
    await query.edit_message_text(
        "👥 <b>Пользователи</b>",
        reply_markup=_users_list_keyboard(list(users)),
        parse_mode=ParseMode.HTML,
    )


# ── Add user (ConversationHandler) ────────────────────────────────────────────


async def add_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "➕ <b>Добавить пользователя</b>\n\n"
        "Выберите платформу, по ID которой хотите добавить администратора.",
        reply_markup=_add_user_platform_keyboard(),
        parse_mode=ParseMode.HTML,
    )
    return _S_ADD_PLATFORM


async def add_user_choose_platform(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    field = "telegram_id" if query.data == _USR_ADD_TG else "max_user_id"
    context.user_data[_CTX_ADD_FIELD] = field
    platform_name = _platform_field_label(field)
    hint = _platform_field_hint(field)
    access_hint = (
        "Теперь он сможет использовать /admin."
        if field == "telegram_id"
        else "Теперь он сможет использовать /admin в MAX."
    )
    context.user_data[_CTX_ADD_FIELD + ":success_hint"] = access_hint
    await query.edit_message_text(
        f"➕ <b>Добавить пользователя</b>\n\n"
        f"Введите <b>{platform_name}</b> пользователя.\n\n"
        f"{hint}"
        "/cancel для отмены.",
        parse_mode=ParseMode.HTML,
    )
    return _S_ADD_ID


async def add_user_back_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    if query:
        await query.answer()
    context.user_data.pop(_CTX_ADD_FIELD, None)
    context.user_data.pop(_CTX_ADD_FIELD + ":success_hint", None)
    await show_users_list(update, context)
    return ConversationHandler.END


async def add_user_receive_id(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    value = (update.message.text or "").strip()
    field = context.user_data.get(_CTX_ADD_FIELD, "telegram_id")
    label = _platform_field_label(str(field))
    success_hint = str(context.user_data.get(_CTX_ADD_FIELD + ":success_hint") or "")

    if not value.lstrip("-").isdigit():
        await update.message.reply_text(
            f"⚠️ {label} должен быть целым числом. Попробуйте ещё раз:"
        )
        return _S_ADD_ID

    platform_id = int(value)
    actor_uid = update.effective_user.id if update.effective_user else 0
    actor_uname = update.effective_user.username if update.effective_user else None

    async with AsyncSessionLocal() as session:
        column = User.telegram_id if field == "telegram_id" else User.max_user_id
        existing = (
            await session.execute(select(User).where(column == platform_id))
        ).scalar_one_or_none()

        if existing:
            if existing.is_active:
                context.user_data.pop(_CTX_ADD_FIELD, None)
                context.user_data.pop(_CTX_ADD_FIELD + ":success_hint", None)
                await update.message.reply_text(
                    f"ℹ️ Пользователь с {label} <code>{platform_id}</code> уже активен.",
                    parse_mode=ParseMode.HTML,
                )
                return ConversationHandler.END
            else:
                # Re-activate existing inactive user
                existing.is_active = True
                await log_action(
                    session, actor_uid, actor_uname,
                    "user:reactivate", f"target={_user_ref(existing)}"
                )
                await session.commit()
                await _svc(context).reload()
                context.user_data.pop(_CTX_ADD_FIELD, None)
                context.user_data.pop(_CTX_ADD_FIELD + ":success_hint", None)
                await update.message.reply_text(
                    f"✅ Пользователь с {label} <code>{platform_id}</code> повторно активирован.",
                    parse_mode=ParseMode.HTML,
                )
                return ConversationHandler.END

        # New user
        user = User(is_active=True)
        if field == "telegram_id":
            user.telegram_id = platform_id
        else:
            user.max_user_id = platform_id
        session.add(user)
        await log_action(
            session, actor_uid, actor_uname,
            "user:add", f"target={label}:{platform_id}"
        )
        await session.commit()

    await _svc(context).reload()
    context.user_data.pop(_CTX_ADD_FIELD, None)
    context.user_data.pop(_CTX_ADD_FIELD + ":success_hint", None)
    await update.message.reply_text(
        f"✅ Пользователь с {label} <code>{platform_id}</code> добавлен.\n"
        f"{success_hint}",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def add_user_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_CTX_ADD_FIELD, None)
    context.user_data.pop(_CTX_ADD_FIELD + ":success_hint", None)
    await update.message.reply_text("❌ Добавление отменено.")
    return ConversationHandler.END


# ── ConversationHandler ────────────────────────────────────────────────────────


def add_user_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[CallbackQueryHandler(add_user_start, pattern=r"^adm:usr:add$")],
        states={
            _S_ADD_PLATFORM: [
                CallbackQueryHandler(add_user_choose_platform, pattern=r"^adm:usr:add:(tg|max)$"),
                CallbackQueryHandler(add_user_back_to_list, pattern=r"^adm:usr:list$"),
            ],
            _S_ADD_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_user_receive_id)],
        },
        fallbacks=[CommandHandler("cancel", add_user_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


# ── ConversationHandler for edit ───────────────────────────────────────────────


def edit_user_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_field_start, pattern=r"^adm:usr:edit:\d+:\w+$"),
        ],
        states={
            _S_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_field_receive)],
        },
        fallbacks=[CommandHandler("cancel", edit_field_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


# ── Handler list ───────────────────────────────────────────────────────────────


def get_user_handlers() -> list:
    return [
        add_user_conversation(),
        edit_user_conversation(),
        CallbackQueryHandler(show_users_list, pattern=r"^adm:usr:list$"),
        CallbackQueryHandler(show_user_card, pattern=r"^adm:usr:card:\d+$"),
        CallbackQueryHandler(show_user_edit_menu, pattern=r"^adm:usr:edit:\d+$"),
        CallbackQueryHandler(toggle_user, pattern=r"^adm:usr:toggle:\d+$"),
    ]
