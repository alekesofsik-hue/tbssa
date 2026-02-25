from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select

from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import AuditLog, Config, Server, User

log = logging.getLogger("tbssa")


@dataclass
class ServerConfig:
    id: int
    name: str
    ssh_host: str
    ssh_user: str
    ssh_key_path: str
    ssh_known_hosts_path: str
    ssh_fingerprint: str
    ssh_connect_timeout: int
    ssh_command_timeout: int
    ping_host: str
    ping_count: int
    ping_timeout: int


# Keys stored in the config table with their defaults.
_CONFIG_DEFAULTS: dict[str, str] = {
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
    "SOS_BUTTON_LABEL": "SOS",
    "SOS_REQUIRE_CONFIRM": "0",
    "SOS_MSG_HEADER": "SOS выполнен",
}


class ConfigService:
    """
    In-memory cache for servers, admin user IDs, and global config values.
    Call load() on startup and reload() after any admin change.
    """

    def __init__(self) -> None:
        self._admin_ids: set[int] = set()
        self._servers: list[ServerConfig] = []
        self._config: dict[str, str] = dict(_CONFIG_DEFAULTS)
        self._ready: bool = False

    # ------------------------------------------------------------------
    # Load / reload
    # ------------------------------------------------------------------

    async def load(self) -> None:
        async with AsyncSessionLocal() as session:
            users = (await session.execute(select(User).where(User.is_active == True))).scalars().all()  # noqa: E712
            servers = (await session.execute(select(Server).where(Server.is_active == True))).scalars().all()  # noqa: E712
            configs = (await session.execute(select(Config))).scalars().all()

        self._admin_ids = {u.telegram_id for u in users}
        self._servers = [self._to_server_config(s) for s in servers]
        cfg = dict(_CONFIG_DEFAULTS)
        for row in configs:
            cfg[row.key] = row.value
        self._config = cfg
        self._ready = True
        log.info(
            f"[config_service] loaded: {len(self._admin_ids)} admin(s), "
            f"{len(self._servers)} server(s), {len(self._config)} config key(s)"
        )

    async def reload(self) -> None:
        """Invalidate and reload from DB. Call after any admin change."""
        self._ready = False
        await self.load()

    # ------------------------------------------------------------------
    # Access
    # ------------------------------------------------------------------

    def is_ready(self) -> bool:
        return self._ready

    def is_admin(self, telegram_id: int | None) -> bool:
        if not self._admin_ids or telegram_id is None:
            return False
        return telegram_id in self._admin_ids

    def get_servers(self) -> list[ServerConfig]:
        return list(self._servers)

    def get_server(self, server_id: int) -> ServerConfig | None:
        for s in self._servers:
            if s.id == server_id:
                return s
        return None

    def get_first_server(self) -> ServerConfig | None:
        return self._servers[0] if self._servers else None

    def get_global(self, key: str, default: str = "") -> str:
        return self._config.get(key, default)

    def get_admin_ids(self) -> list[int]:
        return list(self._admin_ids)

    def get_int(self, key: str, default: int = 0) -> int:
        try:
            return int(self._config.get(key, str(default)))
        except (ValueError, TypeError):
            return default

    def get_str(self, key: str, default: str = "") -> str:
        return self._config.get(key, default) or default

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    async def write_audit(
        self,
        telegram_id: int,
        username: str | None,
        action: str,
        details: str | None = None,
    ) -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                AuditLog(
                    telegram_id=telegram_id,
                    username=username,
                    action=action,
                    details=details,
                )
            )
            await session.commit()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _to_server_config(self, s: Server) -> ServerConfig:
        ssh_user = self.get_str("SSH_DEFAULT_USER", "bot-admin")
        ssh_key_path = self.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot")
        return ServerConfig(
            id=s.id,
            name=s.name,
            ssh_host=s.ssh_host,
            ssh_user=ssh_user,
            ssh_key_path=ssh_key_path,
            ssh_known_hosts_path=s.ssh_known_hosts_path,
            ssh_fingerprint=s.ssh_fingerprint or "",
            ssh_connect_timeout=s.ssh_connect_timeout,
            ssh_command_timeout=s.ssh_command_timeout,
            ping_host=s.ssh_host,  # один адрес для SSH и проверки доступности
            ping_count=s.ping_count,
            ping_timeout=s.ping_timeout,
        )
