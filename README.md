# tbssa

`tbssa` — бот для мониторинга и администрирования Windows-серверов по SSH с двумя transport-слоями: Telegram и MAX.

Проект уже работает не как “минимальный Telegram-бот”, а как единый operational-контур с общей базой данных, журналом, настройками, уведомлениями и admin UI в обоих мессенджерах.

## Что умеет сейчас

Публичные сценарии:

- `/start`
- `/my`

Административные сценарии:

- `/admin`
- `/status`
- `/reboot`
- `/sos`
- admin broadcast
- мониторинговые алерты
- журнал действий
- управление серверами
- управление пользователями
- изменение runtime-настроек без перезапуска

Критичный паритет MAX относительно Telegram достигнут.

## Быстрый старт

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
cp tbssa.env.example .env
chmod 600 .env
alembic upgrade head
tbssa-seed
python -m tbssa
```

Минимум для первого запуска:

- `TELEGRAM_BOT_TOKEN`
- `ADMIN_IDS` и/или `MAX_ADMIN_IDS`
- SSH/ping bootstrap-поля в `.env`

`MAX_BOT_TOKEN` включает MAX runtime. Для production рекомендуется заполнить `MAX_WEBHOOK_URL`, чтобы MAX работал через webhook вместо long polling. После первичного bootstrap рабочая конфигурация редактируется уже через admin UI и хранится в базе данных.

## Карта документации

- [Документация](docs/README.md)
- [Локальный запуск](docs/setup.md)
- [Развёртывание](docs/deployment.md)
- [Чек-лист ручного тестирования](docs/manual-test-checklist.md)
- [Roadmap стабилизации](docs/stability-roadmap.md)
- [Безопасность](SECURITY.md)
- [OpenSSH на Windows](SSH_SETUP.md)
- [NetBird](NETBIRD_SETUP.md)
- [История MAX parity](docs/history/max-parity-roadmap.md)
- [История Telegram admin UI](docs/history/telegram-admin-roadmap.md)

## Структура проекта

```text
tbssa/
├── src/tbssa/          # runtime, Telegram/MAX handlers, shared business logic
├── tests/              # точечные unit-тесты
├── alembic/            # миграции БД
├── docs/               # актуальная проектная документация
├── deploy/systemd/     # шаблон systemd unit
├── bot.py              # совместимый entrypoint
├── pyproject.toml
├── requirements.txt
└── tbssa.env.example
```

## Развёртывание

Канонический шаблон `systemd`-unit находится в `deploy/systemd/tbssa.service`.

Локальный unit установлен вне репозитория: `/etc/systemd/system/tbssa.service`. В проекте хранится только шаблон без host-specific значений.

## Безопасность

- Секреты хранятся только в `.env`.
- Проверка SSH host key обязательна: `known_hosts` или `SSH_HOST_KEY_FINGERPRINT`.
- Административный доступ работает по fail-closed модели.
- Опасные операции требуют явного подтверждения.

## Лицензия

MIT
