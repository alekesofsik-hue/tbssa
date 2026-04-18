# Развёртывание

## Базовая схема

Production-запуск проекта рассчитан на один процесс, в котором:

- Telegram runtime работает как основной transport;
- MAX runtime поднимается параллельно, если задан `MAX_BOT_TOKEN`;
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

Файл `tbssa.service` в корне проекта считается локальным, host-specific вариантом. Используйте его только как пример уже настроенного окружения, а не как переносимый шаблон.

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
