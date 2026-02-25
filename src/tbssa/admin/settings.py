from __future__ import annotations

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
from tbssa.admin.menu import ADM_HOME
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import Config

# ── Metadata for numeric configurable keys ────────────────────────────────────
# Each entry: (label, description, min_value, max_value)
_KEYS: dict[str, tuple[str, str, int, int]] = {
    "CONFIRM_TTL_SECONDS": (
        "Подтверждение (сек.)",
        "Время жизни запроса на подтверждение опасной команды",
        10,
        600,
    ),
    "PING_COUNT": (
        "Ping: число пакетов",
        "Количество ICMP-пакетов при проверке доступности",
        1,
        20,
    ),
    "PING_TIMEOUT": (
        "Ping: таймаут (сек.)",
        "Таймаут ожидания каждого пакета",
        1,
        30,
    ),
    "SSH_CONNECT_TIMEOUT": (
        "SSH: таймаут подключения (сек.)",
        "Время ожидания установки SSH-соединения",
        3,
        60,
    ),
    "SSH_COMMAND_TIMEOUT": (
        "SSH: таймаут команды (сек.)",
        "Время ожидания выполнения команды по SSH",
        5,
        120,
    ),
    "PING_CHECK_INTERVAL_MINUTES": (
        "Мониторинг: интервал (мин.)",
        "Как часто бот автоматически проверяет доступность серверов (требуется перезапуск)",
        1,
        60,
    ),
    "REACHABILITY_ALERT_COOLDOWN_MINUTES": (
        "Мониторинг: антиспам (мин.)",
        "Минимальный интервал между повторными уведомлениями о недоступном сервере",
        5,
        1440,
    ),
    "SOS_REQUIRE_CONFIRM": (
        "SOS: подтверждение (0/1)",
        "1 — требовать подтверждение перед выполнением SOS; 0 — выполнять немедленно",
        0,
        1,
    ),
}

# ── Metadata for text configurable keys ───────────────────────────────────────
# Each entry: (label, description, max_length)
_TEXT_KEYS: dict[str, tuple[str, str, int]] = {
    "SOS_BUTTON_LABEL": (
        "SOS: текст кнопки",
        "Надпись на красной кнопке в меню /start (только для администраторов)",
        20,
    ),
    "SOS_MSG_HEADER": (
        "SOS: заголовок отчёта",
        "Заголовок сообщения, отправляемого админам при выполнении SOS",
        64,
    ),
    "SSH_DEFAULT_USER": (
        "SSH: пользователь по умолчанию",
        "Имя пользователя для подключения по SSH ко всем серверам",
        64,
    ),
    "SSH_DEFAULT_KEY_PATH": (
        "SSH: путь к ключу",
        "Путь к приватному SSH-ключу для подключения ко всем серверам",
        512,
    ),
    "SSH_CMD_POWEROFF": (
        "Команда: выключение",
        "Команда PowerShell для жёсткого выключения сервера (отправляется по SSH)",
        128,
    ),
    "SSH_CMD_REBOOT": (
        "Команда: перезагрузка",
        "Команда PowerShell для перезагрузки сервера (отправляется по SSH)",
        128,
    ),
    "PING_CMD_TEMPLATE": (
        "Проверка статуса: шаблон команды",
        "Шаблон: {timeout} (сек), {host}. Пример: ping -c 1 -n -w {timeout} {host}",
        256,
    ),
}

_DEFAULTS: dict[str, str] = {
    "CONFIRM_TTL_SECONDS": "60",
    "PING_COUNT": "3",
    "PING_TIMEOUT": "1",
    "PING_CHECK_INTERVAL_MINUTES": "5",
    "REACHABILITY_ALERT_COOLDOWN_MINUTES": "60",
    "SSH_CONNECT_TIMEOUT": "8",
    "SSH_COMMAND_TIMEOUT": "15",
    "SOS_REQUIRE_CONFIRM": "0",
    "SOS_BUTTON_LABEL": "SOS",
    "SOS_MSG_HEADER": "SOS выполнен",
    "SSH_DEFAULT_USER": "bot-admin",
    "SSH_DEFAULT_KEY_PATH": "~/.ssh/id_ed25519_bot",
    "SSH_CMD_POWEROFF": "shutdown /p /f",
    "SSH_CMD_REBOOT": "shutdown /r /t 0 /f",
    "PING_CMD_TEMPLATE": "ping -c 1 -n -w {timeout} {host}",
}

