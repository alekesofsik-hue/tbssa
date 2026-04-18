from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class TelegramApiError(RuntimeError):
    pass


class TelegramApi:
    def __init__(self, token: str) -> None:
        self._token = (token or "").strip()
        self._base_url = f"https://api.telegram.org/bot{self._token}"

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        parse_mode: str = "HTML",
    ) -> dict:
        if not self.enabled:
            raise TelegramApiError("Telegram API token is not configured")
        payload = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": parse_mode,
        }
        return await asyncio.to_thread(self._request_json, "sendMessage", payload)

    def _request_json(self, method: str, payload: dict) -> dict:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{self._base_url}/{method}",
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        try:
            with urlopen(request, timeout=20) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", "ignore")
            raise TelegramApiError(f"Telegram API HTTP {exc.code}: {details}") from exc
        except URLError as exc:
            raise TelegramApiError(f"Telegram API connection error: {exc}") from exc

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TelegramApiError(f"Telegram API invalid JSON response: {raw}") from exc

        if not data.get("ok"):
            raise TelegramApiError(f"Telegram API error response: {data}")
        return data
