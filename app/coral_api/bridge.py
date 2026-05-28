"""Local HTTP bridge server exposing @coralapi functions to Coral."""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

from app.coral_api.registry import CoralApiRegistry

logger = logging.getLogger(__name__)


def _silent_log(*args: Any, **kwargs: Any) -> None:
    pass


class CoralBridgeServer:
    """Minimal HTTP server exposing @coralapi functions."""

    def __init__(self, host: str = "127.0.0.1", port: int = 0) -> None:
        self._host = host
        self._port = port
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self._ready_event = threading.Event()

    @property
    def base_url(self) -> str:
        if self._port == 0:
            raise RuntimeError("Server not started")
        return f"http://{self._host}:{self._port}"

    def start(self) -> None:
        self._ready_event.clear()

        def run() -> None:
            self._server = ThreadingHTTPServer((self._host, self._port), _BridgeHandler)
            self._server.allow_reuse_address = True
            self._port = self._server.server_address[1]
            self._ready_event.set()
            _BridgeHandler.log_message = _silent_log  # type: ignore[method-assign]
            self._server.serve_forever()

        self._thread = threading.Thread(target=run, daemon=True)
        self._thread.start()
        self._ready_event.wait()
        logger.debug("Bridge server started at %s", self.base_url)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        logger.debug("Bridge server stopped")


class _BridgeHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if not path.startswith("/coral-api/"):
            self.send_error(404, "Not Found")
            return

        table_name = path[len("/coral-api/") :]
        if not table_name:
            self.send_error(404, "Not Found")
            return

        api_func = CoralApiRegistry.get(table_name)
        if api_func is None:
            self.send_error(404, f"Table '{table_name}' not found")
            return

        query_params = parse_qs(parsed.query)
        kwargs: dict[str, Any] = {k: v[0] if len(v) == 1 else v for k, v in query_params.items()}

        # Only pass query params that match declared filters
        filtered_kwargs = {k: v for k, v in kwargs.items() if k in api_func.filters}

        try:
            result = api_func.func(**filtered_kwargs)
            if result is None:
                result = []
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(result).encode("utf-8"))
        except Exception as exc:
            logger.exception("Error calling %s: %s", table_name, exc)
            self.send_error(500, str(exc))

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug(format, *args)
