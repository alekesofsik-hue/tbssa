from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


def parse_admin_ids(raw: str) -> set[int]:
    ids: set[int] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.isdigit():
            continue
        ids.add(int(part))
    return ids


class Settings(BaseSettings):
    """Runtime settings. Only TELEGRAM_BOT_TOKEN is required after bootstrap."""

    TELEGRAM_BOT_TOKEN: str
    MAX_BOT_TOKEN: str = ""
    MAX_BASE_URL: str = "https://platform-api.max.ru"
    MAX_POLL_TIMEOUT: int = 30
    MAX_POLL_LIMIT: int = 100
    MAX_WEBHOOK_URL: str = ""
    MAX_WEBHOOK_SECRET: str = ""
    MAX_WEBHOOK_BIND_HOST: str = "127.0.0.1"
    MAX_WEBHOOK_BIND_PORT: int = 8081
    MAX_WEBHOOK_SYNC_INTERVAL_SECONDS: int = 900

    # Only needed for tbssa-seed (bootstrap). Ignored at runtime — data comes from DB.
    ADMIN_IDS: str = ""
    MAX_ADMIN_IDS: str = ""
    SSH_HOST: str = ""
    SSH_USER: str = "bot-admin"
    SSH_KEY_PATH: str = "~/.ssh/id_ed25519_bot"
    SSH_KNOWN_HOSTS_PATH: str = "~/.ssh/known_hosts"
    SSH_HOST_KEY_FINGERPRINT: str = ""
    SSH_CONNECT_TIMEOUT: int = 8
    SSH_COMMAND_TIMEOUT: int = 15
    PING_HOST: str = ""
    PING_COUNT: int = 3
    PING_TIMEOUT: int = 1
    CONFIRM_TTL_SECONDS: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

