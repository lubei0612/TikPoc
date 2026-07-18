import hashlib
import hmac
import json
import threading
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tikpoc.browser_dm import BrowserDmService, BrowserInbound, BrowserReply
from tikpoc.dashboard import create_server
from tikpoc.db import BrowserConversationBusy, BrowserReplyPlan, Database
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


class FakeBrowserDmService:
    def __init__(self) -> None:
        self.inbounds: list[BrowserInbound] = []
        self.results: list[tuple[int, str]] = []

    def plan(self, inbound: BrowserInbound) -> BrowserReply:
        self.inbounds.append(inbound)
        return BrowserReply(
            plan_id=17,
            conversation_id=inbound.conversation_id,
            inbound_fingerprint=inbound.fingerprint,
            reply_text="Thanks. WhatsApp: +1 555 0100",
            stage="invited",
        )

    def record_result(
        self,
        account_id: str,
        device_id: str,
        plan_id: int,
        state: str,
    ) -> bool:
        self.results.append((plan_id, state))
        return True


def _post_json(base_url: str, path: str, body: dict[str, object]):
    return urlopen(
        Request(
            base_url + path,
            data=json.dumps(body).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": "https://www.tiktok.com",
            },
            method="POST",
        )
    )


def _browser_inbound_body() -> dict[str, object]:
    return {
        "account_id": "account-01",
        "device_id": "phone-01",
        "conversation_id": "conversation-01",
        "fingerprint": "fp-01",
        "participant_username": "prospect",
        "text": "hello",
        "timestamp_ms": 1_720_000_000_000,
    }


def test_browser_dm_plan_and_result_endpoints(tmp_path: Path) -> None:
    service = FakeBrowserDmService()
    server, base_url = _start_server(
        tmp_path / "db.sqlite",
        web_account_registry=_registry(tmp_path),
        browser_dm_service=service,
    )
    try:
        plan_response = _post_json(
            base_url, "/api/browser-dm/reply-plan", _browser_inbound_body()
        )
        planned = json.load(plan_response)
        recorded = json.load(
            _post_json(
                base_url,
                "/api/browser-dm/reply-result",
                {
                    "account_id": "account-01",
                    "device_id": "phone-01",
                    "plan_id": planned["plan_id"],
                    "state": "sent",
                },
            )
        )

        assert planned == {
            "plan_id": 17,
            "conversation_id": "conversation-01",
            "inbound_fingerprint": "fp-01",
            "reply_text": "Thanks. WhatsApp: +1 555 0100",
            "stage": "invited",
        }
        assert plan_response.headers["Access-Control-Allow-Origin"] == (
            "https://www.tiktok.com"
        )
        assert recorded == {"recorded": True}
        assert service.results == [(planned["plan_id"], "sent")]
    finally:
        server.shutdown()


def test_browser_action_and_health_endpoints(tmp_path: Path) -> None:
    database_path = tmp_path / "db.sqlite"
    server, base_url = _start_server(
        database_path,
        web_account_registry=_registry(tmp_path),
        browser_dm_service=FakeBrowserDmService(),
    )
    try:
        identity = {"account_id": "account-01", "device_id": "phone-01"}
        claimed = json.load(
            _post_json(
                base_url,
                "/api/browser-actions/claim",
                {
                    **identity,
                    "action_type": "dm_send",
                    "action_key": "plan-17",
                    "owner_id": "tab-a",
                    "timestamp_ms": 1_000,
                    "lease_seconds": 30,
                },
            )
        )
        duplicate = json.load(
            _post_json(
                base_url,
                "/api/browser-actions/claim",
                {
                    **identity,
                    "action_type": "dm_send",
                    "action_key": "plan-17",
                    "owner_id": "tab-b",
                    "timestamp_ms": 2_000,
                },
            )
        )
        result = json.load(
            _post_json(
                base_url,
                "/api/browser-actions/result",
                {
                    **identity,
                    "action_type": "dm_send",
                    "action_key": "plan-17",
                    "owner_id": "tab-a",
                    "state": "completed",
                },
            )
        )
        health = json.load(
            _post_json(
                base_url,
                "/api/browser-health",
                {
                    **identity,
                    "page_role": "messages",
                    "path": "/messages",
                    "signed_in": True,
                    "timestamp_ms": 3_000,
                },
            )
        )

        assert claimed == {"claimed": True}
        assert duplicate == {"claimed": False}
        assert result == {"recorded": True}
        assert health == {"recorded": True}
        assert Database(database_path).latest_runtime_event()["event_type"] == (
            "browser_health_messages"
        )
    finally:
        server.shutdown()


