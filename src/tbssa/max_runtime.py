from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, func, select

from tbssa.admin.audit import log_action
from tbssa.admin.users import (
    _platform_field_hint,
    _platform_field_label,
    _truncate_button_text,
    _user_card_text,
    _user_ref,
    _user_title,
    sync_admin_from_max,
)
from tbssa.audit_view import format_audit_row
from tbssa.config_meta import (
    CONFIG_DEFAULTS,
    CONFIG_NUMERIC_KEYS,
    CONFIG_TEXT_KEYS,
    merged_config_values,
)
from tbssa.config_service import ConfigService
from tbssa.db.engine import AsyncSessionLocal
from tbssa.db.models import AuditLog, Config, Server, User
from tbssa.error_handlers import MAX_UI_ERROR_TEXT
from tbssa.max_api import MaxApiClient, max_callback_button, max_inline_keyboard
from tbssa.notifier import notify_admins
from tbssa.ping import ping_status
from tbssa.settings import Settings
from tbssa.shared_actions import broadcast_text, execute_sos_all, guest_start_text
from tbssa.ssh import ps, ssh_exec
from tbssa.ui_text import (
    ADMIN_ACCESS_DENIED_TEXT,
    ADMIN_HOME_TEXT,
    ADMIN_QUICK_ACTIONS_TEXT,
    BUTTON_AUDIT,
    BUTTON_CANCEL,
    BUTTON_CONFIRM,
    BUTTON_NEXT_10,
    BUTTON_SERVERS,
    BUTTON_SETTINGS,
    BUTTON_USERS,
    my_id_text,
    sos_confirm_text,
)

log = logging.getLogger("tbssa")

_PAGE_SIZE = 10
_SESSION_TTL_SECONDS = 5 * 60
_MAX_SETTINGS_PAGE_SIZE = 8
_MAX_USERS_PAGE_SIZE = 8

_MAX_SETTINGS_KEY_ORDER: list[str] = [*CONFIG_NUMERIC_KEYS.keys(), *CONFIG_TEXT_KEYS.keys()]
_MAX_SETTINGS_SHORT_LABELS: dict[str, str] = {
    "CONFIRM_TTL_SECONDS": "Подтверждение (сек.)",
    "PING_COUNT": "Ping: пакеты",
    "PING_TIMEOUT": "Ping: таймаут",
    "SSH_CONNECT_TIMEOUT": "SSH: подключение",
    "SSH_COMMAND_TIMEOUT": "SSH: команда",
    "PING_CHECK_INTERVAL_MINUTES": "Мониторинг: интервал",
    "REACHABILITY_ALERT_COOLDOWN_MINUTES": "Мониторинг: антиспам",
    "OFFLINE_CONFIRM_DELAY1_MINUTES": "Недоступность: шаг 1",
    "OFFLINE_CONFIRM_DELAY2_MINUTES": "Недоступность: шаг 2",
    "ONLINE_CONFIRM_DELAY1_MINUTES": "Доступность: шаг 1",
    "ONLINE_CONFIRM_DELAY2_MINUTES": "Доступность: шаг 2",
    "SOS_REQUIRE_CONFIRM": "SOS: подтверждение",
    "SOS_BUTTON_LABEL": "SOS: кнопка",
    "SOS_MSG_HEADER": "SOS: заголовок",
    "SSH_DEFAULT_USER": "SSH: пользователь",
    "SSH_DEFAULT_KEY_PATH": "SSH: ключ",
    "SSH_CMD_POWEROFF": "Команда: выключение",
    "SSH_CMD_REBOOT": "Команда: перезагрузка",
    "PING_CMD_TEMPLATE": "Проверка: команда",
}

_HOSTNAME_RE = re.compile(
    r"^(?:[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?\.)*"
    r"[a-zA-Z0-9](?:[a-zA-Z0-9\-]{0,61}[a-zA-Z0-9])?$"
)
_IP_RE = re.compile(r"^(\d{1,3}\.){3}\d{1,3}$")


@dataclass
class _MaxSession:
    kind: str
    data: dict[str, str | bool]
    expires_at: float


