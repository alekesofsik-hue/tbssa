from __future__ import annotations

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes, MessageHandler, filters

from tbssa.admin.audit import log_action
from tbssa.admin.guard import _svc
from tbssa.admin.users import sync_admin_from_telegram
from tbssa.db.engine import AsyncSessionLocal


async def broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Любой текст от администратора мгновенно рассылается всем администраторам.
    Не срабатывает, когда админ участвует в активном диалоге (настройки, серверы и т.д.)
    """
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    uid = update.effective_user.id if update.effective_user else 0
    svc = _svc(context)

    if text and text.lower() in ("sos", "сос"):
        if svc.is_ready() and svc.is_admin(uid):
            from tbssa.handlers import _trigger_sos_from_message
            await _trigger_sos_from_message(update, context)
        return

    uname = update.effective_user.username if update.effective_user else None

    if not svc.is_ready() or not svc.is_admin(uid):
        return

    u = update.effective_user
    if u:
        await sync_admin_from_telegram(uid, u.username, u.first_name)

    if not text:
        return

    who = f"@{uname}" if uname else f"id:{uid}"
    full_text = f"📢 <b>Сообщение от {who}:</b>\n\n{text}"

    sent = 0
    failed = 0
    for admin_id in svc.get_admin_ids():
        try:
            await context.bot.send_message(admin_id, full_text, parse_mode=ParseMode.HTML)
            sent += 1
        except Exception:
            failed += 1

    async with AsyncSessionLocal() as session:
        await log_action(session, uid, uname, "broadcast", f"sent={sent} failed={failed}")
        await session.commit()


# Регистрировать ПОСЛЕ всех ConversationHandler, чтобы диалоги (настройки, CRUD) имели приоритет
def get_broadcast_handlers() -> list:
    return [
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            broadcast_message,
        ),
    ]
