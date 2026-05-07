# Развёртывание

## Базовая схема

Production-запуск проекта рассчитан на один процесс, в котором:

- Telegram runtime работает как основной transport;
- MAX runtime поднимается параллельно, если задан `MAX_BOT_TOKEN`;
- при заданном `MAX_WEBHOOK_URL` MAX runtime работает через webhook, иначе использует fallback на long polling;
- конфигурация и администраторы читаются из базы данных;
- мониторинг серверов работает через job queue.

## Подготовка

1. Разверните проект на хосте.
2. Создайте `.env` и ограничьте доступ:

```bash
cp tbssa.env.example .env
chmod 600 .env
```

3. Примените миграции:

```bash
alembic upgrade head
```

4. При первом запуске выполните bootstrap:

```bash
tbssa-seed
```

5. Для production webhook в MAX подготовьте публичный HTTPS endpoint:

- заполните `MAX_WEBHOOK_URL`, например `https://bot.example.com/max/webhook`;
- при желании задайте `MAX_WEBHOOK_SECRET`;
- поднимите reverse proxy на `443`, который проксирует этот путь на локальный listener `MAX_WEBHOOK_BIND_HOST:MAX_WEBHOOK_BIND_PORT`.

Пример для nginx:

```nginx
location /max/webhook {
    proxy_pass http://127.0.0.1:8081/max/webhook;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto https;
}
```

После старта `tbssa` бот сам зарегистрирует webhook через `POST /subscriptions`.

## systemd

Канонический шаблон unit-файла лежит в `deploy/systemd/tbssa.service`.

Порядок установки:

```bash
sudo cp deploy/systemd/tbssa.service /etc/systemd/system/tbssa.service
sudo systemctl daemon-reload
sudo systemctl enable --now tbssa
sudo systemctl status tbssa
```

Перед установкой обязательно отредактируйте:

- `User`
- `WorkingDirectory`
- `EnvironmentFile`
- `ExecStart`
- `ReadOnlyPaths`

Фактический unit рабочего сервиса установлен вне репозитория: `/etc/systemd/system/tbssa.service`. В проекте хранится только переносимый шаблон `deploy/systemd/tbssa.service`.

## Логи и обслуживание

Основные команды:

```bash
sudo systemctl restart tbssa
sudo systemctl status tbssa
journalctl -u tbssa -f
```

## Обновление проекта

После обновления кода:

```bash
. .venv/bin/activate
pip install -e .
alembic upgrade head
sudo systemctl restart tbssa
```

Если менялась только конфигурация через admin UI, перезапуск обычно не требуется.

Если вы временно убираете `MAX_WEBHOOK_URL` и возвращаетесь к polling, убедитесь, что старые webhook-подписки в MAX удалены, иначе события могут продолжать уходить только в webhook.