class MaxBotRuntime:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.client = MaxApiClient(settings.MAX_BOT_TOKEN, settings.MAX_BASE_URL)
        self._thread: threading.Thread | None = None
        self._sessions: dict[int, _MaxSession] = {}

    def start_background(self) -> None:
        if not self.client.enabled:
            log.info("[max] MAX_BOT_TOKEN is not configured; MAX runtime is disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run_thread, name="tbssa-max", daemon=True)
        self._thread.start()
        log.info("[max] background runtime thread started")

    def _run_thread(self) -> None:
        asyncio.run(self.run_forever())

    def _get_session(self, user_id: int) -> _MaxSession | None:
        return self._sessions.get(user_id)

    def _set_session(self, user_id: int, kind: str, **data: str | bool) -> None:
        self._sessions[user_id] = _MaxSession(
            kind=kind,
            data=data,
            expires_at=time.monotonic() + _SESSION_TTL_SECONDS,
        )

    def _clear_session(self, user_id: int) -> None:
        self._sessions.pop(user_id, None)

    async def _handle_update_exception(self, update: dict, exc: Exception) -> None:
        update_type = update.get("update_type")
        log.exception("[max] failed to process update %s: %s", update_type, exc)

        user_id: int | None = None
        callback_id: str | None = None
        if update_type == "message_callback":
            callback = update.get("callback") or {}
            callback_id = callback.get("callback_id")
            user_id = _user_id(callback.get("user") or {})
        elif update_type == "message_created":
            message = update.get("message") or {}
            sender = message.get("sender") or {}
            if not sender.get("is_bot"):
                user_id = _user_id(sender)

        if user_id is None:
            return

        if callback_id:
            try:
                await self.client.answer_callback(callback_id, notification=MAX_UI_ERROR_TEXT)
                return
            except Exception as callback_exc:
                log.debug("[max] failed to send callback error notification: %s", callback_exc)

        try:
            await self.client.send_message_to_user(user_id, MAX_UI_ERROR_TEXT, format="html")
        except Exception as notify_exc:  # pragma: no cover - best effort path
            log.debug("[max] failed to send UI error message: %s", notify_exc)

    async def run_forever(self) -> None:
        svc = ConfigService()
        await svc.load()

        marker = await self._bootstrap_marker()
        backoff = 3

        try:
            me = await self.client.get_me()
            log.info("[max] connected as %s (%s)", me.get("first_name") or me.get("name"), me.get("user_id"))
        except Exception as exc:
            log.warning("[max] failed to fetch bot info: %s", exc)

        while True:
            try:
                await svc.reload()
                page = await self.client.get_updates(
                    marker=marker,
                    timeout=self.settings.MAX_POLL_TIMEOUT,
                    limit=self.settings.MAX_POLL_LIMIT,
                    types=["message_created", "message_callback", "bot_started", "user_added"],
                )
                marker = page.get("marker", marker)
                for update in page.get("updates", []):
                    try:
                        await self._handle_update(svc, update)
                    except Exception as exc:
                        await self._handle_update_exception(update, exc)
                backoff = 3
            except Exception as exc:
                log.warning("[max] polling error: %s; retry in %ss", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _bootstrap_marker(self) -> int | None:
        try:
            page = await self.client.get_updates(timeout=0, limit=1)
            return page.get("marker")
        except Exception as exc:
            log.warning("[max] failed to bootstrap marker: %s", exc)
            return None

    async def _handle_update(self, svc: ConfigService, update: dict) -> None:
        update_type = update.get("update_type")
        if update_type == "message_created":
            await self._handle_message_created(svc, update)
            return
        if update_type == "message_callback":
            await self._handle_callback(svc, update)
            return
        if update_type in {"bot_started", "user_added"}:
            user = update.get("user") or {}
            user_id = _user_id(user)
            if user_id is None:
                return
            await self._sync_max_user(user_id, user)
            if svc.is_max_admin(user_id):
                await self.client.send_message_to_user(
                    user_id,
                    ADMIN_QUICK_ACTIONS_TEXT,
                    attachments=_admin_start_keyboard(svc),
                )
            else:
                await self.client.send_message_to_user(user_id, guest_start_text())

    async def _handle_message_created(self, svc: ConfigService, update: dict) -> None:
        message = update.get("message") or {}
        sender = message.get("sender") or {}
        if sender.get("is_bot"):
            return

        user_id = _user_id(sender)
        text = ((message.get("body") or {}).get("text") or "").strip()
        if user_id is None or not text:
            return

        await self._sync_max_user(user_id, sender)
        is_admin = svc.is_max_admin(user_id)
        session = self._get_session(user_id)
        command = text.split()[0].lower()
        lowered = text.lower().strip()
        username = sender.get("username")

        if session and is_admin:
            handled = await self._handle_session_message(svc, user_id, username, text, session)
            if handled:
                return

        if command == "/start":
            if is_admin:
                await self.client.send_message_to_user(
                    user_id,
                    ADMIN_QUICK_ACTIONS_TEXT,
                    attachments=_admin_start_keyboard(svc),
                )
            else:
                await self.client.send_message_to_user(user_id, guest_start_text())
            return

        if command == "/my":
            await self.client.send_message_to_user(
                user_id,
                my_id_text(user_id),
                format="html",
            )
            return

        if not is_admin:
            return

        if command == "/admin":
            await self.client.send_message_to_user(
                user_id,
                _admin_home_text(),
                attachments=_admin_home_keyboard(svc),
                format="html",
            )
            return

        if command == "/sos" or lowered in ("sos", "сос"):
            await self._run_sos_from_message(svc, user_id, username)
            return

        if text.startswith("/"):
            return

        full_text = broadcast_text(user_id, username, "max", text)
        sent, failed = await notify_admins(self.settings, svc, full_text)
        await svc.write_audit(user_id, username, "broadcast", f"sent={sent} failed={failed}", platform="max")

    async def _handle_callback(self, svc: ConfigService, update: dict) -> None:
        callback = update.get("callback") or {}
        callback_id = callback.get("callback_id")
        payload = callback.get("payload") or ""
        user = callback.get("user") or {}
        user_id = _user_id(user)
        if not callback_id or user_id is None:
            return

        await self._sync_max_user(user_id, user)
        if not svc.is_max_admin(user_id):
            await self.client.answer_callback(callback_id, notification=ADMIN_ACCESS_DENIED_TEXT)
            return

        username = user.get("username")

        if payload == "start:sos":
            if svc.get_int("SOS_REQUIRE_CONFIRM", 0):
                await self.client.answer_callback(
                    callback_id,
                    text=_sos_confirm_text(svc),
                    attachments=_sos_confirm_keyboard(),
                    format="html",
                )
            else:
                report = await execute_sos_all(svc, user_id, username, actor_platform="max")
                await self.client.answer_callback(
                    callback_id,
                    text=report,
                    attachments=_admin_start_keyboard(svc),
                    format="html",
                )
                await notify_admins(self.settings, svc, report, exclude_max_user_id=user_id)
            return

        if payload == "start:sos:confirm":
            report = await execute_sos_all(svc, user_id, username, actor_platform="max")
            await self.client.answer_callback(
                callback_id,
                text=report,
                attachments=_admin_start_keyboard(svc),
                format="html",
            )
            await notify_admins(self.settings, svc, report, exclude_max_user_id=user_id)
            return

        if payload == "start:sos:cancel":
            await self.client.answer_callback(
                callback_id,
                text=ADMIN_QUICK_ACTIONS_TEXT,
                attachments=_admin_start_keyboard(svc),
            )
            return

        if payload == "max:home":
            await self.client.answer_callback(
                callback_id,
                text=_admin_home_text(),
                attachments=_admin_home_keyboard(svc),
                format="html",
            )
            return

        if payload == "max:users":
            await svc.write_audit(user_id, username, "open:admin:users", platform="max")
            text, attachments = await _users_list_view(0)
            await self.client.answer_callback(
                callback_id,
                text=text,
                attachments=attachments,
                format="html",
            )
            return

        if payload.startswith("max:users:page:"):
            offset = int(payload.split(":")[3])
            text, attachments = await _users_list_view(offset)
            await self.client.answer_callback(
                callback_id,
                text=text,
                attachments=attachments,
                format="html",
            )
            return

        if payload == "max:settings":
            await svc.write_audit(user_id, username, "open:admin:settings", platform="max")
            text, attachments = await _settings_view()
            await self.client.answer_callback(
                callback_id,
                text=text,
                attachments=attachments,
                format="html",
            )
            return

        if payload == "max:cfg:edit":
            await self.client.answer_callback(
                callback_id,
                text="⚙️ <b>Выберите параметр для изменения:</b>",
                attachments=_settings_edit_keyboard(0),
                format="html",
            )
            return

        if payload.startswith("max:cfg:page:"):
            page = int(payload.split(":")[3])
            await self.client.answer_callback(
                callback_id,
                text="⚙️ <b>Выберите параметр для изменения:</b>",
                attachments=_settings_edit_keyboard(page),
                format="html",
            )
            return

        if payload.startswith("max:cfg:k:"):
            index = int(payload.split(":")[3])
            key = _config_key_by_index(index)
            if key is None:
                await self.client.answer_callback(callback_id, notification="Неизвестный параметр.")
                return
            text = await _setting_edit_prompt(key)
            if text.startswith("⚠️"):
                await self.client.answer_callback(callback_id, notification="Неизвестный параметр.")
                return
            self._set_session(
                user_id,
                "config_edit",
                key=key,
                is_text=str(key in CONFIG_TEXT_KEYS),
            )
            await self.client.answer_callback(
                callback_id,
                text=text,
                attachments=max_inline_keyboard([[max_callback_button("◀️ Назад", "max:settings")]]),
                format="html",
            )
            return

        if payload == "max:servers":
            await svc.write_audit(user_id, username, "open:admin:servers", platform="max")
            text, attachments = await _servers_list_view()
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload == "max:servers:inactive":
            await svc.write_audit(user_id, username, "open:admin:servers:inactive", platform="max")
            text, attachments = await _servers_list_view(show_inactive=True)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload == "max:srv:add":
            self._set_session(user_id, "server_add_name")
            await self.client.answer_callback(
                callback_id,
                text=(
                    "➕ <b>Новый сервер</b>\n\n"
                    "Шаг 1/2 — Введите <b>имя</b> сервера (латиница, цифры, дефис):\n"
                    "/cancel для отмены."
                ),
                attachments=max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]]),
                format="html",
            )
            return

        if payload == "max:srv:add:save":
            text, attachments = await self._server_add_save(svc, user_id, username)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload == "max:srv:add:cancel":
            self._clear_session(user_id)
            text, attachments = await _servers_list_view()
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload == "max:usr:add":
            self._clear_session(user_id)
            await self.client.answer_callback(
                callback_id,
                text=(
                    "➕ <b>Добавить пользователя</b>\n\n"
                    "Выберите платформу, по ID которой хотите добавить администратора."
                ),
                attachments=_user_add_platform_keyboard(),
                format="html",
            )
            return

        if payload in {"max:usr:add:tg", "max:usr:add:max"}:
            field = "telegram_id" if payload.endswith(":tg") else "max_user_id"
            self._set_session(user_id, "user_add_id", field=field, offset="0")
            await self.client.answer_callback(
                callback_id,
                text=(
                    f"➕ <b>Добавить пользователя</b>\n\n"
                    f"Введите <b>{_platform_field_label(field)}</b> пользователя.\n\n"
                    f"{_platform_field_hint(field)}"
                    "/cancel для отмены."
                ),
                attachments=max_inline_keyboard([[max_callback_button("◀️ Назад", "max:users")]]),
                format="html",
            )
            return

        if payload.startswith("max:srv:view:"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _server_card_view(server_id, svc)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:usr:view:"):
            parts = payload.split(":")
            target_user_id = int(parts[3])
            offset = int(parts[4]) if len(parts) > 4 else 0
            text, attachments = await _user_card_view(target_user_id, offset)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:usr:editf:"):
            parts = payload.split(":")
            target_user_id = int(parts[3])
            field = parts[4]
            offset = int(parts[5]) if len(parts) > 5 else 0
            prompt = await _user_edit_prompt(target_user_id, field)
            if prompt.startswith("⚠️"):
                back_payload = f"max:users:page:{offset}" if "не найден" in prompt.lower() else f"max:usr:view:{target_user_id}:{offset}"
                await self.client.answer_callback(
                    callback_id,
                    text=prompt,
                    attachments=max_inline_keyboard([[max_callback_button("◀️ Назад", back_payload)]]),
                    format="html",
                )
                return
            self._set_session(
                user_id,
                "user_edit_value",
                user_id=str(target_user_id),
                field=field,
                offset=str(offset),
            )
            await self.client.answer_callback(
                callback_id,
                text=prompt,
                attachments=max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:usr:view:{target_user_id}:{offset}")]]),
                format="html",
            )
            return

        if payload.startswith("max:usr:edit:"):
            parts = payload.split(":")
            target_user_id = int(parts[3])
            offset = int(parts[4]) if len(parts) > 4 else 0
            text, attachments = await _user_edit_menu(target_user_id, offset)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:usr:toggle:"):
            parts = payload.split(":")
            target_user_id = int(parts[3])
            offset = int(parts[4]) if len(parts) > 4 else 0
            text, attachments = await _toggle_user(target_user_id, offset, svc, user_id, username)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:editf:"):
            parts = payload.split(":")
            server_id = int(parts[3])
            field = parts[4]
            prompt = await _server_edit_prompt(server_id, field)
            if prompt.startswith("⚠️"):
                back_payload = "max:servers" if "не найден" in prompt.lower() else f"max:srv:view:{server_id}"
                await self.client.answer_callback(
                    callback_id,
                    text=prompt,
                    attachments=max_inline_keyboard([[max_callback_button("◀️ Назад", back_payload)]]),
                    format="html",
                )
                return
            self._set_session(user_id, "server_edit_value", server_id=str(server_id), field=field)
            await self.client.answer_callback(
                callback_id,
                text=prompt,
                attachments=max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")]]),
                format="html",
            )
            return

        if payload.startswith("max:srv:edit:"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _server_edit_menu(server_id)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:toggle:"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _toggle_server(server_id, svc, user_id, username)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:del:") and payload.endswith(":yes"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _delete_server(server_id, svc, user_id, username)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:del:"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _delete_server_confirm(server_id)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:check:"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _check_server_now(server_id, svc)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:poweroff:") and payload.endswith(":yes"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _poweroff_server(server_id, svc, user_id, username)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:poweroff:"):
            server_id = int(payload.split(":")[3])
            await self.client.answer_callback(
                callback_id,
                text=await _poweroff_confirm_text(server_id),
                attachments=_poweroff_confirm_keyboard(server_id),
                format="html",
            )
            return

        if payload.startswith("max:srv:reboot:") and payload.endswith(":yes"):
            server_id = int(payload.split(":")[3])
            text, attachments = await _reboot_server(server_id, svc, user_id, username)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        if payload.startswith("max:srv:reboot:"):
            server_id = int(payload.split(":")[3])
            await self.client.answer_callback(
                callback_id,
                text=await _reboot_confirm_text(server_id),
                attachments=_reboot_confirm_keyboard(server_id),
                format="html",
            )
            return

        if payload.startswith("max:journal:"):
            await svc.write_audit(user_id, username, "open:admin:audit", platform="max")
            offset = int(payload.split(":")[2])
            text, attachments = await _journal_view(offset)
            await self.client.answer_callback(callback_id, text=text, attachments=attachments, format="html")
            return

        await self.client.answer_callback(callback_id, notification="Команда не поддерживается.")

    async def _run_sos_from_message(self, svc: ConfigService, user_id: int, username: str | None) -> None:
        if svc.get_int("SOS_REQUIRE_CONFIRM", 0):
            await self.client.send_message_to_user(
                user_id,
                _sos_confirm_text(svc),
                attachments=_sos_confirm_keyboard(),
                format="html",
            )
            return

        report = await execute_sos_all(svc, user_id, username, actor_platform="max")
        await self.client.send_message_to_user(
            user_id,
            report,
            attachments=_admin_start_keyboard(svc),
            format="html",
        )
        await notify_admins(self.settings, svc, report, exclude_max_user_id=user_id)

    async def _sync_max_user(self, user_id: int, user: dict) -> None:
        await sync_admin_from_max(user_id, user.get("username"), user.get("first_name") or user.get("name"))

    async def _handle_session_message(
        self,
        svc: ConfigService,
        user_id: int,
        username: str | None,
        text: str,
        session: _MaxSession,
    ) -> bool:
        if session.expires_at <= time.monotonic():
            self._clear_session(user_id)
            await self.client.send_message_to_user(
                user_id,
                "⏱ Сессия редактирования истекла. Откройте раздел заново.",
                format="html",
            )
            return True

        if text.strip().lower() == "/cancel":
            self._clear_session(user_id)
            if session.kind == "server_edit_value":
                server_id = int(str(session.data.get("server_id") or "0"))
                settings_text, attachments = await _server_card_view(server_id, svc)
            elif session.kind == "user_edit_value":
                target_user_id = int(str(session.data.get("user_id") or "0"))
                offset = int(str(session.data.get("offset") or "0"))
                settings_text, attachments = await _user_card_view(target_user_id, offset)
            elif session.kind.startswith("server_"):
                settings_text, attachments = await _servers_list_view()
            elif session.kind.startswith("user_"):
                offset = int(str(session.data.get("offset") or "0"))
                settings_text, attachments = await _users_list_view(offset)
            else:
                settings_text, attachments = await _settings_view()
            await self.client.send_message_to_user(
                user_id,
                f"❌ Редактирование отменено.\n\n{settings_text}",
                attachments=attachments,
                format="html",
            )
            return True

        if session.kind == "config_edit":
            if text.startswith("/"):
                await self.client.send_message_to_user(
                    user_id,
                    "⚠️ Сейчас идет редактирование параметра. Введите новое значение или /cancel.",
                    format="html",
                )
                return True

            key = str(session.data.get("key") or "")
            ok, response = await _save_config_value(svc, user_id, username, key, text, platform="max")
            if not ok:
                await self.client.send_message_to_user(user_id, response, format="html")
                return True

            self._clear_session(user_id)
            settings_text, attachments = await _settings_view()
            await self.client.send_message_to_user(
                user_id,
                f"{response}\n\n{settings_text}",
                attachments=attachments,
                format="html",
            )
            return True

        if session.kind == "user_add_id":
            if text.startswith("/"):
                await self.client.send_message_to_user(
                    user_id,
                    "⚠️ Сейчас идет добавление пользователя. Введите ID или /cancel.",
                    format="html",
                )
                return True

            field = str(session.data.get("field") or "")
            ok, response, attachments = await _create_or_reactivate_user(
                svc,
                field,
                text,
                actor_id=user_id,
                actor_username=username,
            )
            if not ok:
                await self.client.send_message_to_user(user_id, response, format="html")
                return True
            self._clear_session(user_id)
            await self.client.send_message_to_user(
                user_id,
                response,
                attachments=attachments,
                format="html",
            )
            return True

        if session.kind == "user_edit_value":
            if text.startswith("/"):
                await self.client.send_message_to_user(
                    user_id,
                    "⚠️ Сейчас идет редактирование пользователя. Введите новое значение или /cancel.",
                    format="html",
                )
                return True
            target_user_id = int(str(session.data.get("user_id") or "0"))
            field = str(session.data.get("field") or "")
            offset = int(str(session.data.get("offset") or "0"))
            ok, response, attachments = await _save_user_field(
                svc,
                target_user_id,
                field,
                text,
                offset=offset,
                actor_id=user_id,
                actor_username=username,
            )
            if not ok:
                await self.client.send_message_to_user(user_id, response, format="html")
                return True
            self._clear_session(user_id)
            await self.client.send_message_to_user(
                user_id,
                response,
                attachments=attachments,
                format="html",
            )
            return True

        if session.kind == "server_add_name":
            ok, response, extra = await _server_add_name_step(text)
            if not ok:
                await self.client.send_message_to_user(user_id, response, format="html")
                return True
            self._set_session(user_id, "server_add_host", name=str(extra["name"]))
            await self.client.send_message_to_user(
                user_id,
                "Шаг 2/2 — Введите <b>Host</b> (IP или hostname):",
                format="html",
            )
            return True

        if session.kind == "server_add_host":
            name = str(session.data.get("name") or "")
            ok, response, extra = _server_add_host_step(text)
            if not ok:
                await self.client.send_message_to_user(user_id, response, format="html")
                return True
            self._set_session(
                user_id,
                "server_add_confirm",
                name=name,
                ssh_host=str(extra["ssh_host"]),
            )
            confirm_text, attachments = _server_add_confirm_view(svc, name, str(extra["ssh_host"]))
            await self.client.send_message_to_user(
                user_id,
                confirm_text,
                attachments=attachments,
                format="html",
            )
            return True

        if session.kind == "server_add_confirm":
            await self.client.send_message_to_user(
                user_id,
                "⚠️ Используйте кнопки подтверждения или /cancel.",
                format="html",
            )
            return True

        if session.kind == "server_edit_value":
            if text.startswith("/"):
                await self.client.send_message_to_user(
                    user_id,
                    "⚠️ Сейчас идет редактирование сервера. Введите новое значение или /cancel.",
                    format="html",
                )
                return True
            server_id = int(str(session.data.get("server_id") or "0"))
            field = str(session.data.get("field") or "")
            ok, response, attachments = await _save_server_field(
                svc,
                server_id,
                field,
                text,
                actor_id=user_id,
                actor_username=username,
            )
            if not ok:
                await self.client.send_message_to_user(user_id, response, format="html")
                return True
            self._clear_session(user_id)
            await self.client.send_message_to_user(
                user_id,
                response,
                attachments=attachments,
                format="html",
            )
            return True

        self._clear_session(user_id)
        return False

    async def _server_add_save(
        self,
        svc: ConfigService,
        user_id: int,
        username: str | None,
    ) -> tuple[str, list[dict]]:
        session = self._get_session(user_id)
        if session is None or session.kind != "server_add_confirm":
            text, attachments = await _servers_list_view()
            return text, attachments

        name = str(session.data.get("name") or "")
        ssh_host = str(session.data.get("ssh_host") or "")
        self._clear_session(user_id)
        text, attachments = await _create_server(
            svc,
            name,
            ssh_host,
            actor_id=user_id,
            actor_username=username,
        )
        return text, attachments


def _user_id(user: dict) -> int | None:
    raw = user.get("user_id")
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _admin_start_keyboard(svc: ConfigService) -> list[dict]:
    return max_inline_keyboard([[max_callback_button(svc.get_str("SOS_BUTTON_LABEL", "SOS"), "start:sos")]])


def _admin_home_text() -> str:
    return ADMIN_HOME_TEXT


def _admin_home_keyboard(svc: ConfigService) -> list[dict]:
    return max_inline_keyboard([
        [max_callback_button(svc.get_str("SOS_BUTTON_LABEL", "SOS"), "start:sos")],
        [max_callback_button(BUTTON_SERVERS, "max:servers"), max_callback_button(BUTTON_USERS, "max:users")],
        [max_callback_button(BUTTON_SETTINGS, "max:settings"), max_callback_button(BUTTON_AUDIT, "max:journal:0")],
    ])


def _sos_confirm_text(svc: ConfigService) -> str:
    label = svc.get_str("SOS_BUTTON_LABEL", "SOS")
    return sos_confirm_text(label)


def _sos_confirm_keyboard() -> list[dict]:
    return max_inline_keyboard([
        [
            max_callback_button(BUTTON_CONFIRM, "start:sos:confirm"),
            max_callback_button(BUTTON_CANCEL, "start:sos:cancel"),
        ]
    ])


async def _load_config_values() -> dict[str, str]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(Config))).scalars().all()
    return merged_config_values(rows)


