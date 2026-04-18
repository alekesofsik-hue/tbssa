from __future__ import annotations

from sqlalchemy import desc, func, select
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import CallbackQueryHandler, ContextTypes

from tbssa.audit_view import format_audit_row
from tbssa.admin.menu import ADM_HOME
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import AuditLog

# ── Constants ──────────────────────────────────────────────────────────────────
_PAGE_SIZE = 10

_JRN_PAGE = "adm:jrn:page:{offset}"  # show page starting at offset


# ── Keyboards ─────────────────────────────────────────────────────────────────


def _journal_keyboard(offset: int, total: int) -> InlineKeyboardMarkup:
    nav = []
    if offset > 0:
        prev = max(0, offset - _PAGE_SIZE)
        nav.append(InlineKeyboardButton("◀️ Назад", callback_data=_JRN_PAGE.format(offset=prev)))
    if offset + _PAGE_SIZE < total:
        nxt = offset + _PAGE_SIZE
        nav.append(InlineKeyboardButton("Следующие 10 ▶", callback_data=_JRN_PAGE.format(offset=nxt)))

    rows = []
    if nav:
        rows.append(nav)
    rows.append([InlineKeyboardButton("◀️ Главное меню", callback_data=ADM_HOME)])
    return InlineKeyboardMarkup(rows)


# ── Page loader ────────────────────────────────────────────────────────────────


async def _render_page(offset: int) -> tuple[str, int]:
    """Return (formatted text, total record count)."""
    async with AsyncSessionLocal() as session:
        total: int = (
            await session.execute(select(func.count()).select_from(AuditLog))
        ).scalar_one()

        entries = (
            await session.execute(
                select(AuditLog)
                .order_by(desc(AuditLog.created_at))
                .offset(offset)
                .limit(_PAGE_SIZE)
            )
        ).scalars().all()

    if not entries:
        return "📋 <b>Журнал пуст.</b>", 0

    page_num = offset // _PAGE_SIZE + 1
    page_max = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    header = f"📋 <b>Журнал действий</b>  <i>(стр. {page_num}/{page_max}, всего: {total})</i>\n"
    rows = [header, "─" * 32]
    for entry in entries:
        rows.append(format_audit_row(entry))
    return "\n".join(rows), total


# ── Handlers ───────────────────────────────────────────────────────────────────


async def show_journal(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point: called from admin_callback when ADM_AUDIT is pressed."""
    query = update.callback_query
    text, total = await _render_page(0)
    await query.edit_message_text(
        text,
        reply_markup=_journal_keyboard(0, total),
        parse_mode=ParseMode.HTML,
    )


async def journal_page(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Pagination: called when user presses ◀ / ▶ buttons."""
    query = update.callback_query
    await query.answer()
    offset = int(query.data.split(":")[3])
    text, total = await _render_page(offset)
    await query.edit_message_text(
        text,
        reply_markup=_journal_keyboard(offset, total),
        parse_mode=ParseMode.HTML,
    )


# ── Handler list ───────────────────────────────────────────────────────────────


def get_journal_handlers() -> list:
    return [
        CallbackQueryHandler(journal_page, pattern=r"^adm:jrn:page:\d+$"),
    ]
