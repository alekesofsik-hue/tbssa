from __future__ import annotations

import asyncio
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from telegram.ext import ContextTypes

from tbssa.config_service import ConfigService, ServerConfig
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import Server
from tbssa.notifier import notify_admins
from tbssa.ping import ping_status
from tbssa.ssh import ssh_exec

log = logging.getLogger("tbssa")

# ── bot_data keys ──────────────────────────────────────────────────────────────
_PENDING_KEY = "monitor:pending"  # dict[server_id, _PendingState]


def _svc(context: ContextTypes.DEFAULT_TYPE) -> ConfigService:
    return context.bot_data["config_service"]


def _pending(context: ContextTypes.DEFAULT_TYPE) -> dict[int, "_PendingState"]:
    if _PENDING_KEY not in context.bot_data:
        context.bot_data[_PENDING_KEY] = {}
    return context.bot_data[_PENDING_KEY]


# ── Pending state per server ───────────────────────────────────────────────────

@dataclass
class _PendingState:
    direction: Literal["offline", "online"]  # what we are confirming
    stage: int = 0                            # 0 = waiting for 1st confirm, 1 = for 2nd
    token: str = field(default_factory=lambda: secrets.token_hex(4))


@dataclass(frozen=True)
class ConfirmedReachabilityCheck:
    confirmed_before: bool | None
    confirmed_after: bool | None
    icmp_ok: bool
    ssh_stage1_ok: bool | None = None
    ssh_stage2_ok: bool | None = None

    @property
    def changed(self) -> bool:
        return self.confirmed_before != self.confirmed_after


# ── ICMP helper ────────────────────────────────────────────────────────────────

def _valid_ping_template(tpl: str) -> bool:
    return bool(tpl and "{timeout}" in tpl and "{host}" in tpl)


async def _icmp_check(server: ServerConfig, cmd_template: str | None) -> bool:
    """Returns True if at least one ICMP ping succeeds."""
    try:
        tpl = cmd_template if _valid_ping_template(cmd_template or "") else None
        ok, _ = await asyncio.to_thread(
            ping_status,
            server.ping_host,
            server.ping_count,
            server.ping_timeout,
            tpl,
        )
        return ok > 0
    except Exception:
        return False


# ── SSH check helper ───────────────────────────────────────────────────────────

async def _ssh_check(server: ServerConfig) -> bool:
    """
    Performs an SSH connection + 'whoami' command.
    Returns True if the command succeeds (rc == 0).
    Any SSH error (auth, host key, timeout) is treated as failure.
    """
    try:
        rc, _, _ = await asyncio.to_thread(
            ssh_exec,
            host=server.ssh_host,
            user=server.ssh_user,
            key_path=server.ssh_key_path,
            known_hosts_path=server.ssh_known_hosts_path,
            pinned_fingerprint_md5=server.ssh_fingerprint or "",
            connect_timeout=server.ssh_connect_timeout,
            command_timeout=server.ssh_command_timeout,
            cmd="whoami",
        )
        return rc == 0
    except Exception as exc:
        log.debug(f"[monitor] ssh_check failed for {server.name}: {exc}")
        return False


def _confirmed_state_label(value: bool | None) -> str:
    if value is True:
        return "SSH доступен"
    if value is False:
        return "SSH недоступен"
    return "статус не задан"


def _probe_label(value: bool | None) -> str:
    if value is None:
        return "—"
    return "OK" if value else "FAIL"


def _live_ssh_summary(result: ConfirmedReachabilityCheck) -> str:
    if result.ssh_stage2_ok is None:
        if result.ssh_stage1_ok is True:
            return "Прямо сейчас SSH до сервера проходит."
        if result.ssh_stage1_ok is False:
            return "Прямо сейчас SSH до сервера не проходит."
        return "Прямо сейчас результат SSH-проверки не получен."

    if result.ssh_stage1_ok is result.ssh_stage2_ok:
        if result.ssh_stage2_ok:
            return "Прямо сейчас SSH до сервера стабильно проходит."
        return "Прямо сейчас SSH до сервера стабильно не проходит."

    return "Прямо сейчас SSH дал переходный результат: две проверки не совпали."


async def run_confirmed_reachability_check(
    server: ServerConfig,
    *,
    confirmed_ok: bool | None,
    ping_template: str | None,
) -> ConfirmedReachabilityCheck:
    """
    Run the same reachability criteria as monitor confirmations, but immediately.

    This helper is used by manual "Check" actions so that UI diagnostics match
    monitor semantics and never overwrite confirmed state based on ICMP alone.
    """
    ssh_stage1_ok: bool | None = await _ssh_check(server)
    icmp_ok = await _icmp_check(server, ping_template)
    ssh_stage2_ok: bool | None = None
    confirmed_after = confirmed_ok

    if confirmed_ok is True:
        if not ssh_stage1_ok:
            ssh_stage2_ok = await _ssh_check(server)
            if ssh_stage2_ok is False:
                confirmed_after = False
    elif confirmed_ok is False:
        if ssh_stage1_ok:
            ssh_stage2_ok = await _ssh_check(server)
            if ssh_stage2_ok:
                confirmed_after = True
    else:
        ssh_stage2_ok = await _ssh_check(server)
        if ssh_stage1_ok and ssh_stage2_ok:
            confirmed_after = True
        elif (not ssh_stage1_ok) and (ssh_stage2_ok is False):
            confirmed_after = False

    return ConfirmedReachabilityCheck(
        confirmed_before=confirmed_ok,
        confirmed_after=confirmed_after,
        icmp_ok=icmp_ok,
        ssh_stage1_ok=ssh_stage1_ok,
        ssh_stage2_ok=ssh_stage2_ok,
    )