def _settings_view_text(cfg: dict[str, str]) -> str:
    lines = ["⚙️ <b>Глобальные настройки</b>\n"]
    for key, (label, _, _, _) in CONFIG_NUMERIC_KEYS.items():
        lines.append(f"<code>{label:<30}</code> <b>{cfg.get(key, CONFIG_DEFAULTS[key])}</b>")
    for key, (label, _, _) in CONFIG_TEXT_KEYS.items():
        lines.append(f"<code>{label:<30}</code> <b>{cfg.get(key, CONFIG_DEFAULTS[key])}</b>")
    return "\n".join(lines)


async def _settings_view() -> tuple[str, list[dict]]:
    cfg = await _load_config_values()
    return _settings_view_text(cfg), max_inline_keyboard([
        [max_callback_button("✏️ Изменить", "max:cfg:edit")],
        [max_callback_button("◀️ Главное меню", "max:home")],
    ])


def _config_key_by_index(index: int) -> str | None:
    if 0 <= index < len(_MAX_SETTINGS_KEY_ORDER):
        return _MAX_SETTINGS_KEY_ORDER[index]
    return None


def _max_setting_label(key: str) -> str:
    return _MAX_SETTINGS_SHORT_LABELS.get(
        key,
        CONFIG_NUMERIC_KEYS.get(key, CONFIG_TEXT_KEYS.get(key, (key, "", 0)))[0],
    )


