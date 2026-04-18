from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from tbssa.ui_text import (
    ADMIN_HOME_TEXT,
    BUTTON_AUDIT,
    BUTTON_MAIN_MENU,
    BUTTON_SERVERS,
    BUTTON_SETTINGS,
    BUTTON_USERS,
)

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
                InlineKeyboardButton(BUTTON_SERVERS, callback_data=ADM_SERVERS),
                InlineKeyboardButton(BUTTON_USERS, callback_data=ADM_USERS),
            ],
            [
                InlineKeyboardButton(BUTTON_SETTINGS, callback_data=ADM_SETTINGS),
                InlineKeyboardButton(BUTTON_AUDIT, callback_data=ADM_AUDIT),
            ],
        ]
    )


def back_to_main_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(BUTTON_MAIN_MENU, callback_data=ADM_HOME)]]
    )


MAIN_MENU_TEXT = ADMIN_HOME_TEXT