# ── callback_data constants ────────────────────────────────────────────────────
_CFG_LIST = "adm:cfg:list"
_CFG_EDIT = "adm:cfg:edit"
_CFG_EDIT_KEY = "adm:cfg:key:{key}"

# ── FSM states ────────────────────────────────────────────────────────────────
_S_VALUE = 0
_CTX_KEY = "adm:cfg:edit_key"
_CTX_IS_TEXT = "adm:cfg:is_text"


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _load_config(session) -> dict[str, str]:
    """Load current values from DB, falling back to defaults."""
    rows = (await session.execute(select(Config))).scalars().all()
    result = dict(_DEFAULTS)
    for row in rows:
        if row.key in result:
            result[row.key] = row.value
    return result


# ── Keyboards ─────────────────────────────────────────────────────────────────


def _settings_view_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✏️ Изменить", callback_data=_CFG_EDIT)],
        [InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)],
    ])


def _edit_keys_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(_KEYS[k][0], callback_data=_CFG_EDIT_KEY.format(key=k))]
        for k in _KEYS
    ]
    for k in _TEXT_KEYS:
        rows.append([InlineKeyboardButton(_TEXT_KEYS[k][0], callback_data=_CFG_EDIT_KEY.format(key=k))])
    rows.append([InlineKeyboardButton("◀️ Назад", callback_data=_CFG_LIST)])
    return InlineKeyboardMarkup(rows)


# ── Settings view ──────────────────────────────────────────────────────────────


async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    async with AsyncSessionLocal() as session:
        cfg = await _load_config(session)

    lines = ["⚙️ <b>Глобальные настройки</b>\n"]
    for key, (label, _, _, _) in _KEYS.items():
        val = cfg.get(key, _DEFAULTS[key])
        lines.append(f"<code>{label:<30}</code> <b>{val}</b>")
    for key, (label, _, _) in _TEXT_KEYS.items():
        val = cfg.get(key, _DEFAULTS[key])
        lines.append(f"<code>{label:<30}</code> <b>{val}</b>")

    await query.edit_message_text(
        "\n".join(lines),
        reply_markup=_settings_view_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# ── Edit menu ──────────────────────────────────────────────────────────────────


async def show_edit_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "⚙️ <b>Выберите параметр для изменения:</b>",
        reply_markup=_edit_keys_keyboard(),
        parse_mode=ParseMode.HTML,
    )


# ── Edit value (ConversationHandler) ──────────────────────────────────────────


async def edit_key_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()

    key = query.data.split(":", 3)[3]  # adm:cfg:key:{key}

    if key in _TEXT_KEYS:
        label, description, max_len = _TEXT_KEYS[key]
        async with AsyncSessionLocal() as session:
            cfg = await _load_config(session)
        current = cfg.get(key, _DEFAULTS.get(key, ""))
        context.user_data[_CTX_KEY] = key
        context.user_data[_CTX_IS_TEXT] = True
        await query.edit_message_text(
            f"✏️ <b>{label}</b>\n\n"
            f"<i>{description}</i>\n\n"
            f"Текущее значение: <code>{current}</code>\n"
            f"Максимум символов: <b>{max_len}</b>\n\n"
            "Введите новое значение или /cancel для отмены.",
            parse_mode=ParseMode.HTML,
        )
        return _S_VALUE

    if key not in _KEYS:
        await query.edit_message_text("⚠️ Неизвестный параметр.")
        return ConversationHandler.END

    label, description, min_val, max_val = _KEYS[key]
    async with AsyncSessionLocal() as session:
        cfg = await _load_config(session)
    current = cfg.get(key, _DEFAULTS[key])

    context.user_data[_CTX_KEY] = key
    context.user_data[_CTX_IS_TEXT] = False
    await query.edit_message_text(
        f"✏️ <b>{label}</b>\n\n"
        f"<i>{description}</i>\n\n"
        f"Текущее значение: <code>{current}</code>\n"
        f"Допустимый диапазон: <b>{min_val} – {max_val}</b>\n\n"
        "Введите новое значение или /cancel для отмены.",
        parse_mode=ParseMode.HTML,
    )
    return _S_VALUE