def _settings_edit_keyboard(page: int) -> list[dict]:
    total = len(_MAX_SETTINGS_KEY_ORDER)
    max_page = max(0, (total - 1) // _MAX_SETTINGS_PAGE_SIZE)
    page = max(0, min(page, max_page))
    start = page * _MAX_SETTINGS_PAGE_SIZE
    end = start + _MAX_SETTINGS_PAGE_SIZE

    rows: list[list[dict]] = []
    for index in range(start, min(end, total)):
        key = _MAX_SETTINGS_KEY_ORDER[index]
        rows.append([max_callback_button(_max_setting_label(key), f"max:cfg:k:{index}")])

    nav: list[dict] = []
    if page > 0:
        nav.append(max_callback_button("◀️ Назад", f"max:cfg:page:{page - 1}"))
    if page < max_page:
        nav.append(max_callback_button("Ещё ▶", f"max:cfg:page:{page + 1}"))
    if nav:
        rows.append(nav)
    rows.append([max_callback_button("◀️ Назад", "max:settings")])
    return max_inline_keyboard(rows)


async def _setting_edit_prompt(key: str) -> str:
    cfg = await _load_config_values()
    if key in CONFIG_TEXT_KEYS:
        label, description, max_len = CONFIG_TEXT_KEYS[key]
        current = cfg.get(key, CONFIG_DEFAULTS.get(key, ""))
        return (
            f"✏️ <b>{label}</b>\n\n"
            f"<i>{description}</i>\n\n"
            f"Текущее значение: <code>{current}</code>\n"
            f"Максимум символов: <b>{max_len}</b>\n\n"
            "Введите новое значение или /cancel для отмены."
        )

    if key in CONFIG_NUMERIC_KEYS:
        label, description, min_val, max_val = CONFIG_NUMERIC_KEYS[key]
        current = cfg.get(key, CONFIG_DEFAULTS[key])
        return (
            f"✏️ <b>{label}</b>\n\n"
            f"<i>{description}</i>\n\n"
            f"Текущее значение: <code>{current}</code>\n"
            f"Допустимый диапазон: <b>{min_val} – {max_val}</b>\n\n"
            "Введите новое значение или /cancel для отмены."
        )

    return "⚠️ Неизвестный параметр."


async def _save_config_value(
    svc: ConfigService,
    actor_id: int,
    actor_username: str | None,
    key: str,
    raw: str,
    *,
    platform: str,
) -> tuple[bool, str]:
    value = raw.strip()
    if key in CONFIG_TEXT_KEYS:
        label, _, max_len = CONFIG_TEXT_KEYS[key]
        if not value or len(value) > max_len:
            return False, (
                f"⚠️ Значение не должно быть пустым и длиннее {max_len} символов. Попробуйте ещё раз:"
            )
        saved_value = value
    elif key in CONFIG_NUMERIC_KEYS:
        label, _, min_val, max_val = CONFIG_NUMERIC_KEYS[key]
        if not value.lstrip("-").isdigit():
            return False, (
                f"⚠️ Значение должно быть целым числом ({min_val}–{max_val}). Попробуйте ещё раз:"
            )
        parsed = int(value)
        if not (min_val <= parsed <= max_val):
            return False, (
                f"⚠️ Значение должно быть в диапазоне {min_val}–{max_val}. Попробуйте ещё раз:"
            )
        saved_value = str(parsed)
    else:
        return False, "⚠️ Неизвестный параметр."

    async with AsyncSessionLocal() as session:
        row = await session.get(Config, key)
        if row:
            row.value = saved_value
        else:
            session.add(Config(key=key, value=saved_value))
        await log_action(session, actor_id, actor_username, "config:set", f"{key}={saved_value}", platform=platform)
        await session.commit()

    await svc.reload()
    return True, f"✅ <b>{label}</b> установлен в <code>{saved_value}</code>."


def _max_user_button_label(user: User) -> str:
    icon = "✅" if user.is_active else "🚫"
    title = _user_title(user)
    ref = _user_ref(user)
    label = f"{icon} {title}" if title == ref else f"{icon} {title} · {ref}"
    return _truncate_button_text(label)


def _user_add_platform_keyboard() -> list[dict]:
    return max_inline_keyboard([
        [max_callback_button("Telegram ID", "max:usr:add:tg")],
        [max_callback_button("MAX ID", "max:usr:add:max")],
        [max_callback_button("◀️ Назад", "max:users")],
    ])


async def _users_list_view(offset: int = 0) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count()).select_from(User))).scalar_one()
        if total:
            max_offset = ((total - 1) // _MAX_USERS_PAGE_SIZE) * _MAX_USERS_PAGE_SIZE
            offset = max(0, min(offset, max_offset))
        else:
            offset = 0
        users = (
            await session.execute(
                select(User).order_by(User.created_at).offset(offset).limit(_MAX_USERS_PAGE_SIZE)
            )
        ).scalars().all()

    if not users:
        return (
            "👥 <b>Пользователи</b>\n\n<i>Нет зарегистрированных пользователей.</i>",
            max_inline_keyboard([
                [max_callback_button("➕ Добавить", "max:usr:add")],
                [max_callback_button("◀️ Главное меню", "max:home")],
            ]),
        )

    page_num = offset // _MAX_USERS_PAGE_SIZE + 1
    page_max = (total + _MAX_USERS_PAGE_SIZE - 1) // _MAX_USERS_PAGE_SIZE
    text = f"👥 <b>Пользователи</b> <i>(стр. {page_num}/{page_max}, всего: {total})</i>"

    rows: list[list[dict]] = []
    for user in users:
        rows.append([max_callback_button(_max_user_button_label(user), f"max:usr:view:{user.id}:{offset}")])

    nav: list[dict] = []
    if offset > 0:
        nav.append(max_callback_button("◀️ Назад", f"max:users:page:{max(0, offset - _MAX_USERS_PAGE_SIZE)}"))
    if offset + _MAX_USERS_PAGE_SIZE < total:
        nav.append(max_callback_button("Ещё ▶", f"max:users:page:{offset + _MAX_USERS_PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append([max_callback_button("➕ Добавить", "max:usr:add")])
    rows.append([max_callback_button("◀️ Главное меню", "max:home")])
    return text, max_inline_keyboard(rows)


def _user_card_keyboard(user_id: int, offset: int, is_active: bool) -> list[dict]:
    return max_inline_keyboard([
        [
            max_callback_button("Редактировать", f"max:usr:edit:{user_id}:{offset}"),
            max_callback_button("Отключить" if is_active else "Включить", f"max:usr:toggle:{user_id}:{offset}"),
        ],
        [max_callback_button("◀️ Список", f"max:users:page:{offset}")],
    ])


async def _user_card_view(user_id: int, offset: int = 0) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if not user:
        return (
            "⚠️ Пользователь не найден.",
            max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:users:page:{offset}")]]),
        )
    return _user_card_text(user), _user_card_keyboard(user_id, offset, user.is_active)


async def _user_edit_menu(user_id: int, offset: int) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if not user:
        return (
            "⚠️ Пользователь не найден.",
            max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:users:page:{offset}")]]),
        )
    return (
        f"✏️ <b>Редактировать: {_user_title(user)}</b>\n\nЧто изменить?",
        max_inline_keyboard([
            [max_callback_button("Реальное имя", f"max:usr:editf:{user_id}:display_name:{offset}")],
            [max_callback_button("Telegram ID", f"max:usr:editf:{user_id}:telegram_id:{offset}")],
            [max_callback_button("MAX ID", f"max:usr:editf:{user_id}:max_user_id:{offset}")],
            [max_callback_button("◀️ Назад", f"max:usr:view:{user_id}:{offset}")],
        ]),
    )


async def _user_edit_prompt(user_id: int, field: str) -> str:
    if field not in {"display_name", "telegram_id", "max_user_id"}:
        return "⚠️ Неизвестное поле."

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    if not user:
        return "⚠️ Пользователь не найден."

    current = ""
    if field == "display_name":
        current = (user.display_name or "").strip()
    elif field == "telegram_id":
        current = str(user.telegram_id) if user.telegram_id is not None else ""
    elif field == "max_user_id":
        current = str(user.max_user_id) if user.max_user_id is not None else ""

    return (
        f"✏️ <b>{_platform_field_label(field)}</b>\n\n"
        f"Текущее значение: <code>{current or '—'}</code>\n\n"
        f"{_platform_field_hint(field)}"
        "Введите новое значение или /cancel для отмены."
    )


async def _create_or_reactivate_user(
    svc: ConfigService,
    field: str,
    raw: str,
    *,
    actor_id: int,
    actor_username: str | None,
) -> tuple[bool, str, list[dict]]:
    if field not in {"telegram_id", "max_user_id"}:
        return False, "⚠️ Неизвестная платформа.", []

    value = raw.strip()
    if not value.lstrip("-").isdigit():
        return False, f"⚠️ {_platform_field_label(field)} должен быть целым числом. Попробуйте ещё раз:", []

    platform_id = int(value)
    label = _platform_field_label(field)
    success_hint = (
        "Теперь он сможет использовать /admin."
        if field == "telegram_id"
        else "Теперь он сможет использовать /admin в MAX."
    )

    async with AsyncSessionLocal() as session:
        column = User.telegram_id if field == "telegram_id" else User.max_user_id
        existing = (await session.execute(select(User).where(column == platform_id))).scalar_one_or_none()

        if existing:
            target_user_id = int(existing.id)
            if existing.is_active:
                message = f"ℹ️ Пользователь с {label} <code>{platform_id}</code> уже активен."
            else:
                existing.is_active = True
                await log_action(
                    session,
                    actor_id,
                    actor_username,
                    "user:reactivate",
                    f"target={_user_ref(existing)}",
                    platform="max",
                )
                await session.commit()
                message = f"✅ Пользователь с {label} <code>{platform_id}</code> повторно активирован."
                await svc.reload()
                text, attachments = await _user_card_view(target_user_id, 0)
                return True, f"{message}\n\n{text}", attachments

            text, attachments = await _user_card_view(target_user_id, 0)
            return True, f"{message}\n\n{text}", attachments

        user = User(is_active=True)
        if field == "telegram_id":
            user.telegram_id = platform_id
        else:
            user.max_user_id = platform_id
        session.add(user)
        await session.flush()
        target_user_id = int(user.id)
        await log_action(
            session,
            actor_id,
            actor_username,
            "user:add",
            f"target={label}:{platform_id}",
            platform="max",
        )
        await session.commit()

    await svc.reload()
    text, attachments = await _user_card_view(target_user_id, 0)
    return True, f"✅ Пользователь с {label} <code>{platform_id}</code> добавлен.\n{success_hint}\n\n{text}", attachments


async def _save_user_field(
    svc: ConfigService,
    user_id: int,
    field: str,
    raw: str,
    *,
    offset: int,
    actor_id: int,
    actor_username: str | None,
) -> tuple[bool, str, list[dict]]:
    value = raw.strip()[:128]
    if field not in {"display_name", "telegram_id", "max_user_id"}:
        return False, "⚠️ Неизвестное поле.", []

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return (
                False,
                "⚠️ Пользователь не найден.",
                max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:users:page:{offset}")]]),
            )

        if field == "display_name":
            user.display_name = value if value else None
        elif field in {"telegram_id", "max_user_id"}:
            if field == "max_user_id" and user.max_user_id == actor_id and user.is_active and value != str(actor_id):
                return False, "⛔ Нельзя менять собственный MAX ID из MAX-сессии.", []

            if value:
                if not value.lstrip("-").isdigit():
                    return False, f"⚠️ {_platform_field_label(field)} должен быть целым числом.", []
                parsed = int(value)
                column = User.telegram_id if field == "telegram_id" else User.max_user_id
                existing = (
                    await session.execute(select(User).where(column == parsed, User.id != user_id))
                ).scalar_one_or_none()
                if existing:
                    return False, f"⚠️ Такой {_platform_field_label(field)} уже привязан к другому пользователю.", []
                changed = getattr(user, field) != parsed
                setattr(user, field, parsed)
                if changed:
                    if field == "telegram_id":
                        user.username = None
                        user.first_name = None
                    else:
                        user.max_username = None
                        user.max_first_name = None
            else:
                if field == "max_user_id" and user.max_user_id == actor_id and user.is_active:
                    return False, "⛔ Нельзя очистить собственный MAX ID из MAX-сессии.", []
                other_id = user.max_user_id if field == "telegram_id" else user.telegram_id
                if other_id is None:
                    return False, "⛔ У пользователя должен остаться хотя бы один platform ID.", []
                setattr(user, field, None)
                if field == "telegram_id":
                    user.username = None
                    user.first_name = None
                else:
                    user.max_username = None
                    user.max_first_name = None

        await log_action(
            session,
            actor_id,
            actor_username,
            "user:edit",
            f"target={_user_ref(user)} field={field} value={value!r}",
            platform="max",
        )
        await session.commit()

    await svc.reload()
    text, attachments = await _user_card_view(user_id, offset)
    return True, f"✅ Поле <b>{_platform_field_label(field)}</b> обновлено.\n\n{text}", attachments


async def _toggle_user(
    user_id: int,
    offset: int,
    svc: ConfigService,
    actor_id: int,
    actor_username: str | None,
) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return (
                "⚠️ Пользователь не найден.",
                max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:users:page:{offset}")]]),
            )

        if user.max_user_id is not None and user.max_user_id == actor_id and user.is_active:
            text, attachments = await _user_card_view(user_id, offset)
            return f"⛔ Нельзя отключить самого себя.\n\n{text}", attachments

        if user.is_active:
            active_count = (
                await session.execute(select(func.count()).where(User.is_active == True))  # noqa: E712
            ).scalar_one()
            if active_count <= 1:
                text, attachments = await _user_card_view(user_id, offset)
                return f"⛔ Нельзя отключить последнего активного пользователя.\n\n{text}", attachments

        user.is_active = not user.is_active
        is_active = user.is_active
        action = "user:activate" if user.is_active else "user:deactivate"
        await log_action(
            session,
            actor_id,
            actor_username,
            action,
            f"target={_user_ref(user)}",
            platform="max",
        )
        await session.commit()

    await svc.reload()
    text, attachments = await _user_card_view(user_id, offset)
    status_text = "✅ Пользователь включён." if is_active else "🚫 Пользователь отключён."
    return f"{status_text}\n\n{text}", attachments


def _valid_name(value: str) -> bool:
    stripped = value.strip()
    return bool(stripped) and len(stripped) <= 64 and re.fullmatch(r"[A-Za-z0-9\-]+", stripped) is not None


def _valid_host(value: str) -> bool:
    stripped = value.strip()
    if not stripped or len(stripped) > 255:
        return False
    if _IP_RE.fullmatch(stripped):
        try:
            return all(0 <= int(part) <= 255 for part in stripped.split("."))
        except ValueError:
            return False
    return _HOSTNAME_RE.fullmatch(stripped) is not None


def _server_editable_field_label(field: str) -> str | None:
    return {
        "name": "Имя сервера (латиница, цифры, дефис)",
        "ssh_host": "Host (IP или hostname)",
        "ssh_fingerprint": "Fingerprint (или пустую строку, чтобы очистить)",
    }.get(field)


async def _server_add_name_step(raw: str) -> tuple[bool, str, dict[str, str]]:
    value = raw.strip()
    if not _valid_name(value):
        return (
            False,
            "⚠️ Имя должно содержать только латиницу, цифры и дефис, не длиннее 64 символов. Повторите:",
            {},
        )

    async with AsyncSessionLocal() as session:
        exists = (await session.execute(select(Server).where(Server.name == value))).scalar_one_or_none()
    if exists:
        return False, f"⚠️ Сервер с именем <b>{value}</b> уже существует. Введите другое имя:", {}

    return True, "", {"name": value}


def _server_add_host_step(raw: str) -> tuple[bool, str, dict[str, str]]:
    value = raw.strip()
    if not _valid_host(value):
        return False, "⚠️ Некорректный IP или hostname. Попробуйте ещё раз:", {}
    return True, "", {"ssh_host": value}


def _server_add_confirm_view(svc: ConfigService, name: str, ssh_host: str) -> tuple[str, list[dict]]:
    text = (
        "📋 <b>Проверьте данные нового сервера:</b>\n\n"
        f"Имя: <code>{name}</code>\n"
        f"Host: <code>{ssh_host}</code>\n"
        f"SSH user: <code>{svc.get_str('SSH_DEFAULT_USER', 'bot-admin')}</code>\n"
        f"Ключ: <code>{svc.get_str('SSH_DEFAULT_KEY_PATH', '~/.ssh/id_ed25519_bot')}</code>\n\n"
        "Всё верно?"
    )
    attachments = max_inline_keyboard([
        [
            max_callback_button("✅ Сохранить", "max:srv:add:save"),
            max_callback_button("❌ Отмена", "max:srv:add:cancel"),
        ]
    ])
    return text, attachments


async def _create_server(
    svc: ConfigService,
    name: str,
    ssh_host: str,
    *,
    actor_id: int,
    actor_username: str | None,
) -> tuple[str, list[dict]]:
    if not _valid_name(name):
        return await _servers_list_view()
    if not _valid_host(ssh_host):
        return await _servers_list_view()

    async with AsyncSessionLocal() as session:
        exists = (await session.execute(select(Server).where(Server.name == name))).scalar_one_or_none()
        if exists:
            return (
                f"⚠️ Сервер <b>{name}</b> уже существует.",
                max_inline_keyboard([
                    [max_callback_button("◀️ Список серверов", "max:servers")],
                ]),
            )

        first_server = svc.get_first_server()
        server = Server(
            name=name,
            ssh_host=ssh_host,
            ssh_user=svc.get_str("SSH_DEFAULT_USER", "bot-admin"),
            ssh_key_path=svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot"),
            ssh_known_hosts_path=(
                first_server.ssh_known_hosts_path if first_server else "~/.ssh/known_hosts"
            ),
            ping_host=ssh_host,
            is_active=True,
        )
        session.add(server)
        await log_action(session, actor_id, actor_username, "server:add", f"server={name}", platform="max")
        await session.commit()
        server_id = int(server.id)

    await svc.reload()
    text, attachments = await _server_card_view(server_id, svc)
    return f"✅ Сервер <b>{name}</b> добавлен и активирован.\n\n{text}", attachments


async def _server_edit_prompt(server_id: int, field: str) -> str:
    label = _server_editable_field_label(field)
    if label is None:
        return "⚠️ Неизвестное поле."

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        return "⚠️ Сервер не найден."

    current = getattr(server, field, None) or "—"
    return (
        f"✏️ <b>{label}</b>\n\n"
        f"Текущее значение: <code>{current}</code>\n\n"
        "Введите новое значение или /cancel для отмены."
    )


async def _server_edit_menu(server_id: int) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])

    return (
        f"✏️ <b>Редактировать: {server.name}</b>\n\nЧто изменить?",
        max_inline_keyboard([
            [max_callback_button("Имя", f"max:srv:editf:{server_id}:name")],
            [max_callback_button("Host", f"max:srv:editf:{server_id}:ssh_host")],
            [max_callback_button("Fingerprint", f"max:srv:editf:{server_id}:ssh_fingerprint")],
            [max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")],
        ]),
    )


async def _save_server_field(
    svc: ConfigService,
    server_id: int,
    field: str,
    raw: str,
    *,
    actor_id: int,
    actor_username: str | None,
) -> tuple[bool, str, list[dict]]:
    value = raw.strip()
    label = _server_editable_field_label(field)
    if label is None:
        return False, "⚠️ Неизвестное поле.", []

    if field == "name":
        if not _valid_name(value):
            return (
                False,
                "⚠️ Имя должно содержать только латиницу, цифры и дефис (до 64 символов).",
                [],
            )
        async with AsyncSessionLocal() as session:
            existing = (
                await session.execute(
                    select(Server).where(
                        Server.name == value,
                        Server.id != server_id,
                    )
                )
            ).scalar_one_or_none()
        if existing:
            return False, "⚠️ Сервер с таким именем уже существует.", []
    elif field == "ssh_host":
        if not _valid_host(value):
            return False, "⚠️ Некорректный IP или hostname. Попробуйте ещё раз:", []

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return (
                False,
                "⚠️ Сервер не найден.",
                max_inline_keyboard([[max_callback_button("◀️ Список серверов", "max:servers")]]),
            )
        setattr(server, field, value if value else None)
        if field == "ssh_host":
            server.ping_host = value
        await log_action(
            session,
            actor_id,
            actor_username,
            "server:edit",
            f"server={server.name} field={field}",
            platform="max",
        )
        await session.commit()

    await svc.reload()
    text, attachments = await _server_card_view(server_id, svc)
    return True, f"✅ Поле <b>{label}</b> обновлено.\n\n{text}", attachments


def _reach_icon(server: Server) -> str:
    if not server.is_active:
        return "⛔"
    if server.last_ping_ok is None:
        return "🔵"
    return "🟢" if server.last_ping_ok else "🟡"


def _reach_text(server: Server) -> str:
    if server.last_ping_ok is None:
        return "⚪ Не проверялся"
    ts = server.last_ping_at.strftime("%d.%m %H:%M") if server.last_ping_at else "—"
    if server.last_ping_ok:
        return f"🟢 Онлайн ({ts})"
    return f"🔴 Недоступен ({ts})"


async def _load_servers_split() -> tuple[list[Server], list[Server]]:
    async with AsyncSessionLocal() as session:
        servers = (await session.execute(select(Server).order_by(Server.name))).scalars().all()
    active = [server for server in servers if server.is_active]
    inactive = [server for server in servers if not server.is_active]
    return active, inactive


async def _servers_list_view(*, show_inactive: bool = False) -> tuple[str, list[dict]]:
    active_servers, inactive_servers = await _load_servers_split()
    visible_servers = inactive_servers if show_inactive else active_servers

    if show_inactive:
        text = "🖥 <b>Неактивные серверы</b>"
        if not inactive_servers:
            text += "\n\n<i>Неактивных серверов пока нет.</i>"
        else:
            text += f"\n\nВсего: <b>{len(inactive_servers)}</b>"
    else:
        text = "🖥 <b>Серверы</b>\n\n"
        text += f"Активные: <b>{len(active_servers)}</b>"
        if inactive_servers:
            text += f"\nНеактивные: <b>{len(inactive_servers)}</b>"
        if not active_servers:
            text += "\n\n<i>Активных серверов пока нет.</i>"

    rows: list[list[dict]] = []
    for server in visible_servers:
        rows.append([max_callback_button(f"{_reach_icon(server)} {server.name}", f"max:srv:view:{server.id}")])
    if show_inactive:
        rows.append([max_callback_button("◀️ Активные серверы", "max:servers")])
    elif inactive_servers:
        rows.append([max_callback_button(f"📦 Неактивные ({len(inactive_servers)})", "max:servers:inactive")])
    rows.append([max_callback_button("➕ Добавить сервер", "max:srv:add")])
    rows.append([max_callback_button("◀️ Главное меню", "max:home")])
    return text, max_inline_keyboard(rows)


async def _server_card_view(server_id: int, svc: ConfigService) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])

    text = (
        f"🖥 <b>{server.name}</b>\n\n"
        f"Host: <code>{server.ssh_host}</code>\n"
        f"SSH: <code>{svc.get_str('SSH_DEFAULT_USER', 'bot-admin')}@{server.ssh_host}</code>\n"
        f"Ключ: <code>{svc.get_str('SSH_DEFAULT_KEY_PATH', '~/.ssh/id_ed25519_bot')}</code>\n"
        f"Known hosts: <code>{server.ssh_known_hosts_path}</code>\n"
        f"Fingerprint: <code>{server.ssh_fingerprint or '—'}</code>\n"
        f"В работе: {'Да ✅' if server.is_active else 'Нет ⛔'}\n"
        f"Доступность: {_reach_text(server)}"
    )
    attachments = max_inline_keyboard([
        [
            max_callback_button("Проверить", f"max:srv:check:{server_id}"),
            max_callback_button("Редактировать", f"max:srv:edit:{server_id}"),
        ],
        [
            max_callback_button("Выключить", f"max:srv:poweroff:{server_id}"),
            max_callback_button("Перезагрузка", f"max:srv:reboot:{server_id}"),
        ],
        [
            max_callback_button("Отключить" if server.is_active else "Включить", f"max:srv:toggle:{server_id}"),
            max_callback_button("Удалить", f"max:srv:del:{server_id}"),
        ],
        [max_callback_button("◀️ Список серверов", "max:servers")],
    ])
    return text, attachments


