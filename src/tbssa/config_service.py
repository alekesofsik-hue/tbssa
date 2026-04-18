from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypeAlias

from sqlalchemy import select

from tbssa.config_meta import CONFIG_DEFAULTS
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import AuditLog, Config, Server, User

log = logging.getLogger("tbssa")

LoadSignature: TypeAlias = tuple[
    tuple[int, ...],
    tuple[int, ...],
    tuple[int, ...],
    tuple[tuple[str, str], ...],
]


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


class ConfigService:
    """
    In-memory cache for servers, admin user IDs, and global config values.
    Call load() on startup and reload() after any admin change.
    """

    def __init__(self) -> None:
        self._telegram_admin_ids: set[int] = set()
        self._max_admin_ids: set[int] = set()
        self._servers: list[ServerConfig] = []
        self._config: dict[str, str] = dict(CONFIG_DEFAULTS)
        self._ready: bool = False
        self._last_load_signature: LoadSignature | None = None

    # ------------------------------------------------------------------
    # Load / reload
    # ------------------------------------------------------------------

    async def load(self) -> None:
        async with AsyncSessionLocal() as session:
            users = (await session.execute(select(User).where(User.is_active == True))).scalars().all()  # noqa: E712
            servers = (await session.execute(select(Server).where(Server.is_active == True))).scalars().all()  # noqa: E712
            configs = (await session.execute(select(Config))).scalars().all()

        cfg = dict(CONFIG_DEFAULTS)
        for row in configs:
            cfg[row.key] = row.value
        self._config = cfg
        self._telegram_admin_ids = {u.telegram_id for u in users if u.telegram_id is not None}
        self._max_admin_ids = {u.max_user_id for u in users if u.max_user_id is not None}
        self._servers = [self._to_server_config(s) for s in servers]
        self._ready = True
        self._log_load_summary(
            self._build_load_signature(
                telegram_admin_ids=self._telegram_admin_ids,
                max_admin_ids=self._max_admin_ids,
                server_ids={s.id for s in servers},
                config_values=self._config,
            )
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
        if not self._telegram_admin_ids or telegram_id is None:
            return False
        return telegram_id in self._telegram_admin_ids

    def is_max_admin(self, max_user_id: int | None) -> bool:
        if not self._max_admin_ids or max_user_id is None:
            return False
        return max_user_id in self._max_admin_ids

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
        return list(self._telegram_admin_ids)

    def get_max_admin_ids(self) -> list[int]:
        return list(self._max_admin_ids)

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
        actor_id: int,
        username: str | None,
        action: str,
        details: str | None = None,
        platform: str = "telegram",
    ) -> None:
        async with AsyncSessionLocal() as session:
            session.add(
                AuditLog(
                    actor_id=actor_id,
                    platform=platform,
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

    def _build_load_signature(
        self,
        *,
        telegram_admin_ids: set[int],
        max_admin_ids: set[int],
        server_ids: set[int],
        config_values: dict[str, str],
    ) -> LoadSignature:
        return (
            tuple(sorted(telegram_admin_ids)),
            tuple(sorted(max_admin_ids)),
            tuple(sorted(server_ids)),
            tuple(sorted(config_values.items())),
        )

    def _log_load_summary(self, signature: LoadSignature) -> None:
        if self._last_load_signature == signature:
            return

        verb = "loaded" if self._last_load_signature is None else "reloaded"
        self._last_load_signature = signature
        tg_ids, max_ids, server_ids, config_items = signature
        log.info(
            "[config_service] %s: %s telegram admin(s), %s max admin(s), %s server(s), %s config key(s)",
            verb,
            len(tg_ids),
            len(max_ids),
            len(server_ids),
            len(config_items),
        )