def test_browser_dm_service_is_built_from_registry(tmp_path: Path) -> None:
    server = create_server(
        tmp_path / "db.sqlite",
        "127.0.0.1",
        0,
        web_account_registry=_registry(tmp_path),
    )
    try:
        assert isinstance(server.browser_dm_service, BrowserDmService)
        assert server.browser_dm_service.database is server.database
    finally:
        server.server_close()


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/browser-dm/reply-plan", _browser_inbound_body()),
        (
            "/api/browser-dm/reply-result",
            {
                "account_id": "account-01",
                "device_id": "phone-01",
                "plan_id": 17,
                "state": "sent",
            },
        ),
        (
            "/api/browser-actions/claim",
            {
                "account_id": "account-01",
                "device_id": "phone-01",
                "action_type": "dm_send",
                "action_key": "plan-17",
                "owner_id": "tab-a",
                "timestamp_ms": 1_000,
            },
        ),
        (
            "/api/browser-actions/result",
            {
                "account_id": "account-01",
                "device_id": "phone-01",
                "action_type": "dm_send",
                "action_key": "plan-17",
                "owner_id": "tab-a",
                "state": "completed",
            },
        ),
        (
            "/api/browser-health",
            {
                "account_id": "account-01",
                "device_id": "phone-01",
                "page_role": "messages",
                "path": "/messages",
                "signed_in": True,
                "timestamp_ms": 1_000,
            },
        ),
    ],
)
def test_browser_endpoints_reject_mismatched_account_device(
    tmp_path: Path, path: str, body: dict[str, object]
) -> None:
    server, base_url = _start_server(
        tmp_path / "db.sqlite",
        web_account_registry=_registry(tmp_path),
        browser_dm_service=FakeBrowserDmService(),
    )
    body["device_id"] = "wrong-phone"
    try:
        with pytest.raises(HTTPError) as raised:
            _post_json(base_url, path, body)
        assert raised.value.code == 400
        assert json.load(raised.value) == {"error": "invalid browser request"}
    finally:
        server.shutdown()


def test_browser_endpoints_require_integer_fields(tmp_path: Path) -> None:
    server, base_url = _start_server(
        tmp_path / "db.sqlite",
        web_account_registry=_registry(tmp_path),
        browser_dm_service=FakeBrowserDmService(),
    )
    try:
        invalid_plan = _browser_inbound_body()
        invalid_plan["timestamp_ms"] = "1720000000000"
        with pytest.raises(HTTPError) as timestamp_error:
            _post_json(base_url, "/api/browser-dm/reply-plan", invalid_plan)
        assert timestamp_error.value.code == 400

        with pytest.raises(HTTPError) as plan_id_error:
            _post_json(
                base_url,
                "/api/browser-dm/reply-result",
                {
                    "account_id": "account-01",
                    "device_id": "phone-01",
                    "plan_id": "17",
                    "state": "sent",
                },
            )
        assert plan_id_error.value.code == 400
    finally:
        server.shutdown()


def test_browser_conversation_busy_returns_conflict_without_message_text(
    tmp_path: Path,
) -> None:
    class BusyService(FakeBrowserDmService):
        def plan(self, inbound: BrowserInbound) -> BrowserReply:
            raise BrowserConversationBusy(
                BrowserReplyPlan(
                    id=23,
                    account_id=inbound.account_id,
                    conversation_id=inbound.conversation_id,
                    inbound_fingerprint="previous-fingerprint",
                    participant_username="prospect",
                    inbound_text="private inbound text",
                    inbound_timestamp_ms=1,
                    reply_text="private reply text",
                    stage="engaged",
                    state="uncertain",
                )
            )

    server, base_url = _start_server(
        tmp_path / "db.sqlite",
        web_account_registry=_registry(tmp_path),
        browser_dm_service=BusyService(),
    )
    try:
        with pytest.raises(HTTPError) as raised:
            _post_json(base_url, "/api/browser-dm/reply-plan", _browser_inbound_body())
        body = raised.value.read().decode()
        assert raised.value.code == 409
        assert json.loads(body) == {"error": "browser conversation busy"}
        assert "private" not in body
    finally:
        server.shutdown()


@pytest.mark.parametrize(
    "path",
    [
        "/api/browser-events",
        "/api/browser-dm/reply-plan",
        "/api/browser-dm/reply-result",
        "/api/browser-actions/claim",
        "/api/browser-actions/result",
        "/api/browser-health",
    ],
)
def test_browser_post_options_returns_cors_headers(tmp_path: Path, path: str) -> None:
    server, base_url = _start_server(tmp_path / "db.sqlite")
    try:
        response = urlopen(
            Request(
                base_url + path,
                headers={"Origin": "https://www.tiktok.com"},
                method="OPTIONS",
            )
        )
        assert response.status == 204
        assert response.headers["Access-Control-Allow-Origin"] == (
            "https://www.tiktok.com"
        )
        assert response.headers["Access-Control-Allow-Methods"] == "POST, OPTIONS"
    finally:
        server.shutdown()


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