async def _toggle_server(
    server_id: int,
    svc: ConfigService,
    actor_id: int,
    actor_username: str | None,
) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])
        server.is_active = not server.is_active
        is_active = server.is_active
        action = "server:activate" if server.is_active else "server:deactivate"
        await log_action(session, actor_id, actor_username, action, f"server={server.name}", platform="max")
        await session.commit()

    await svc.reload()
    text, attachments = await _server_card_view(server_id, svc)
    return (
        f"{'✅ Сервер включён.' if is_active else '🔴 Сервер отключён.'}\n\n{text}",
        attachments,
    )


async def _delete_server_confirm(server_id: int) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])

    return (
        f"🗑 Удалить сервер <b>{server.name}</b>?\n\n"
        "Сервер будет <b>полностью удалён</b> из системы. Действие необратимо.",
        max_inline_keyboard([
            [
                max_callback_button("Да, удалить", f"max:srv:del:{server_id}:yes"),
                max_callback_button(BUTTON_CANCEL, f"max:srv:view:{server_id}"),
            ]
        ]),
    )


async def _delete_server(
    server_id: int,
    svc: ConfigService,
    actor_id: int,
    actor_username: str | None,
) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])
        was_active = server.is_active
        server_name = server.name
        await log_action(session, actor_id, actor_username, "server:delete", f"server={server_name}", platform="max")
        await session.delete(server)
        await session.commit()

    await svc.reload()
    text, attachments = await _servers_list_view(show_inactive=not was_active)
    return f"✅ Сервер полностью удалён.\n\n{text}", attachments


