from __future__ import annotations

import asyncio
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class MaxApiError(RuntimeError):
    pass


class MaxApiClient:
    def __init__(self, token: str, base_url: str = "https://platform-api.max.ru") -> None:
        self._token = (token or "").strip()
        self._base_url = (base_url or "https://platform-api.max.ru").rstrip("/")

    @property
    def enabled(self) -> bool:
        return bool(self._token)

    async def get_me(self) -> dict:
        return await asyncio.to_thread(self._request_json, "GET", "/me")

    async def list_subscriptions(self) -> dict:
        return await asyncio.to_thread(self._request_json, "GET", "/subscriptions")

    async def create_subscription(
        self,
        *,
        url: str,
        update_types: list[str] | None = None,
        secret: str | None = None,
    ) -> dict:
        body: dict[str, object] = {"url": url}
        if update_types:
            body["update_types"] = update_types
        if secret:
            body["secret"] = secret
        return await asyncio.to_thread(self._request_json, "POST", "/subscriptions", body=body)

    async def delete_subscription(self, *, url: str) -> dict:
        return await asyncio.to_thread(
            self._request_json,
            "DELETE",
            "/subscriptions",
            params={"url": url},
        )

    async def get_updates(
        self,
        *,
        marker: int | None = None,
        timeout: int = 30,
        limit: int = 100,
        types: list[str] | None = None,
    ) -> dict:
        params: dict[str, str] = {
            "timeout": str(timeout),
            "limit": str(limit),
        }
        if marker is not None:
            params["marker"] = str(marker)
        if types:
            params["types"] = ",".join(types)
        return await asyncio.to_thread(self._request_json, "GET", "/updates", params=params)

    async def send_message_to_user(
        self,
        user_id: int,
        text: str,
        *,
        format: str = "html",
        attachments: list[dict] | None = None,
        notify: bool = True,
    ) -> dict:
        return await asyncio.to_thread(
            self._request_json,
            "POST",
            "/messages",
            params={"user_id": str(user_id)},
            body={
                "text": text,
                "format": format,
                "notify": notify,
                "attachments": attachments or [],
            },
        )

    async def answer_callback(
        self,
        callback_id: str,
        *,
        text: str | None = None,
        format: str = "html",
        attachments: list[dict] | None = None,
        notification: str | None = None,
    ) -> dict:
        payload: dict[str, object] = {}
        if text is not None or attachments is not None:
            payload["message"] = {
                "text": text,
                "format": format,
                "attachments": attachments or [],
            }
        if notification:
            payload["notification"] = notification
        return await asyncio.to_thread(
            self._request_json,
            "POST",
            "/answers",
            params={"callback_id": callback_id},
            body=payload,
        )

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: dict | None = None,
    ) -> dict:
        if not self.enabled:
            raise MaxApiError("MAX API token is not configured")

        query = f"?{urlencode(params)}" if params else ""
        data = None
        headers = {"Authorization": self._token}
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"

        request = Request(
            f"{self._base_url}{path}{query}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with urlopen(request, timeout=timeout_for(method, params)) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            details = exc.read().decode("utf-8", "ignore")
            raise MaxApiError(f"MAX API HTTP {exc.code}: {details}") from exc
        except URLError as exc:
            raise MaxApiError(f"MAX API connection error: {exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise MaxApiError(f"MAX API invalid JSON response: {raw}") from exc


def timeout_for(method: str, params: dict[str, str] | None) -> int:
    if method == "GET" and params and "timeout" in params:
        try:
            return int(params["timeout"]) + 15
        except (TypeError, ValueError):
            return 45
    return 20


def max_callback_button(text: str, payload: str) -> dict:
    return {"type": "callback", "text": text, "payload": payload}


def max_inline_keyboard(rows: list[list[dict]]) -> list[dict]:
    return [{"type": "inline_keyboard", "payload": {"buttons": rows}}]
