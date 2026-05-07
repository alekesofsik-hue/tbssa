from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

log = logging.getLogger("tbssa")


@dataclass(frozen=True)
class MaxWebhookConfig:
    public_url: str
    bind_host: str
    bind_port: int
    path: str
    secret: str = ""


class _ReusableThreadingHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class MaxWebhookServer:
    def __init__(self, config: MaxWebhookConfig, accept_update: Callable[[dict], bool]) -> None:
        self._config = config
        self._accept_update = accept_update
        self._server: _ReusableThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        handler = self._build_handler()
        self._server = _ReusableThreadingHTTPServer(
            (self._config.bind_host, self._config.bind_port),
            handler,
        )
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.5},
            name="tbssa-max-webhook",
            daemon=True,
        )
        self._thread.start()
        log.info(
            "[max] webhook listener started on http://%s:%s%s",
            self._config.bind_host,
            self._config.bind_port,
            self._config.path,
        )

    def stop(self) -> None:
        if not self._server:
            return
        self._server.shutdown()
        self._server.server_close()
        self._server = None

    def _build_handler(self) -> type[BaseHTTPRequestHandler]:
        config = self._config
        accept_update = self._accept_update

        class Handler(BaseHTTPRequestHandler):
            server_version = "tbssa-max-webhook"
            sys_version = ""

            def do_POST(self) -> None:  # noqa: N802
                if self.path != config.path:
                    self.send_error(HTTPStatus.NOT_FOUND)
                    return

                if config.secret:
                    actual_secret = self.headers.get("X-Max-Bot-Api-Secret", "")
                    if actual_secret != config.secret:
                        log.warning("[max] webhook rejected request with invalid secret")
                        self.send_error(HTTPStatus.FORBIDDEN)
                        return

                try:
                    content_length = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(HTTPStatus.BAD_REQUEST, "invalid content length")
                    return
                if content_length <= 0:
                    self.send_error(HTTPStatus.BAD_REQUEST, "empty body")
                    return

                try:
                    raw = self.rfile.read(content_length)
                    update = json.loads(raw.decode("utf-8"))
                except (OSError, UnicodeDecodeError, json.JSONDecodeError):
                    self.send_error(HTTPStatus.BAD_REQUEST, "invalid json")
                    return

                if not isinstance(update, dict):
                    self.send_error(HTTPStatus.BAD_REQUEST, "invalid payload")
                    return

                if not accept_update(update):
                    self.send_error(HTTPStatus.SERVICE_UNAVAILABLE, "runtime unavailable")
                    return

                self.send_response(HTTPStatus.OK)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"ok")

            def do_GET(self) -> None:  # noqa: N802
                self.send_error(HTTPStatus.METHOD_NOT_ALLOWED)

            def log_message(self, format: str, *args: object) -> None:
                log.debug("[max] webhook http: " + format, *args)

        return Handler