async def _check_server_now(server_id: int, svc: ConfigService) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])
        ping_host = server.ssh_host
        ping_count = server.ping_count
        ping_timeout = server.ping_timeout

    ping_template = svc.get_str("PING_CMD_TEMPLATE", "")
    template = ping_template if (ping_template and "{timeout}" in ping_template and "{host}" in ping_template) else None
    try:
        ok, _ = await asyncio.to_thread(ping_status, ping_host, ping_count, ping_timeout, template)
        reachable = ok > 0
    except Exception:
        reachable = False

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])
        server.last_ping_ok = reachable
        server.last_ping_at = datetime.utcnow()
        await session.commit()

    await svc.reload()
    return await _server_card_view(server_id, svc)


async def _poweroff_confirm_text(server_id: int) -> str:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        return "⚠️ Сервер не найден."
    return f"⚡ Выключить сервер <b>{server.name}</b>?\n\nБудет отправлена команда жёсткого выключения."


def _poweroff_confirm_keyboard(server_id: int) -> list[dict]:
    return max_inline_keyboard([
        [
            max_callback_button("Да, выключить", f"max:srv:poweroff:{server_id}:yes"),
            max_callback_button(BUTTON_CANCEL, f"max:srv:view:{server_id}"),
        ]
    ])


async def _poweroff_server(
    server_id: int,
    svc: ConfigService,
    actor_id: int,
    actor_username: str | None,
) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])
        name = server.name
        host = server.ssh_host
        known_hosts = server.ssh_known_hosts_path
        fingerprint = server.ssh_fingerprint or ""
        connect_timeout = server.ssh_connect_timeout
        command_timeout = server.ssh_command_timeout

    command = ps(svc.get_str("SSH_CMD_POWEROFF", "shutdown /p /f"))
    try:
        rc, _, err = await asyncio.to_thread(
            ssh_exec,
            host=host,
            user=svc.get_str("SSH_DEFAULT_USER", "bot-admin"),
            key_path=svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot"),
            known_hosts_path=known_hosts,
            pinned_fingerprint_md5=fingerprint,
            connect_timeout=connect_timeout,
            command_timeout=command_timeout,
            cmd=command,
        )
    except Exception as exc:
        await svc.write_audit(actor_id, actor_username, "poweroff", f"server={name} error={exc}", platform="max")
        return (
            f"❌ SSH-ошибка: {exc}",
            max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")]]),
        )

    await svc.write_audit(actor_id, actor_username, "poweroff", f"server={name} rc={rc}", platform="max")
    if rc != 0:
        return (
            f"⚠️ Команда вернула rc={rc}\nstderr:\n{(err or '')[:400]}",
            max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")]]),
        )

    return (
        f"✅ Команда принята. Сервер <b>{name}</b> сейчас выключится.",
        max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")]]),
    )


