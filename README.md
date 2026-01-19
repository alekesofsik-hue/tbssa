## tbssa — Telegram Bot Server Supervisor Assistant

Мини-бот для мониторинга и управления Windows-сервером:
- `/status` — проверка доступности через ICMP ping с VPS
- `/reboot` — перезагрузка (с подтверждением)
- `/sos` — жёсткое выключение (с подтверждением)
- `/me` — показать `chat_id`/`user_id`

### Требования

- Python **3.12+**
- Доступ по SSH до Windows (например через Tailscale)
- Настроенный `known_hosts` (или pinned fingerprint)

### Быстрый старт (локально/на VPS)

Установка **без виртуального окружения** (рекомендуется `pipx`, чтобы не засорять системный Python):

```bash
cd /home/ezovskikh_a/apps/tbssa
python3 -m pip install --user pipx
python3 -m pipx ensurepath
pipx install -e .
```

Создать `.env`:

```bash
cp tbssa.env.example .env
chmod 600 .env
```

Добавить host key в `known_hosts` (рекомендуется):

```bash
ssh-keyscan -H <SSH_HOST> >> ~/.ssh/known_hosts
```

Запуск:

```bash
python3 -m tbssa
```

### Деплой через systemd

1) Отредактировать `tbssa.service` под своё окружение (путь/пользователь/EnvironmentFile).

2) Установить unit:

```bash
sudo cp /home/ezovskikh_a/apps/tbssa/tbssa.service /etc/systemd/system/tbssa.service
sudo systemctl daemon-reload
sudo systemctl enable --now tbssa
```

### Безопасность

- **Обязательно** задайте `ADMIN_IDS`. Если он пуст — админ-команды будут заблокированы (fail-closed).
- Рекомендуется проверка SSH host key через `~/.ssh/known_hosts` либо через `SSH_HOST_KEY_FINGERPRINT`.

