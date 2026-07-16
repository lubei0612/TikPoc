import hashlib
import hmac
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tikpoc.dashboard import create_server
from tikpoc.db import Database
from tikpoc.web_accounts import WebAccount, WebAccountRegistry


def _start_server(database_path: Path, **kwargs):
    server = create_server(database_path, "127.0.0.1", 0, **kwargs)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{server.server_port}"


def test_status_recent_and_pause_api(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    database = Database(database_path)
    database.migrate()
    database.insert_task("batch", "1", "sample")
    server, base_url = _start_server(database_path)
    try:
        status = json.load(urlopen(f"{base_url}/api/status"))
        recent = json.load(urlopen(f"{base_url}/api/recent?limit=10"))
        response = urlopen(Request(f"{base_url}/api/control/pause", method="POST"))

        assert status["total"] == 1
        assert status["counts"] == {"pending": 1}
        assert recent == []
        assert json.load(response)["control"] == "paused"
        assert database.worker_control() == "paused"
    finally:
        server.shutdown()


def test_unknown_route_returns_404(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    Database(database_path).migrate()
    server, base_url = _start_server(database_path)
    try:
        try:
            urlopen(f"{base_url}/missing")
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("missing route returned success")
    finally:
        server.shutdown()


def test_device_event_endpoint_enqueues_and_deduplicates(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    Database(database_path).migrate()
    server, base_url = _start_server(database_path)
    body = json.dumps(
        {
            "device_id": "phone-01",
            "event_type": "dm_received",
            "dedup_key": "message-1",
            "payload": {"username": "sample", "message": "hello"},
        }
    ).encode()
    try:
        first = json.load(
            urlopen(
                Request(
                    f"{base_url}/api/device-events",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            )
        )
        second = json.load(
            urlopen(
                Request(
                    f"{base_url}/api/device-events",
                    data=body,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            )
        )

        assert first == {"accepted": True}
        assert second == {"accepted": False}
    finally:
        server.shutdown()


def _registry(tmp_path: Path) -> WebAccountRegistry:
    return WebAccountRegistry(
        (
            WebAccount(
                account_id="account-01",
                device_id="phone-01",
                business_id="business-01",
                token_file=tmp_path / "token.json",
            ),
        )
    )


def _webhook_body() -> bytes:
    return json.dumps(
        {
            "event": "im_receive_msg",
            "user_openid": "business-01",
            "content": json.dumps(
                {
                    "from": "prospect",
                    "unique_identifier": "person-01",
                    "conversation_id": "conversation-01",
                    "message_id": "message-01",
                    "timestamp": 1_720_000_000_000,
                    "type": "text",
                    "text": {"body": "hello"},
                    "is_follower": True,
                }
            ),
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _webhook_signature(body: bytes, secret: str, timestamp: int) -> str:
    digest = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},s={digest}"


def test_tiktok_webhook_endpoint_verifies_enqueues_and_deduplicates(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "tasks.db"
    Database(database_path).migrate()
    server, base_url = _start_server(
        database_path,
        web_account_registry=_registry(tmp_path),
        tiktok_app_secret="app-secret",
        clock=lambda: 1_720_000_010,
    )
    body = _webhook_body()

    def request() -> Request:
        return Request(
            f"{base_url}/api/tiktok-business/webhook",
            data=body,
            headers={
                "Content-Type": "application/json",
                "TikTok-Signature": _webhook_signature(
                    body, "app-secret", 1_720_000_000
                ),
            },
            method="POST",
        )

    try:
        first = json.load(urlopen(request()))
        second = json.load(urlopen(request()))

        assert first == {"accepted": True}
        assert second == {"accepted": False}
        event = Database(database_path).claim_web_event("account-01")
        assert event is not None
        assert event.event_type == "dm_received"
        assert event.payload["conversation_id"] == "conversation-01"
        assert event.payload["text"] == "hello"
    finally:
        server.shutdown()


def test_tiktok_webhook_endpoint_rejects_invalid_signature(tmp_path: Path) -> None:
    database_path = tmp_path / "tasks.db"
    Database(database_path).migrate()
    server, base_url = _start_server(
        database_path,
        web_account_registry=_registry(tmp_path),
        tiktok_app_secret="app-secret",
        clock=lambda: 1_720_000_010,
    )
    try:
        request = Request(
            f"{base_url}/api/tiktok-business/webhook",
            data=_webhook_body(),
            headers={"TikTok-Signature": "t=1720000000,s=bad"},
            method="POST",
        )
        try:
            urlopen(request)
        except HTTPError as error:
            assert error.code == 401
        else:
            raise AssertionError("invalid signature returned success")
    finally:
        server.shutdown()


def test_browser_event_endpoint_enqueues_and_returns_cors_header(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "tasks.db"
    Database(database_path).migrate()
    server, base_url = _start_server(
        database_path, web_account_registry=_registry(tmp_path)
    )
    body = json.dumps(
        {
            "account_id": "account-01",
            "device_id": "phone-01",
            "event_type": "followback_completed",
            "dedup_key": "prospect:/@prospect",
            "payload": {
                "username": "prospect",
                "profile_url": "https://www.tiktok.com/@prospect",
            },
        }
    ).encode()
    try:
        response = urlopen(
            Request(
                f"{base_url}/api/browser-events",
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "Origin": "https://www.tiktok.com",
                },
                method="POST",
            )
        )

        assert json.load(response) == {"accepted": True}
        assert response.headers["Access-Control-Allow-Origin"] == (
            "https://www.tiktok.com"
        )
        event = Database(database_path).claim_web_event("account-01")
        assert event is not None
        assert event.event_type == "followback_completed"
        assert event.payload["device_id"] == "phone-01"
    finally:
        server.shutdown()