async def _reboot_confirm_text(server_id: int) -> str:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        return "⚠️ Сервер не найден."
    return f"🔄 Перезагрузить сервер <b>{server.name}</b>?\n\nБудет отправлена команда перезагрузки."


def _reboot_confirm_keyboard(server_id: int) -> list[dict]:
    return max_inline_keyboard([
        [
            max_callback_button("Да, перезагрузить", f"max:srv:reboot:{server_id}:yes"),
            max_callback_button(BUTTON_CANCEL, f"max:srv:view:{server_id}"),
        ]
    ])


async def _reboot_server(
    server_id: int,
    svc: ConfigService,
    actor_id: int,
    actor_username: str | None,
) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return "⚠️ Сервер не найден.", max_inline_keyboard([[max_callback_button("◀️ Назад", "max:servers")]])
        name = server.name
        host = server.ssh_host
        known_hosts = server.ssh_known_hosts_path
        fingerprint = server.ssh_fingerprint or ""
        connect_timeout = server.ssh_connect_timeout
        command_timeout = server.ssh_command_timeout

    command = ps(svc.get_str("SSH_CMD_REBOOT", "shutdown /r /t 0 /f"))
    try:
        rc, _, err = await asyncio.to_thread(
            ssh_exec,
            host=host,
            user=svc.get_str("SSH_DEFAULT_USER", "bot-admin"),
            key_path=svc.get_str("SSH_DEFAULT_KEY_PATH", "~/.ssh/id_ed25519_bot"),
            known_hosts_path=known_hosts,
            pinned_fingerprint_md5=fingerprint,
            connect_timeout=connect_timeout,
            command_timeout=command_timeout,
            cmd=command,
        )
    except Exception as exc:
        await svc.write_audit(actor_id, actor_username, "reboot", f"server={name} error={exc}", platform="max")
        return (
            f"❌ SSH-ошибка: {exc}",
            max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")]]),
        )

    await svc.write_audit(actor_id, actor_username, "reboot", f"server={name} rc={rc}", platform="max")
    if rc != 0:
        return (
            f"⚠️ Команда вернула rc={rc}\nstderr:\n{(err or '')[:400]}",
            max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")]]),
        )

    return (
        f"✅ Команда принята. Сервер <b>{name}</b> уходит в перезагрузку.",
        max_inline_keyboard([[max_callback_button("◀️ Назад", f"max:srv:view:{server_id}")]]),
    )


