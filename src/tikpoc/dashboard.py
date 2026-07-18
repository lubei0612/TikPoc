import json
import os
import re
import time
from collections.abc import Callable, Iterable
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .browser_dm import (
    BrowserConversationBusy,
    BrowserDmService,
    BrowserInbound,
)
from .db import Database
from .messaging import AiReplyClient
from .web_accounts import WebAccount, WebAccountRegistry
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
        browser_dm_service: BrowserDmService | None = None,
        browser_extension_origins: Iterable[str] | None = None,
        tiktok_app_secret: str = "",
        webhook_max_age_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        super().__init__(address, DashboardHandler)
        self.database = Database(database_path)
        self.database.migrate()
        self.web_account_registry = web_account_registry
        if browser_dm_service is None and web_account_registry is not None:
            browser_dm_service = BrowserDmService(
                self.database,
                web_account_registry,
                AiReplyClient.from_environment(),
            )
        self.browser_dm_service = browser_dm_service
        if browser_extension_origins is None:
            browser_extension_origins = os.getenv(
                "TIKPOC_BROWSER_EXTENSION_ORIGINS", ""
            ).split(",")
        self.browser_origins = {
            "https://tiktok.com",
            "https://www.tiktok.com",
            *(
                origin.strip()
                for origin in browser_extension_origins
                if re.fullmatch(r"chrome-extension://[a-p]{32}", origin.strip())
            ),
        }
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
    _browser_post_paths = {
        "/api/browser-events",
        "/api/browser-dm/reply-plan",
        "/api/browser-dm/reply-result",
        "/api/browser-actions/claim",
        "/api/browser-actions/result",
        "/api/browser-health",
    }

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
        if self.path in self._browser_post_paths:
            if self._allowed_origin() is None:
                self._send_json(
                    {"error": "browser origin is not allowed"},
                    HTTPStatus.FORBIDDEN,
                )
                return
            media_type = self.headers.get("Content-Type", "").split(";", 1)[0]
            if media_type.strip().lower() != "application/json":
                self._send_json(
                    {"error": "browser request must use application/json"},
                    HTTPStatus.UNSUPPORTED_MEDIA_TYPE,
                )
                return
        if self.path == "/api/tiktok-business/webhook":
            self._receive_tiktok_business_webhook()
            return
        if self.path == "/api/browser-events":
            self._receive_browser_event()
            return
        browser_handlers = {
            "/api/browser-dm/reply-plan": self._plan_browser_reply,
            "/api/browser-dm/reply-result": self._record_browser_reply_result,
            "/api/browser-actions/claim": self._claim_browser_action,
            "/api/browser-actions/result": self._record_browser_action_result,
            "/api/browser-health": self._record_browser_health,
        }
        browser_handler = browser_handlers.get(self.path)
        if browser_handler is not None:
            browser_handler()
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
                self._send_json(
                    {"error": "invalid device event"}, HTTPStatus.BAD_REQUEST
                )
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
        if self.path not in self._browser_post_paths or self._allowed_origin() is None:
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

            self._browser_account(account_id, device_id)
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

    def _plan_browser_reply(self) -> None:
        service = self.server.browser_dm_service
        if service is None:
            self._send_json(
                {"error": "browser DM service is not configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        try:
            body = self._read_json_object()
            account_id, device_id = self._browser_identity(body)
            reply = service.plan(
                BrowserInbound(
                    account_id=account_id,
                    device_id=device_id,
                    conversation_id=self._required_text(body, "conversation_id"),
                    fingerprint=self._required_text(body, "fingerprint"),
                    participant_username=self._required_text(
                        body, "participant_username"
                    ),
                    text=self._required_text(body, "text"),
                    timestamp_ms=self._required_integer(
                        body, "timestamp_ms", minimum=0
                    ),
                )
            )
        except BrowserConversationBusy:
            self._send_json({"error": "browser conversation busy"}, HTTPStatus.CONFLICT)
            return
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_invalid_browser_request()
            return
        self._send_json(
            {
                "plan_id": reply.plan_id,
                "conversation_id": reply.conversation_id,
                "inbound_fingerprint": reply.inbound_fingerprint,
                "reply_text": reply.reply_text,
                "stage": reply.stage,
            }
        )

    def _record_browser_reply_result(self) -> None:
        service = self.server.browser_dm_service
        if service is None:
            self._send_json(
                {"error": "browser DM service is not configured"},
                HTTPStatus.SERVICE_UNAVAILABLE,
            )
            return
        try:
            body = self._read_json_object()
            account_id, device_id = self._browser_identity(body)
            recorded = service.record_result(
                account_id,
                device_id,
                self._required_integer(body, "plan_id", minimum=1),
                self._required_text(body, "state"),
            )
        except KeyError:
            self._send_json(
                {"error": "browser reply plan not found"}, HTTPStatus.NOT_FOUND
            )
            return
        except (TypeError, ValueError, json.JSONDecodeError):
            self._send_invalid_browser_request()
            return
        self._send_json({"recorded": recorded})

    def _claim_browser_action(self) -> None:
        try:
            body = self._read_json_object()
            account_id, _ = self._browser_identity(body)
            lease_seconds = self._optional_integer(
                body, "lease_seconds", default=30, minimum=1
            )
            claimed = self.server.database.claim_browser_action(
                account_id,
                self._required_text(body, "action_type"),
                self._required_text(body, "action_key"),
                self._required_text(body, "owner_id"),
                self._required_integer(body, "timestamp_ms", minimum=0),
                lease_seconds,
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_invalid_browser_request()
            return
        self._send_json({"claimed": claimed})

    def _record_browser_action_result(self) -> None:
        try:
            body = self._read_json_object()
            account_id, _ = self._browser_identity(body)
            recorded = self.server.database.finish_browser_action(
                account_id,
                self._required_text(body, "action_type"),
                self._required_text(body, "action_key"),
                self._required_text(body, "owner_id"),
                self._required_text(body, "state"),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_invalid_browser_request()
            return
        self._send_json({"recorded": recorded})

    def _record_browser_health(self) -> None:
        try:
            body = self._read_json_object()
            account_id, _ = self._browser_identity(body)
            page_role = self._required_text(body, "page_role")
            if page_role not in {"activity", "messages"}:
                raise ValueError("invalid browser page role")
            self._required_text(body, "path")
            if type(body.get("signed_in")) is not bool:
                raise TypeError("signed_in")
            self._required_integer(body, "timestamp_ms", minimum=0)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            self._send_invalid_browser_request()
            return
        self.server.database.record_runtime_event(
            f"browser_health_{page_role}", account_id
        )
        self._send_json({"recorded": True})

    def _read_json_object(self) -> dict[str, object]:
        body = json.loads(self._read_body())
        if not isinstance(body, dict):
            raise TypeError("JSON body must be an object")
        return body

    def _browser_identity(self, body: dict[str, object]) -> tuple[str, str]:
        account_id = self._required_text(body, "account_id")
        device_id = self._required_text(body, "device_id")
        self._browser_account(account_id, device_id)
        return account_id, device_id

    def _browser_account(self, account_id: str, device_id: str) -> WebAccount:
        registry = self.server.web_account_registry
        if registry is None:
            raise ValueError("web account registry is not configured")
        account = registry.by_account_id(account_id)
        if not account.enabled or account.device_id != device_id:
            raise ValueError("browser account and device mapping do not match")
        return account

    def _send_invalid_browser_request(self) -> None:
        self._send_json({"error": "invalid browser request"}, HTTPStatus.BAD_REQUEST)

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

    @staticmethod
    def _required_integer(
        body: dict[str, object], key: str, *, minimum: int | None = None
    ) -> int:
        value = body[key]
        if type(value) is not int:
            raise TypeError(key)
        if minimum is not None and value < minimum:
            raise ValueError(key)
        return value

    @classmethod
    def _optional_integer(
        cls,
        body: dict[str, object],
        key: str,
        *,
        default: int,
        minimum: int | None = None,
    ) -> int:
        if key not in body:
            return default
        return cls._required_integer(body, key, minimum=minimum)

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
        if origin in self.server.browser_origins:
            return origin
        if origin and re.fullmatch(r"chrome-extension://[a-p]{32}", origin):
            return origin
        return None

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
    browser_dm_service: BrowserDmService | None = None,
    browser_extension_origins: Iterable[str] | None = None,
    tiktok_app_secret: str = "",
    webhook_max_age_seconds: int = 300,
    clock: Callable[[], float] = time.time,
) -> DashboardServer:
    return DashboardServer(
        (host, port),
        database_path,
        web_account_registry=web_account_registry,
        browser_dm_service=browser_dm_service,
        browser_extension_origins=browser_extension_origins,
        tiktok_app_secret=tiktok_app_secret,
        webhook_max_age_seconds=webhook_max_age_seconds,
        clock=clock,
    )
