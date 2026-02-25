from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# All admin callback_data is namespaced with "adm:" to avoid collisions.

# ── Top-level sections ────────────────────────────────────────────────────────
ADM_SERVERS = "adm:servers"
ADM_USERS = "adm:users"
ADM_SETTINGS = "adm:settings"
ADM_AUDIT = "adm:audit"
ADM_HOME = "adm:home"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("🖥 Серверы", callback_data=ADM_SERVERS),
                InlineKeyboardButton("👥 Пользователи", callback_data=ADM_USERS),
            ],
            [
                InlineKeyboardButton("⚙️ Настройки", callback_data=ADM_SETTINGS),
                InlineKeyboardButton("📋 Журнал", callback_data=ADM_AUDIT),
            ],
        ]
    )


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)]]
    )


MAIN_MENU_TEXT = "🛠 <b>Панель управления</b>\n\nВыберите раздел:"