def _journal_row(entry: AuditLog) -> str:
    return format_audit_row(entry)


async def _journal_view(offset: int) -> tuple[str, list[dict]]:
    async with AsyncSessionLocal() as session:
        total = (await session.execute(select(func.count()).select_from(AuditLog))).scalar_one()
        entries = (
            await session.execute(
                select(AuditLog).order_by(desc(AuditLog.created_at)).offset(offset).limit(_PAGE_SIZE)
            )
        ).scalars().all()

    if not entries:
        return "📋 <b>Журнал пуст.</b>", max_inline_keyboard([[max_callback_button("◀️ Главное меню", "max:home")]])

    page_num = offset // _PAGE_SIZE + 1
    page_max = (total + _PAGE_SIZE - 1) // _PAGE_SIZE
    text = [f"📋 <b>Журнал действий</b> <i>(стр. {page_num}/{page_max}, всего: {total})</i>\n"]
    for entry in entries:
        text.append(_journal_row(entry))

    rows: list[list[dict]] = []
    nav: list[dict] = []
    if offset > 0:
        nav.append(max_callback_button("◀️ Назад", f"max:journal:{max(0, offset - _PAGE_SIZE)}"))
    if offset + _PAGE_SIZE < total:
        nav.append(max_callback_button(BUTTON_NEXT_10, f"max:journal:{offset + _PAGE_SIZE}"))
    if nav:
        rows.append(nav)
    rows.append([max_callback_button("◀️ Главное меню", "max:home")])
    return "\n".join(text), max_inline_keyboard(rows)
