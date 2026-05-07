from __future__ import annotations

from collections.abc import Iterable
from typing import Any

# Each numeric entry: (label, description, min_value, max_value)
CONFIG_NUMERIC_KEYS: dict[str, tuple[str, str, int, int]] = {
    "CONFIRM_TTL_SECONDS": (
        "Подтверждение (сек.)",
        "Время жизни запроса на подтверждение опасной команды",
        10,
        600,
    ),
    "PING_COUNT": (
        "ICMP: число пакетов",
        "Количество ICMP-пакетов при вспомогательной диагностике",
        1,
        20,
    ),
    "PING_TIMEOUT": (
        "ICMP: таймаут (сек.)",
        "Таймаут ожидания каждого ICMP-пакета",
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
        "Как часто бот автоматически проверяет SSH-доступность серверов (требуется перезапуск)",
        1,
        60,
    ),
    "REACHABILITY_ALERT_COOLDOWN_MINUTES": (
        "Мониторинг: антиспам (мин.)",
        "Минимальный интервал между повторными уведомлениями о недоступности SSH",
        5,
        1440,
    ),
    "OFFLINE_CONFIRM_DELAY1_MINUTES": (
        "SSH недоступен: задержка 1 (мин.)",
        "Через сколько минут после первичного SSH-fail запускается 1-я подтверждающая SSH-проверка",
        1,
        30,
    ),
    "OFFLINE_CONFIRM_DELAY2_MINUTES": (
        "SSH недоступен: задержка 2 (мин.)",
        "Через сколько минут после 1-й подтверждающей SSH-проверки запускается 2-я SSH-проверка",
        1,
        30,
    ),
    "ONLINE_CONFIRM_DELAY1_MINUTES": (
        "SSH доступен: задержка 1 (мин.)",
        "Через сколько минут после первичного SSH-ok запускается 1-я подтверждающая SSH-проверка",
        1,
        30,
    ),
    "ONLINE_CONFIRM_DELAY2_MINUTES": (
        "SSH доступен: задержка 2 (мин.)",
        "Через сколько минут после 1-й подтверждающей SSH-проверки запускается 2-я SSH-проверка",
        1,
        30,
    ),
    "SOS_REQUIRE_CONFIRM": (
        "SOS: подтверждение (0/1)",
        "1 — требовать подтверждение перед выполнением SOS; 0 — выполнять немедленно",
        0,
        1,
    ),
}

# Each text entry: (label, description, max_length)
CONFIG_TEXT_KEYS: dict[str, tuple[str, str, int]] = {
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
        "ICMP: шаблон команды",
        "Шаблон: {timeout} (сек), {host}. Используется только для вспомогательной ICMP-диагностики",
        256,
    ),
}

CONFIG_DEFAULTS: dict[str, str] = {
    "CONFIRM_TTL_SECONDS": "60",
    "PING_COUNT": "3",
    "PING_TIMEOUT": "1",
    "SSH_CONNECT_TIMEOUT": "8",
    "SSH_COMMAND_TIMEOUT": "15",
    "SSH_DEFAULT_USER": "bot-admin",
    "SSH_DEFAULT_KEY_PATH": "~/.ssh/id_ed25519_bot",
    "SSH_CMD_POWEROFF": "shutdown /p /f",
    "SSH_CMD_REBOOT": "shutdown /r /t 0 /f",
    "PING_CMD_TEMPLATE": "ping -c 1 -n -w {timeout} {host}",
    "PING_CHECK_INTERVAL_MINUTES": "5",
    "REACHABILITY_ALERT_COOLDOWN_MINUTES": "60",
    "OFFLINE_CONFIRM_DELAY1_MINUTES": "3",
    "OFFLINE_CONFIRM_DELAY2_MINUTES": "2",
    "ONLINE_CONFIRM_DELAY1_MINUTES": "3",
    "ONLINE_CONFIRM_DELAY2_MINUTES": "2",
    "SOS_BUTTON_LABEL": "SOS",
    "SOS_REQUIRE_CONFIRM": "0",
    "SOS_MSG_HEADER": "SOS выполнен",
}


def merged_config_values(rows: Iterable[Any]) -> dict[str, str]:
    result = dict(CONFIG_DEFAULTS)
    for row in rows:
        key = getattr(row, "key", None)
        value = getattr(row, "value", None)
        if key:
            result[key] = value
    return result
