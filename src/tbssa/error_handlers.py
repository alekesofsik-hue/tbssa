from __future__ import annotations

import logging

from telegram import Update
from telegram.error import BadRequest
from telegram.ext import ContextTypes

from tbssa.ui_text import UI_ACTION_FAILED_TEXT

log = logging.getLogger("tbssa")

TELEGRAM_UI_ERROR_TEXT = UI_ACTION_FAILED_TEXT
MAX_UI_ERROR_TEXT = UI_ACTION_FAILED_TEXT


def is_ignorable_telegram_error(exc: Exception) -> bool:
    if not isinstance(exc, BadRequest):
        return False
    text = str(exc).lower()
    return "message is not modified" in text


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    exc = context.error
    if exc is None:
        return

    if is_ignorable_telegram_error(exc):
        log.debug("[telegram] ignored UI error: %s", exc)
        return

    log.exception("[telegram] unhandled update error: %s", exc)

    if not isinstance(update, Update):
        return

    try:
        if update.callback_query is not None:
            await update.callback_query.answer(TELEGRAM_UI_ERROR_TEXT, show_alert=True)
        elif update.effective_chat is not None:
            await update.effective_chat.send_message(TELEGRAM_UI_ERROR_TEXT)
    except Exception as notify_exc:  # pragma: no cover - best effort path
        log.debug("[telegram] failed to notify user about UI error: %s", notify_exc)
