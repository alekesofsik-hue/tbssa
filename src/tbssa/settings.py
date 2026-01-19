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
    TELEGRAM_BOT_TOKEN: str

    # "123,456"
    ADMIN_IDS: str = ""

    # SSH to Windows
    SSH_HOST: str
    SSH_USER: str = "bot-admin"
    SSH_KEY_PATH: str = "/home/ezovskikh_a/.ssh/id_ed25519_bot"
    SSH_KNOWN_HOSTS_PATH: str = "/home/ezovskikh_a/.ssh/known_hosts"
    # Optional pinned fingerprint (MD5 hex with or without ':')
    SSH_HOST_KEY_FINGERPRINT: str = ""
    SSH_CONNECT_TIMEOUT: int = 8
    SSH_COMMAND_TIMEOUT: int = 15

    # Ping settings
    PING_HOST: str
    PING_COUNT: int = 3
    PING_TIMEOUT: int = 1

    # Dangerous commands confirmation
    CONFIRM_TTL_SECONDS: int = 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

