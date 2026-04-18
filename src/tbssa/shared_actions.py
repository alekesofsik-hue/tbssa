from __future__ import annotations

import asyncio
from datetime import datetime

from tbssa.config_service import ConfigService, ServerConfig
from tbssa.notifier import actor_display
from tbssa.ssh import ps, ssh_exec

_RU_MONTHS = [
    "", "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def guest_start_text(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return (
        "Здравствуйте.\n"
        f"Дата: {now.day} {_RU_MONTHS[now.month]} {now.year} г.\n"
        f"Время: {now.strftime('%H:%M')}\n\n"
        "Если вам нужен доступ к управлению, отправьте команду /my и передайте ID владельцу бота."
    )


def broadcast_text(actor_id: int, actor_username: str | None, actor_platform: str, text: str) -> str:
    return f"📢 <b>Сообщение от {actor_display(actor_id, actor_username, actor_platform)}:</b>\n\n{text}"


def sos_progress_text(servers: list[ServerConfig]) -> str:
    names = ", ".join(f"<b>{s.name}</b>" for s in servers)
    return f"🆘 <b>Выполняю SOS для серверов:</b> {names}"


async def execute_sos_all(
    svc: ConfigService,
    actor_id: int,
    actor_username: str | None,
    *,
    actor_platform: str = "telegram",
) -> str:
    servers = svc.get_servers()
    if not servers:
        return "⚠️ Нет активных серверов."

    poweroff_cmd = ps(svc.get_str("SSH_CMD_POWEROFF", "shutdown /p /f"))

    async def _shutdown_one(server: ServerConfig) -> tuple[str, int, str | None]:
        try:
            rc, _, err = await asyncio.to_thread(
                ssh_exec,
                host=server.ssh_host,
                user=server.ssh_user,
                key_path=server.ssh_key_path,
                known_hosts_path=server.ssh_known_hosts_path,
                pinned_fingerprint_md5=server.ssh_fingerprint,
                connect_timeout=server.ssh_connect_timeout,
                command_timeout=server.ssh_command_timeout,
                cmd=poweroff_cmd,
            )
            return server.name, rc, err
        except Exception as exc:
            return server.name, -1, str(exc)

    results = await asyncio.gather(*[_shutdown_one(server) for server in servers])

    header = svc.get_str("SOS_MSG_HEADER", "SOS выполнен")
    lines: list[str] = [f"🆘 <b>{header}</b>\n"]
    for name, rc, err in results:
        if rc == 0:
            lines.append(f"🖥 {name}: ✅ команда принята")
        elif rc == -1:
            lines.append(f"🖥 {name}: ❌ ошибка: {err}")
        else:
            lines.append(f"🖥 {name}: ⚠️ rc={rc}")
        await svc.write_audit(
            actor_id,
            actor_username,
            "sos:all",
            f"server={name} rc={rc}",
            platform=actor_platform,
        )

    lines.append(f"\nИнициировал: {actor_display(actor_id, actor_username, actor_platform)}")
    return "\n".join(lines)