async def edit_key_receive(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    key = context.user_data.get(_CTX_KEY)
    is_text = context.user_data.get(_CTX_IS_TEXT, False)

    actor_uid = update.effective_user.id if update.effective_user else 0
    actor_uname = update.effective_user.username if update.effective_user else None
    raw = (update.message.text or "").strip()

    if is_text:
        if key not in _TEXT_KEYS:
            await update.message.reply_text("⚠️ Сессия устарела. Откройте настройки заново.")
            return ConversationHandler.END
        label, _, max_len = _TEXT_KEYS[key]
        if not raw or len(raw) > max_len:
            await update.message.reply_text(
                f"⚠️ Значение не должно быть пустым и длиннее {max_len} символов. Попробуйте ещё раз:"
            )
            return _S_VALUE
        saved_value = raw
    else:
        if not key or key not in _KEYS:
            await update.message.reply_text("⚠️ Сессия устарела. Откройте настройки заново.")
            return ConversationHandler.END
        label, _, min_val, max_val = _KEYS[key]
        if not raw.lstrip("-").isdigit():
            await update.message.reply_text(
                f"⚠️ Значение должно быть целым числом ({min_val}–{max_val}). Попробуйте ещё раз:"
            )
            return _S_VALUE
        value = int(raw)
        if not (min_val <= value <= max_val):
            await update.message.reply_text(
                f"⚠️ Значение должно быть в диапазоне {min_val}–{max_val}. Попробуйте ещё раз:"
            )
            return _S_VALUE
        saved_value = str(value)

    async with AsyncSessionLocal() as session:
        row = await session.get(Config, key)
        if row:
            row.value = saved_value
        else:
            session.add(Config(key=key, value=saved_value))
        await log_action(
            session, actor_uid, actor_uname,
            "config:set", f"{key}={saved_value}"
        )
        await session.commit()

    await _svc(context).reload()
    context.user_data.pop(_CTX_KEY, None)
    context.user_data.pop(_CTX_IS_TEXT, None)

    await update.message.reply_text(
        f"✅ <b>{label}</b> установлен в <code>{saved_value}</code>.\n"
        "Изменение применяется немедленно.",
        parse_mode=ParseMode.HTML,
    )
    return ConversationHandler.END


async def edit_key_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    context.user_data.pop(_CTX_KEY, None)
    context.user_data.pop(_CTX_IS_TEXT, None)
    await update.message.reply_text("❌ Редактирование отменено.")
    return ConversationHandler.END


# ── ConversationHandler ────────────────────────────────────────────────────────


def edit_config_conversation() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CallbackQueryHandler(edit_key_start, pattern=r"^adm:cfg:key:.+$"),
        ],
        states={
            _S_VALUE: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, edit_key_receive),
            ],
        },
        fallbacks=[CommandHandler("cancel", edit_key_cancel)],
        per_user=True,
        per_chat=True,
        per_message=False,
    )


# ── Handler list ───────────────────────────────────────────────────────────────


def get_settings_handlers() -> list:
    return [
        edit_config_conversation(),
        CallbackQueryHandler(show_settings, pattern=r"^adm:cfg:list$"),
        CallbackQueryHandler(show_edit_menu, pattern=r"^adm:cfg:edit$"),
    ]