def format_confirmed_reachability_report(result: ConfirmedReachabilityCheck) -> str:
    lines = [
        "🔍 <b>Проверка SSH-доступности по логике мониторинга</b>",
        f"SSH #1: <b>{_probe_label(result.ssh_stage1_ok)}</b>",
    ]
    if result.ssh_stage2_ok is not None:
        lines.append(f"SSH #2: <b>{_probe_label(result.ssh_stage2_ok)}</b>")
    lines.append(f"ICMP (справочно): <b>{_probe_label(result.icmp_ok)}</b>")

    lines.append("")
    lines.append(_live_ssh_summary(result))

    if result.changed:
        if result.confirmed_before is None:
            lines.append("Подтверждённый статус мониторинга ещё не сформирован.")
        else:
            lines.append("Этот живой результат не совпадает с подтверждённым статусом мониторинга в карточке ниже.")
        lines.append(
            "Если фоновый мониторинг повторно увидит и подтвердит такой же результат, "
            "подтверждённый статус станет "
            f"<b>{_confirmed_state_label(result.confirmed_after)}</b>."
        )
        lines.append("Кнопка показывает только живую проверку и не меняет подтверждённый статус напрямую.")
        return "\n".join(lines)

    if result.confirmed_before is None:
        lines.append(
            "Подтверждённый статус мониторинга ещё не сформирован: для него нужен стабильный результат SSH."
        )
    elif result.ssh_stage2_ok is not None and result.ssh_stage1_ok is not result.ssh_stage2_ok:
        lines.append(
            "Две SSH-проверки дали разный результат, поэтому подтверждённый статус мониторинга пока не меняется."
        )
    else:
        lines.append(
            "Этот живой результат согласуется с подтверждённым статусом мониторинга в карточке ниже."
        )
    lines.append("Кнопка показывает только живую проверку и не меняет подтверждённый статус напрямую.")
    return "\n".join(lines)


# ── Admin notification ────────────────────────────────────────────────────────

async def _alert_admins(
    context: ContextTypes.DEFAULT_TYPE,
    server: ServerConfig,
    is_now_reachable: bool,
) -> None:
    svc = _svc(context)
    settings = context.bot_data["settings"]
    icon = "🟢" if is_now_reachable else "🔴"
    state = "снова доступен" if is_now_reachable else "недоступен"
    text = (
        f"{icon} <b>SSH к серверу «{server.name}»</b> {state}!\n"
        f"Хост: <code>{server.ssh_host}</code>\n"
        "Метод проверки: SSH\n"
        f"Время: {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}"
    )
    await notify_admins(settings, svc, text)


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _get_confirmed_ok(server_id: int) -> bool | None:
    """Read confirmed SSH availability from DB."""
    async with AsyncSessionLocal() as session:
        db_server = await session.get(Server, server_id)
        return db_server.last_ping_ok if db_server else None


async def _set_confirmed_status(server_id: int, reachable: bool) -> None:
    """Write confirmed SSH availability to DB."""
    async with AsyncSessionLocal() as session:
        db_server = await session.get(Server, server_id)
        if db_server is None:
            return
        db_server.last_ping_ok = reachable
        db_server.last_ping_at = datetime.utcnow()
        await session.commit()


# ── Confirm job (run_once callback) ───────────────────────────────────────────

