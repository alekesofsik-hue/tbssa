from __future__ import annotations

BUTTON_SERVERS = "🖥 Серверы"
BUTTON_USERS = "👥 Пользователи"
BUTTON_SETTINGS = "⚙️ Настройки"
BUTTON_AUDIT = "📋 Журнал"
BUTTON_BACK = "◀️ Назад"
BUTTON_MAIN_MENU = "◀️ Главное меню"
BUTTON_CONFIRM = "✅ Подтвердить"
BUTTON_CANCEL = "❌ Отмена"
BUTTON_NEXT_10 = "Следующие 10 ▶"

ADMIN_HOME_TEXT = "🛠 <b>Панель управления</b>\n\nВыберите раздел:"
ADMIN_QUICK_ACTIONS_TEXT = "🛠 Быстрое управление"
ADMIN_ACCESS_DENIED_TEXT = "⛔ Доступ запрещён."
BOT_INITIALIZING_TEXT = "⛔ Бот инициализируется. Повторите попытку через несколько секунд."
SESSION_EXPIRED_TEXT = "⏱ Сессия истекла. Откройте меню заново: /admin"
UI_ACTION_FAILED_TEXT = "⚠️ Не удалось обработать действие. Повторите попытку."


def my_id_text(user_id: int) -> str:
    return (
        f"Ваш ID: <code>{user_id}</code>\n\n"
        "Если вам нужен доступ, передайте этот ID владельцу бота."
    )


def sos_confirm_text(label: str) -> str:
    return (
        f"⚠️ <b>Подтвердите выполнение команды {label}.</b>\n\n"
        "Все активные серверы будут немедленно выключены."
    )
