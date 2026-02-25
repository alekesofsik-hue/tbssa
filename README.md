# tbssa — Telegram Bot Server Supervisor Assistant

Мини-бот для мониторинга и управления Windows-сервером по SSH (например через Tailscale).

## Возможности

| Команда  | Описание                                   |
|----------|--------------------------------------------|
| `/start` | Приветствие и список команд                |
| `/my`    | Показать свой ID (для сообщения владельцу) |
| `/status`| Проверка доступности сервера (ICMP ping)   |
| `/reboot`| Перезагрузка сервера (с подтверждением)    |
| `/sos`   | Жёсткое выключение (с подтверждением)      |

Админ-команды (`/status`, `/reboot`, `/sos`) доступны только пользователям из `ADMIN_IDS`.

## Требования

- Python 3.12+
- SSH-доступ к Windows (OpenSSH, например через Tailscale)
- `known_hosts` или pinning fingerprint для проверки host key

## Быстрый старт

### 1. Установка

**Через pip (системный или пользовательский):**

```bash
cd /path/to/tbssa
pip install -e .
```

**Через pipx (без venv, изолированно):**

```bash
pip install --user pipx && pipx ensurepath
pipx install -e /path/to/tbssa
```

### 2. Конфигурация

```bash
cp tbssa.env.example .env
chmod 600 .env
# Отредактируйте .env — TELEGRAM_BOT_TOKEN, ADMIN_IDS, SSH_HOST, PING_HOST и т.д.
```

### 3. SSH host key

Добавьте хост в `known_hosts`:

```bash
ssh-keyscan -H <SSH_HOST> >> ~/.ssh/known_hosts
```

Либо укажите `SSH_HOST_KEY_FINGERPRINT` в `.env` (MD5, формат `aa:bb:cc:...`).

### 4. Запуск

```bash
python3 -m tbssa
```

Или после установки: `tbssa`

## Деплой через systemd

1. Отредактируйте `tbssa.service` под своё окружение (User, WorkingDirectory, EnvironmentFile, ReadOnlyPaths).

2. Установите unit:

```bash
sudo cp tbssa.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tbssa
sudo systemctl status tbssa
```

## Архитектура

```
tbssa/
├── bot.py              # Совместимый entrypoint (python bot.py)
├── src/tbssa/
│   ├── __main__.py     # Точка входа python -m tbssa
│   ├── app.py          # Сборка Telegram Application
│   ├── handlers.py     # Обработчики команд
│   ├── ssh.py          # SSH + PowerShell, host key policy
│   ├── ping.py         # ICMP ping
│   ├── settings.py     # Конфигурация (pydantic-settings)
│   └── logging_setup.py
├── tests/
├── deploy/systemd/     # Шаблон systemd unit
├── pyproject.toml
├── requirements.txt
└── tbssa.env.example
```

## Безопасность

- **ADMIN_IDS обязателен.** Если пуст — админ-команды запрещены (fail-closed).
- Проверка SSH host key: `known_hosts` или `SSH_HOST_KEY_FINGERPRINT`.
- Опасные команды требуют подтверждения кодом.
- Не коммитьте `.env` — он в `.gitignore`.

## Лицензия

MIT
