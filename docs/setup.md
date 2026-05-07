# Локальный запуск

## Что нужно заранее

- Python `3.12+`
- SSH-доступ к Windows-серверам
- Telegram bot token
- MAX bot token, если нужен второй транспорт

## Установка

Рекомендуемый вариант:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

## Переменные окружения

1. Создайте `.env`:

```bash
cp tbssa.env.example .env
chmod 600 .env
```

2. Заполните минимум:

- `TELEGRAM_BOT_TOKEN` — обязателен для запуска Telegram-части.
- `MAX_BOT_TOKEN` — включает MAX runtime.
- `MAX_WEBHOOK_URL` — включает webhook-режим MAX в production. Если оставить пустым, MAX использует fallback на long polling.
- `ADMIN_IDS` и/или `MAX_ADMIN_IDS` — только для первичного bootstrap.
- `SSH_HOST`, `PING_HOST` и SSH-параметры — только для первичного seed.

После bootstrap основная конфигурация хранится в базе данных и редактируется через admin UI.

Для webhook-режима также доступны:

- `MAX_WEBHOOK_SECRET` — общий секрет для проверки заголовка `X-Max-Bot-Api-Secret`;
- `MAX_WEBHOOK_BIND_HOST` и `MAX_WEBHOOK_BIND_PORT` — локальный HTTP listener, на который должен проксировать внешний HTTPS endpoint;
- `MAX_WEBHOOK_SYNC_INTERVAL_SECONDS` — период повторной проверки подписки в MAX API.

## База данных и миграции

Перед первым запуском примените миграции:

```bash
alembic upgrade head
```

По умолчанию используется SQLite. При необходимости можно переопределить путь через `DB_URL`.

## Bootstrap начальных данных

Первичное заполнение:

```bash
tbssa-seed
```

Скрипт:

- создаёт начальных администраторов из `ADMIN_IDS` и `MAX_ADMIN_IDS`;
- добавляет стартовый сервер из `.env`, если заполнены SSH/ping параметры;
- не должен использоваться как постоянный источник конфигурации после запуска проекта.

## Запуск

```bash
python -m tbssa
```

Или после установки:

```bash
tbssa
```

## Быстрая проверка

1. Откройте Telegram и выполните `/start`, `/my`, `/admin`.
2. Если включён MAX, повторите `/start`, `/my`, `/admin` там.
3. Убедитесь, что открываются разделы `Серверы`, `Пользователи`, `Настройки`, `Журнал`.
4. Проверьте, что изменения в настройках применяются без перезапуска.