def _make_confirm_job(
    server_id: int,
    token: str,
    direction: Literal["offline", "online"],
    stage: int,
):
    """
    Returns an async callable suitable for JobQueue.run_once.
    stage: 0 = first SSH confirm attempt, 1 = second (final) SSH confirm attempt.
    direction: 'offline' = we are checking if server is really down,
               'online'  = we are checking if server is really up.
    """
    async def _job(context: ContextTypes.DEFAULT_TYPE) -> None:
        svc = _svc(context)
        if not svc.is_ready():
            return

        # Resolve current server config from cache
        server = svc.get_server(server_id)
        if server is None:
            # Server was removed from active list, cancel pending
            _pending(context).pop(server_id, None)
            return

        # Guard: check token still matches (no race / stale job)
        pending_map = _pending(context)
        state = pending_map.get(server_id)
        if state is None or state.token != token or state.stage != stage:
            log.debug(
                f"[monitor] stale confirm job skipped: server={server.name} "
                f"direction={direction} stage={stage}"
            )
            return

        # Perform SSH check
        ssh_ok = await _ssh_check(server)
        log.info(
            f"[monitor] confirm stage={stage} direction={direction} "
            f"server={server.name} ssh_ok={ssh_ok}"
        )

        if direction == "offline":
            if ssh_ok:
                # Server is reachable via SSH — false alarm, cancel pending
                log.info(f"[monitor] server={server.name} SSH ok at offline-stage={stage}, cancelling pending")
                pending_map.pop(server_id, None)
                return

            # SSH failed again
            if stage < 1:
                # Schedule next confirm attempt
                delay2 = svc.get_int("OFFLINE_CONFIRM_DELAY2_MINUTES", 2) * 60
                state.stage = 1
                context.job_queue.run_once(
                    _make_confirm_job(server_id, token, "offline", 1),
                    when=delay2,
                    name=f"confirm:offline:{server_id}:{token}:1",
                )
                log.info(
                    f"[monitor] server={server.name} offline-stage=1 scheduled in {delay2}s"
                )
            else:
                # Both SSH confirms failed → confirmed down
                pending_map.pop(server_id, None)
                await _set_confirmed_status(server_id, False)
                await svc.reload()
                await _alert_admins(context, server, is_now_reachable=False)
                log.info(f"[monitor] server={server.name} CONFIRMED DOWN → admins notified")

        else:  # direction == "online"
            if not ssh_ok:
                # Server still not reachable via SSH
                if stage < 1:
                    delay2 = svc.get_int("ONLINE_CONFIRM_DELAY2_MINUTES", 2) * 60
                    state.stage = 1
                    context.job_queue.run_once(
                        _make_confirm_job(server_id, token, "online", 1),
                        when=delay2,
                        name=f"confirm:online:{server_id}:{token}:1",
                    )
                    log.info(
                        f"[monitor] server={server.name} online-stage=1 scheduled in {delay2}s"
                    )
                else:
                    # Both SSH confirms failed for "up" → still confirmed down, cancel pending
                    pending_map.pop(server_id, None)
                    log.info(
                        f"[monitor] server={server.name} online confirm failed twice — stays DOWN"
                    )
                return

            # SSH ok
            if stage < 1:
                # Schedule next confirm attempt
                delay2 = svc.get_int("ONLINE_CONFIRM_DELAY2_MINUTES", 2) * 60
                state.stage = 1
                context.job_queue.run_once(
                    _make_confirm_job(server_id, token, "online", 1),
                    when=delay2,
                    name=f"confirm:online:{server_id}:{token}:1",
                )
                log.info(
                    f"[monitor] server={server.name} online-stage=1 scheduled in {delay2}s"
                )
            else:
                # Both SSH confirms succeeded → confirmed up
                pending_map.pop(server_id, None)
                await _set_confirmed_status(server_id, True)
                await svc.reload()
                await _alert_admins(context, server, is_now_reachable=True)
                log.info(f"[monitor] server={server.name} CONFIRMED UP → admins notified")

    return _job


# ── Main periodic job ─────────────────────────────────────────────────────────

async def check_servers_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Periodic job: SSH-first monitor for all active servers.

    - Each cycle performs a primary SSH probe.
    - If SSH fails for a server whose confirmed status is True (or None on first check):
        → start offline-confirmation process via delayed SSH checks.
    - If SSH succeeds for a server whose confirmed status is False (or None on first check):
        → start online-confirmation process via delayed SSH checks.
    - ICMP is auxiliary only and does not define the authoritative status.
    - last_ping_ok in DB is only updated when confirmation is complete.
    - UI shows confirmed SSH state, NOT raw ping results.
    """
    svc = _svc(context)
    if not svc.is_ready():
        return

    servers = svc.get_servers()
    if not servers:
        return

    pending_map = _pending(context)
    ping_template = svc.get_str("PING_CMD_TEMPLATE", "")

    for server in servers:
        confirmed_ok = await _get_confirmed_ok(server.id)

        # Server already has a pending confirmation — don't interfere
        if server.id in pending_map:
            log.debug(
                f"[monitor] server={server.name} has pending confirm, skipping ICMP trigger"
            )
            continue

        ssh_ok = await _ssh_check(server)

        direction: Literal["offline", "online"] | None = None
        if confirmed_ok is None:
            direction = "online" if ssh_ok else "offline"
        elif confirmed_ok and not ssh_ok:
            direction = "offline"
        elif confirmed_ok is False and ssh_ok:
            direction = "online"

        if direction is None:
            continue

        icmp_ok = await _icmp_check(server, ping_template)
        delay_key = "OFFLINE_CONFIRM_DELAY1_MINUTES" if direction == "offline" else "ONLINE_CONFIRM_DELAY1_MINUTES"
        delay1 = svc.get_int(delay_key, 3) * 60
        token = secrets.token_hex(4)
        pending_map[server.id] = _PendingState(direction=direction, stage=0, token=token)
        context.job_queue.run_once(
            _make_confirm_job(server.id, token, direction, 0),
            when=delay1,
            name=f"confirm:{direction}:{server.id}:{token}:0",
        )
        log.info(
            "[monitor] server=%s primary SSH %s (ICMP %s, confirmed=%s) — %s confirm scheduled in %ss",
            server.name,
            "ok" if ssh_ok else "fail",
            "ok" if icmp_ok else "fail",
            confirmed_ok,
            direction,
            delay1,
        )
