from __future__ import annotations

import asyncio
import secrets
import time
from functools import wraps
from typing import Awaitable, Callable, TypeVar

from telegram import Update
from telegram.ext import ContextTypes

from tbssa.ping import ping_status
from tbssa.settings import parse_admin_ids
from tbssa.ssh import ps, ssh_exec


T = TypeVar("T")


def _now() -> float:
    return time.time()


def _gen_confirm_code() -> str:
    # Short, copy-paste friendly token
    return secrets.token_hex(3)  # 6 hex chars


def admin_only(*, admin_ids_raw: str) -> Callable[[Callable[..., Awaitable[T]]], Callable[..., Awaitable[T]]]:
    admin_ids = parse_admin_ids(admin_ids_raw)

    def decorator(func: Callable[..., Awaitable[T]]) -> Callable[..., Awaitable[T]]:
        @wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE) -> T | None:
            uid = update.effective_user.id if update.effective_user else None
            if not admin_ids:
                await update.effective_chat.send_message(
                    "⛔ Админ-команды отключены: `ADMIN_IDS` не настроен.",
                    parse_mode="Markdown",
                )
                return None
            if uid is None or uid not in admin_ids:
                await update.effective_chat.send_message("⛔ Доступ запрещён.")
                return None
            return await func(update, context)

        return wrapper

    return decorator


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "Привет! Мини-бот для управления сервером Windows.\n"
        "Команды:\n"
        "/status — пинг до сервера (с VPS)\n"
        "/reboot — перезагрузка (с подтверждением)\n"
        "/sos — жёсткое выключение (с подтверждением)\n"
        "/me — показать твой chat_id",
    )


async def me(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    cid = update.effective_chat.id
    uid = update.effective_user.id if update.effective_user else None
    await update.message.reply_text(f"chat_id = {cid}\nuser_id = {uid}")


async def status_cmd(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ping_host: str,
    ping_count: int,
    ping_timeout: int,
) -> None:
    await update.message.reply_text("Пингую сервер… ⏳")
    ok, rtts = await asyncio.to_thread(ping_status, ping_host, ping_count, ping_timeout)
    if ok == 0:
        await update.message.reply_text(
            "🔴 Сервер *недоступен* по ICMP с VPS.\n"
            f"Хост: `{ping_host}`\n"
            f"Попыток: {ping_count}, успешно: 0",
            parse_mode="Markdown",
        )
        return
    avg = sum(rtts) / len(rtts)
    best = min(rtts)
    worst = max(rtts)
    await update.message.reply_text(
        "🟢 Сервер *доступен* по ICMP.\n"
        f"Хост: `{ping_host}`\n"
        f"Успешно: {ok}/{ping_count}\n"
        f"RTT (мс): min={best:.1f}  avg={avg:.1f}  max={worst:.1f}",
        parse_mode="Markdown",
    )


def _confirm_key(command: str) -> str:
    return f"confirm:{command}"


async def require_confirmation(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    ttl_seconds: int,
) -> bool:
    """
    Two-step confirmation:
    1) /<cmd> -> bot replies with /<cmd> <code>
    2) /<cmd> <code> within TTL -> confirmed
    """
    # Parse argument (code)
    text = (update.message.text or "").strip() if update.message else ""
    parts = text.split(maxsplit=1)
    provided = parts[1].strip() if len(parts) == 2 else ""

    key = _confirm_key(command)
    state = context.user_data.get(key) if context.user_data is not None else None

    if isinstance(state, dict):
        code = str(state.get("code", ""))
        exp = float(state.get("exp", 0))
        if provided and provided == code and _now() <= exp:
            # consume
            context.user_data.pop(key, None)
            return True

    # generate new
    code = _gen_confirm_code()
    exp = _now() + max(10, int(ttl_seconds))
    context.user_data[key] = {"code": code, "exp": exp}
    await update.message.reply_text(
        "⚠️ Подтверждение требуется.\n"
        f"Повторите команду в течение {ttl_seconds}с:\n"
        f"`/{command} {code}`",
        parse_mode="Markdown",
    )
    return False


async def reboot_cmd(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ttl_seconds: int,
    ssh_host: str,
    ssh_user: str,
    ssh_key_path: str,
    ssh_known_hosts_path: str,
    ssh_pinned_fingerprint: str,
    ssh_connect_timeout: int,
    ssh_command_timeout: int,
) -> None:
    if not await require_confirmation(update=update, context=context, command="reboot", ttl_seconds=ttl_seconds):
        return
    await update.message.reply_text("Перезагружаю сервер… ♻️")
    rc, _, err = await asyncio.to_thread(
        ssh_exec,
        host=ssh_host,
        user=ssh_user,
        key_path=ssh_key_path,
        known_hosts_path=ssh_known_hosts_path,
        pinned_fingerprint_md5=ssh_pinned_fingerprint,
        connect_timeout=ssh_connect_timeout,
        command_timeout=ssh_command_timeout,
        cmd=ps("shutdown /r /t 0 /f"),
    )
    if rc != 0:
        await update.message.reply_text(f"⚠️ Команда вернула rc={rc}\nstderr:\n{(err or '')[:600]}")
    else:
        await update.message.reply_text("Ок. Команда отправлена. Сервер уходит в перезагрузку.")


async def sos_cmd(
    *,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    ttl_seconds: int,
    ssh_host: str,
    ssh_user: str,
    ssh_key_path: str,
    ssh_known_hosts_path: str,
    ssh_pinned_fingerprint: str,
    ssh_connect_timeout: int,
    ssh_command_timeout: int,
) -> None:
    if not await require_confirmation(update=update, context=context, command="sos", ttl_seconds=ttl_seconds):
        return
    await update.message.reply_text("Жёсткое выключение… ⚡")
    rc, _, err = await asyncio.to_thread(
        ssh_exec,
        host=ssh_host,
        user=ssh_user,
        key_path=ssh_key_path,
        known_hosts_path=ssh_known_hosts_path,
        pinned_fingerprint_md5=ssh_pinned_fingerprint,
        connect_timeout=ssh_connect_timeout,
        command_timeout=ssh_command_timeout,
        cmd=ps("shutdown /p /f"),
    )
    if rc != 0:
        await update.message.reply_text(f"⚠️ Команда вернула rc={rc}\nstderr:\n{(err or '')[:600]}")
    else:
        await update.message.reply_text("Ок. Команда отправлена. Сервер сейчас выключится.")

