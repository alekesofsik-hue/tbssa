from __future__ import annotations

from functools import wraps
from typing import Awaitable, Callable, TypeVar

from telegram import Update
from telegram.ext import ContextTypes

from tbssa.config_service import ConfigService
from tbssa.ui_text import ADMIN_ACCESS_DENIED_TEXT, BOT_INITIALIZING_TEXT

T = TypeVar("T")


def _svc(context: ContextTypes.DEFAULT_TYPE) -> ConfigService:
    return context.bot_data["config_service"]


def admin_required(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
    """
    Decorator for admin handlers.
    Silently denies access if the user is not in config_service admin list (fail-closed).
    No error is logged to console on denial — security by design.
    """

    @wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> T | None:
        uid = update.effective_user.id if update.effective_user else None
        svc = _svc(context)

        if not svc.is_ready():
            if update.effective_chat:
                await update.effective_chat.send_message(BOT_INITIALIZING_TEXT)
            return None

        if not svc.is_admin(uid):
            if update.effective_chat:
                await update.effective_chat.send_message(ADMIN_ACCESS_DENIED_TEXT)
            return None

        return await func(update, context)

    return wrapper
