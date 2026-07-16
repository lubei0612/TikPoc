import json
import time
from collections.abc import Callable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .db import Database
from .web_accounts import WebAccountRegistry
from .webhooks import (
    WebhookPayloadError,
    WebhookSignatureError,
    parse_business_message_webhook,
    verify_tiktok_signature,
)


class DashboardServer(ThreadingHTTPServer):
    def __init__(
        self,
        address: tuple[str, int],
        database_path: Path,
        *,
        web_account_registry: WebAccountRegistry | None = None,
        tiktok_app_secret: str = "",
        webhook_max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(address, DashboardHandler)
        self.database = Database(database_path)
        self.database.migrate()
        self.web_account_registry = web_account_registry
        self.tiktok_app_secret = tiktok_app_secret
        self.webhook_max_age_seconds = webhook_max_age_seconds
        self.clock = clock


class DashboardHandler(BaseHTTPRequestHandler):
    server: DashboardServer

    _browser_event_types = {
        "new_follower",
        "followback_completed",
        "followback_unresolved",
        "browser_dm_received",
    }
    _cors_origins = {"https://tiktok.com", "https://www.tiktok.com"}

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            payload = self.server.database.dashboard_snapshot()
            payload["latest_event"] = self.server.database.latest_runtime_event()
            self._send_json(payload)
            return
        if parsed.path == "/api/recent":
            raw_limit = parse_qs(parsed.query).get("limit", ["10"])[0]
            try:
                limit = int(raw_limit)
            except ValueError:
                limit = 10
            self._send_json(self.server.database.recent_tasks(limit))
            return
        static_files = {
            "/": ("dashboard.html", "text/html; charset=utf-8"),
            "/dashboard.css": ("dashboard.css", "text/css; charset=utf-8"),
            "/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
        }
        if parsed.path in static_files:
            filename, content_type = static_files[parsed.path]
            self._send_file(filename, content_type)
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        if self.path == "/api/tiktok-business/webhook":
            self._receive_tiktok_business_webhook()
            return
        if self.path == "/api/browser-events":
            self._receive_browser_event()
            return
        if self.path == "/api/device-events":
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length))
                accepted = self.server.database.enqueue_device_event(
                    str(body["device_id"]),
                    str(body["event_type"]),
                    str(body["dedup_key"]),
                    dict(body.get("payload") or {}),
                )
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                self._send_json({"error": "invalid device event"}, HTTPStatus.BAD_REQUEST)
                return
            self._send_json({"accepted": accepted})
            return
        action = self.path.removeprefix("/api/control/")
        states = {"pause": "paused", "resume": "running", "stop": "stopped"}
        if self.path.startswith("/api/control/") and action in states:
            state = states[action]
            self.server.database.set_worker_control(state)
            self._send_json({"control": state})
            return
        self.send_error(HTTPStatus.NOT_FOUND)

    def do_OPTIONS(self) -> None:
        if self.path != "/api/browser-events" or self._allowed_origin() is None:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        self.send_response(HTTPStatus.NO_CONTENT)
        self._send_cors_headers()
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _receive_tiktok_business_webhook(self) -> None:
        body = self._read_body()
        if not self.server.tiktok_app_secret:
            self._send_json(
                {"error": "webhook is not configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        try:
            verify_tiktok_signature(
                body,
                self.headers.get("TikTok-Signature", ""),
                self.server.tiktok_app_secret,
                now=int(self.server.clock()),
                max_age_seconds=self.server.webhook_max_age_seconds,
            )
        except WebhookSignatureError:
            self._send_json({"error": "invalid signature"}, HTTPStatus.UNAUTHORIZED)
            return

        try:
            event = parse_business_message_webhook(body)
        except WebhookPayloadError:
            self._send_json({"accepted": False, "ignored": "invalid payload"})
            return
        if event is None:
            self._send_json({"accepted": False, "ignored": "unsupported event"})
            return

        registry = self.server.web_account_registry
        if registry is None:
            self._send_json(
                {"error": "web account registry is not configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        try:
            account = registry.by_business_id(event.business_id)
        except KeyError:
            self.server.database.record_runtime_event("unknown_business_account")
            self._send_json(
                {"accepted": False, "ignored": "unknown business account"},
                HTTPStatus.ACCEPTED,
            )
            return
        if not account.enabled:
            self._send_json({"accepted": False, "ignored": "account disabled"})
            return

        payload: dict[str, object] = {
            "business_id": event.business_id,
            "conversation_id": event.conversation_id,
            "message_id": event.message_id,
            "sender_id": event.sender_id,
            "sender_username": event.sender_username,
            "text": event.text,
            "message_type": event.message_type,
            "timestamp_ms": event.timestamp_ms,
            "source": event.source,
        }
        if event.is_follower is not None:
            payload["is_follower"] = event.is_follower
        accepted = self.server.database.enqueue_web_event(
            account.account_id,
            "dm_received",
            event.message_id,
            payload,
        )
        self._send_json({"accepted": accepted})

    def _receive_browser_event(self) -> None:
        try:
            body = json.loads(self._read_body())
            if not isinstance(body, dict):
                raise TypeError
            account_id = self._required_text(body, "account_id")
            device_id = self._required_text(body, "device_id")
            event_type = self._required_text(body, "event_type")
            dedup_key = self._required_text(body, "dedup_key")
            payload = body.get("payload") or {}
            if not isinstance(payload, dict):
                raise TypeError
            if event_type not in self._browser_event_types:
                raise ValueError

            registry = self.server.web_account_registry
            if registry is None:
                raise ValueError
            account = registry.by_account_id(account_id)
            if not account.enabled or account.device_id != device_id:
                raise ValueError
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_json({"error": "invalid browser event"}, HTTPStatus.BAD_REQUEST)
            return

        sanitized_payload = dict(payload)
        sanitized_payload["device_id"] = device_id
        accepted = self.server.database.enqueue_web_event(
            account_id,
            event_type,
            dedup_key,
            sanitized_payload,
        )
        self._send_json({"accepted": accepted})

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        return self.rfile.read(max(0, length))

    @staticmethod
    def _required_text(body: dict[str, object], key: str) -> str:
        value = str(body[key]).strip()
        if not value:
            raise ValueError(key)
        return value

    def _send_json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_cors_headers()
        self.end_headers()
        self.wfile.write(body)

    def _allowed_origin(self) -> str | None:
        origin = self.headers.get("Origin")
        return origin if origin in self._cors_origins else None

    def _send_cors_headers(self) -> None:
        origin = self._allowed_origin()
        if origin is not None:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _send_file(self, filename: str, content_type: str) -> None:
        body = (Path(__file__).parent / "static" / filename).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def create_server(
    database_path: Path,
    host: str,
    port: int,
    *,
    web_account_registry: WebAccountRegistry | None = None,
    tiktok_app_secret: str = "",
    webhook_max_age_seconds: int = 300,
    clock: Callable[[], float] = time.time,
) -> DashboardServer:
    return DashboardServer(
        (host, port),
        database_path,
        web_account_registry=web_account_registry,
        tiktok_app_secret=tiktok_app_secret,
        webhook_max_age_seconds=webhook_max_age_seconds,
        clock=clock,
    )
