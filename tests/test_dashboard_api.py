import hashlib
import hmac
import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from fastapi.testclient import TestClient

from tikpoc.api import create_app
from tikpoc.browser_dm import BrowserDmService, BrowserInbound, BrowserReply
from tikpoc.browser_welcome import BrowserWelcomeService
from tikpoc.dashboard import create_server
from tikpoc.db import (
    BrowserConversationBusy,
    BrowserReplyPlan,
    BrowserWelcomePlan,
    Database,
)
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
                expected_tiktok_username="shop_one",
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


class FakeBrowserWelcomeService:
    def __init__(self) -> None:
        self.followbacks: list[tuple[str, str, str]] = []
        self.results: list[tuple[str, str, int, str]] = []

    def plan_after_followback(
        self, account_id: str, device_id: str, follower_key: str
    ) -> BrowserWelcomePlan:
        self.followbacks.append((account_id, device_id, follower_key))
        return self.next_plan(account_id, device_id)

    def next_plan(self, account_id: str, device_id: str) -> BrowserWelcomePlan:
        return BrowserWelcomePlan(
            id=23,
            account_id=account_id,
            follower_username="prospect",
            follower_key="follower:key",
            reply_text="Synthetic welcome",
            state="planned",
            created_at_ms=1_000,
            updated_at_ms=1_000,
        )

    def record_result(
        self,
        account_id: str,
        device_id: str,
        plan_id: int,
        state: str,
    ) -> bool:
        self.results.append((account_id, device_id, plan_id, state))
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


def _post_browser_request(
    base_url: str,
    path: str,
    body: dict[str, object],
    *,
    origin: str | None,
    content_type: str = "application/json",
):
    headers = {"Content-Type": content_type}
    if origin is not None:
        headers["Origin"] = origin
    return urlopen(
        Request(
            base_url + path,
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
    )


def _browser_inbound_body() -> dict[str, object]:
    return {
        **_browser_identity(),
        "conversation_id": "conversation-01",
        "fingerprint": "fp-01",
        "participant_username": "prospect",
        "text": "hello",
        "timestamp_ms": 1_720_000_000_000,
    }


def _browser_identity() -> dict[str, object]:
    return {
        "account_id": "account-01",
        "device_id": "phone-01",
        "observed_username": "@SHOP_ONE",
        "binding_state": "ready",
    }


def _browser_post_bodies() -> dict[str, dict[str, object]]:
    identity = _browser_identity()
    return {
        "/api/browser-events": {
            **identity,
            "event_type": "followback_completed",
            "dedup_key": "prospect:/@prospect",
            "payload": {"username": "prospect"},
        },
        "/api/browser-dm/reply-plan": _browser_inbound_body(),
        "/api/browser-dm/reply-result": {
            **identity,
            "plan_id": 17,
            "state": "sent",
        },
        "/api/browser-dm/welcome-plan": identity,
        "/api/browser-dm/welcome-result": {
            **identity,
            "plan_id": 23,
            "state": "sent",
        },
        "/api/browser-actions/claim": {
            **identity,
            "action_type": "dm_send",
            "action_key": "dm_send:17",
            "owner_id": "tab-a",
            "timestamp_ms": 1_000,
        },
        "/api/browser-actions/result": {
            **identity,
            "action_type": "dm_send",
            "action_key": "dm_send:17",
            "owner_id": "tab-a",
            "state": "completed",
        },
        "/api/browser-health": {
            **identity,
            "page_role": "messages",
            "path": "/messages",
            "signed_in": True,
            "timestamp_ms": 1_000,
        },
    }


@pytest.mark.parametrize("path", tuple(_browser_post_bodies()))
@pytest.mark.parametrize(
    ("identity_update", "error_code"),
    [
        (
            {"observed_username": "", "binding_state": "unverified"},
            "binding_unverified",
        ),
        (
            {"observed_username": "shop_two", "binding_state": "mismatch"},
            "binding_mismatch",
        ),
    ],
)
def test_browser_endpoints_reject_unverified_visible_identity(
    tmp_path: Path,
    path: str,
    identity_update: dict[str, object],
    error_code: str,
) -> None:
    client = TestClient(
        create_app(tmp_path / "binding.db", registry=_registry(tmp_path))
    )
    body = _browser_post_bodies()[path]
    body.update(identity_update)

    response = client.post(
        path,
        json=body,
        headers={"Origin": "https://www.tiktok.com"},
    )

    assert response.status_code == 409
    assert response.json() == {"error": error_code}
    assert Database(tmp_path / "binding.db").claim_web_event("account-01") is None


def test_browser_health_persists_visible_identity_before_binding_conflict(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "health-binding.db"
    client = TestClient(create_app(database_path, registry=_registry(tmp_path)))
    body = _browser_post_bodies()["/api/browser-health"]
    body.update(
        observed_username="shop_two",
        binding_state="ready",
        timestamp_ms=4_000,
        last_scan_at_ms=3_900,
        last_success_at_ms=3_800,
        scan_state="idle",
    )

    response = client.post(
        "/api/browser-health",
        json=body,
        headers={"Origin": "https://www.tiktok.com"},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "binding_mismatch"}
    assert Database(database_path).browser_health_snapshot() == [
        {
            "account_id": "account-01",
            "page_role": "messages",
            "device_id": "phone-01",
            "status": "mismatch",
            "observed_at_ms": 4_000,
            "detail": "/messages",
            "observed_username": "shop_two",
            "last_scan_at_ms": 3_900,
            "last_success_at_ms": 3_800,
            "scan_state": "idle",
        }
    ]


def test_browser_account_without_expected_username_reports_unverified_health(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "unverified-binding.db"
    registry = WebAccountRegistry(
        (WebAccount(account_id="account-01", device_id="phone-01"),)
    )
    client = TestClient(create_app(database_path, registry=registry))
    body = _browser_post_bodies()["/api/browser-health"]
    body.update(observed_username="", binding_state="unverified", timestamp_ms=5_000)

    response = client.post(
        "/api/browser-health",
        json=body,
        headers={"Origin": "https://www.tiktok.com"},
    )

    assert response.status_code == 409
    assert response.json() == {"error": "binding_unverified"}
    assert Database(database_path).browser_health_snapshot()[0] == {
        "account_id": "account-01",
        "page_role": "messages",
        "device_id": "phone-01",
        "status": "unverified",
        "observed_at_ms": 5_000,
        "detail": "/messages",
        "observed_username": "",
        "last_scan_at_ms": 0,
        "last_success_at_ms": 0,
        "scan_state": "not_started",
    }
    claim_body = _browser_post_bodies()["/api/browser-actions/claim"]
    claim_body.update(observed_username="", binding_state="unverified")
    claim = client.post(
        "/api/browser-actions/claim",
        json=claim_body,
        headers={"Origin": "https://www.tiktok.com"},
    )
    assert claim.status_code == 409
    assert claim.json() == {"error": "binding_unverified"}


def test_browser_post_routes_validate_origin_and_json_before_side_effects(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "db.sqlite"
    service = FakeBrowserDmService()
    extension_origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    server, base_url = _start_server(
        database_path,
        web_account_registry=_registry(tmp_path),
        browser_dm_service=service,
        browser_extension_origins=(extension_origin,),
    )
    try:
        for path, body in _browser_post_bodies().items():
            with pytest.raises(HTTPError) as evil_origin:
                _post_browser_request(
                    base_url,
                    path,
                    body,
                    origin="https://evil.example",
                )
            assert evil_origin.value.code == 403
            assert json.load(evil_origin.value) == {
                "error": "browser origin is not allowed"
            }

            with pytest.raises(HTTPError) as missing_origin:
                _post_browser_request(base_url, path, body, origin=None)
            assert missing_origin.value.code == 403
            assert json.load(missing_origin.value) == {
                "error": "browser origin is not allowed"
            }

            with pytest.raises(HTTPError) as wrong_media_type:
                _post_browser_request(
                    base_url,
                    path,
                    body,
                    origin="https://www.tiktok.com",
                    content_type="text/plain",
                )
            assert wrong_media_type.value.code == 415
            assert json.load(wrong_media_type.value) == {
                "error": "browser request must use application/json"
            }

        database = Database(database_path)
        assert service.inbounds == []
        assert service.results == []
        assert database.claim_web_event("account-01") is None
        assert database.latest_runtime_event() is None
        assert json.load(
            _post_browser_request(
                base_url,
                "/api/browser-actions/claim",
                _browser_post_bodies()["/api/browser-actions/claim"],
                origin=extension_origin,
                content_type="application/json; charset=utf-8",
            )
        ) == {"claimed": True}
    finally:
        server.shutdown()


def test_browser_post_routes_accept_verified_chrome_extension_origin(
    tmp_path: Path,
) -> None:
    extension_origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    service = FakeBrowserDmService()
    server, base_url = _start_server(
        tmp_path / "db.sqlite",
        web_account_registry=_registry(tmp_path),
        browser_dm_service=service,
        browser_extension_origins=(extension_origin,),
    )
    try:
        event_response = _post_browser_request(
            base_url,
            "/api/browser-events",
            _browser_post_bodies()["/api/browser-events"],
            origin=extension_origin,
        )
        plan_response = _post_browser_request(
            base_url,
            "/api/browser-dm/reply-plan",
            _browser_inbound_body(),
            origin=extension_origin,
        )

        assert json.load(event_response) == {"accepted": True}
        assert json.load(plan_response)["plan_id"] == 17
        assert event_response.headers["Access-Control-Allow-Origin"] == (
            extension_origin
        )
        assert service.inbounds[0].fingerprint == "fp-01"
    finally:
        server.shutdown()


def test_browser_post_routes_reject_unconfigured_valid_extension_origin(
    tmp_path: Path,
) -> None:
    configured_origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    unconfigured_origin = "chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
    service = FakeBrowserDmService()
    database_path = tmp_path / "db.sqlite"
    server, base_url = _start_server(
        database_path,
        web_account_registry=_registry(tmp_path),
        browser_dm_service=service,
        browser_extension_origins=(configured_origin,),
    )
    try:
        with pytest.raises(HTTPError) as raised:
            _post_browser_request(
                base_url,
                "/api/browser-dm/reply-plan",
                _browser_inbound_body(),
                origin=unconfigured_origin,
            )

        assert raised.value.code == 403
        assert json.load(raised.value) == {"error": "browser origin is not allowed"}
        assert service.inbounds == []
        assert Database(database_path).latest_runtime_event() is None
    finally:
        server.shutdown()


@pytest.mark.parametrize(
    "origin",
    [
        "chrome-extension://abcdefghijklmnoabcdefghijklmnoa",
        "chrome-extension://abcdefghijklmnopabcdefghijklmnoq",
        "chrome-extension://abcdefghijklmnopabcdefghijklmnop/path",
    ],
)
def test_browser_post_routes_reject_malformed_chrome_extension_origins(
    tmp_path: Path, origin: str
) -> None:
    service = FakeBrowserDmService()
    database_path = tmp_path / "db.sqlite"
    server, base_url = _start_server(
        database_path,
        web_account_registry=_registry(tmp_path),
        browser_dm_service=service,
        browser_extension_origins=(origin,),
    )
    try:
        with pytest.raises(HTTPError) as raised:
            _post_browser_request(
                base_url,
                "/api/browser-dm/reply-plan",
                _browser_inbound_body(),
                origin=origin,
            )
        assert raised.value.code == 403
        assert json.load(raised.value) == {"error": "browser origin is not allowed"}
        assert service.inbounds == []
        assert Database(database_path).latest_runtime_event() is None
    finally:
        server.shutdown()


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
        identity = _browser_identity()
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


def test_two_browser_accounts_isolate_equal_events_actions_and_health(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "two-accounts.db"
    registry = WebAccountRegistry(
        (
            WebAccount(
                account_id="account-01",
                device_id="phone-01",
                expected_tiktok_username="shop_one",
            ),
            WebAccount(
                account_id="account-02",
                device_id="phone-02",
                expected_tiktok_username="shop_two",
            ),
        )
    )
    client = TestClient(create_app(database_path, registry=registry))
    identities = (
        {
            "account_id": "account-01",
            "device_id": "phone-01",
            "observed_username": "shop_one",
            "binding_state": "ready",
        },
        {
            "account_id": "account-02",
            "device_id": "phone-02",
            "observed_username": "shop_two",
            "binding_state": "ready",
        },
    )

    for identity in identities:
        assert client.post(
            "/api/browser-events",
            json={
                **identity,
                "event_type": "new_follower",
                "dedup_key": "follower:same-user:same-event",
                "payload": {"username": "same.user"},
            },
            headers={"Origin": "https://www.tiktok.com"},
        ).json() == {"accepted": True}
        assert client.post(
            "/api/browser-actions/claim",
            json={
                **identity,
                "action_type": "followback",
                "action_key": "followback:same-user:same-event",
                "owner_id": f"tab-{identity['account_id']}",
                "timestamp_ms": 1_000,
                "lease_seconds": 30,
            },
            headers={"Origin": "https://www.tiktok.com"},
        ).json() == {"claimed": True}
        assert client.post(
            "/api/browser-actions/result",
            json={
                **identity,
                "action_type": "followback",
                "action_key": "followback:same-user:same-event",
                "owner_id": f"tab-{identity['account_id']}",
                "state": "completed",
            },
            headers={"Origin": "https://www.tiktok.com"},
        ).json() == {"recorded": True}
        assert client.post(
            "/api/browser-health",
            json={
                **identity,
                "page_role": "activity",
                "path": "/activity",
                "signed_in": True,
                "timestamp_ms": 2_000,
            },
            headers={"Origin": "https://www.tiktok.com"},
        ).json() == {"recorded": True}

    database = Database(database_path)
    first_event = database.claim_web_event("account-01")
    second_event = database.claim_web_event("account-02")
    assert (
        first_event is not None
        and first_event.dedup_key == "follower:same-user:same-event"
    )
    assert (
        second_event is not None
        and second_event.dedup_key == "follower:same-user:same-event"
    )
    with sqlite3.connect(database_path) as connection:
        lease_rows = connection.execute(
            """
            SELECT account_id, action_key, state
            FROM browser_action_leases ORDER BY account_id
            """
        ).fetchall()
    assert lease_rows == [
        ("account-01", "followback:same-user:same-event", "completed"),
        ("account-02", "followback:same-user:same-event", "completed"),
    ]
    assert database.browser_health_snapshot() == [
        {
            "account_id": "account-01",
            "page_role": "activity",
            "device_id": "phone-01",
            "status": "ready",
            "observed_at_ms": 2_000,
            "detail": "/activity",
            "observed_username": "shop_one",
            "last_scan_at_ms": 0,
            "last_success_at_ms": 0,
            "scan_state": "not_started",
        },
        {
            "account_id": "account-02",
            "page_role": "activity",
            "device_id": "phone-02",
            "status": "ready",
            "observed_at_ms": 2_000,
            "detail": "/activity",
            "observed_username": "shop_two",
            "last_scan_at_ms": 0,
            "last_success_at_ms": 0,
            "scan_state": "not_started",
        },
    ]


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


def test_browser_welcome_service_is_built_from_registry(tmp_path: Path) -> None:
    client = TestClient(
        create_app(tmp_path / "welcome.db", registry=_registry(tmp_path))
    )

    assert isinstance(client.app.state.browser_welcome_service, BrowserWelcomeService)


def test_suppressed_follower_cannot_claim_followback_action(tmp_path: Path) -> None:
    database_path = tmp_path / "suppressed-followback.db"
    database = Database(database_path)
    database.migrate()
    assert database.enqueue_web_event(
        "account-01",
        "new_follower",
        "follower:suppressed",
        {"username": "buyer.one"},
    )
    assert database.suppress_browser_contact(
        "account-01",
        "buyer.one",
        reason="explicit_opt_out",
        now_ms=1_000,
    )
    client = TestClient(create_app(database_path, registry=_registry(tmp_path)))

    response = client.post(
        "/api/browser-actions/claim",
        headers={"Origin": "https://www.tiktok.com"},
        json={
            **_browser_identity(),
            "action_type": "followback",
            "action_key": "follower:suppressed",
            "owner_id": "activity-tab",
            "timestamp_ms": 2_000,
        },
    )

    assert response.status_code == 200
    assert response.json() == {"claimed": False}


def test_completed_followback_triggers_welcome_and_messages_api_reconciles_it(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "welcome-api.db"
    welcome = FakeBrowserWelcomeService()
    client = TestClient(
        create_app(
            database_path,
            registry=_registry(tmp_path),
            browser_welcome_service=welcome,
        )
    )
    identity = _browser_identity()

    assert client.post(
        "/api/browser-events",
        headers={"Origin": "https://www.tiktok.com"},
        json={
            **identity,
            "event_type": "followback_completed",
            "dedup_key": "follower:key",
            "payload": {"username": "prospect"},
        },
    ).json() == {"accepted": True}
    assert client.post(
        "/api/browser-actions/claim",
        headers={"Origin": "https://www.tiktok.com"},
        json={
            **identity,
            "action_type": "followback",
            "action_key": "follower:key",
            "owner_id": "activity-tab",
            "timestamp_ms": 1_000,
        },
    ).json() == {"claimed": True}
    assert client.post(
        "/api/browser-actions/result",
        headers={"Origin": "https://www.tiktok.com"},
        json={
            **identity,
            "action_type": "followback",
            "action_key": "follower:key",
            "owner_id": "activity-tab",
            "state": "completed",
        },
    ).json() == {"recorded": True}
    assert welcome.followbacks == [("account-01", "phone-01", "follower:key")]

    plan = client.post(
        "/api/browser-dm/welcome-plan",
        headers={"Origin": "https://www.tiktok.com"},
        json=identity,
    )
    result = client.post(
        "/api/browser-dm/welcome-result",
        headers={"Origin": "https://www.tiktok.com"},
        json={**identity, "plan_id": 23, "state": "sent"},
    )

    assert plan.status_code == 200
    assert plan.json() == {
        "plan_id": 23,
        "follower_username": "prospect",
        "reply_text": "Synthetic welcome",
    }
    assert result.json() == {"recorded": True}
    assert welcome.results == [("account-01", "phone-01", 23, "sent")]


def test_welcome_endpoints_apply_visible_account_binding(tmp_path: Path) -> None:
    welcome = FakeBrowserWelcomeService()
    client = TestClient(
        create_app(
            tmp_path / "welcome-binding.db",
            registry=_registry(tmp_path),
            browser_welcome_service=welcome,
        )
    )
    mismatched = {
        **_browser_identity(),
        "observed_username": "other_shop",
        "binding_state": "mismatch",
    }

    response = client.post(
        "/api/browser-dm/welcome-plan",
        headers={"Origin": "https://www.tiktok.com"},
        json=mismatched,
    )

    assert response.status_code == 409
    assert response.json() == {"error": "binding_mismatch"}


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


def _fastapi_client(tmp_path: Path, **kwargs: object) -> TestClient:
    return TestClient(create_app(tmp_path / "fastapi.db", **kwargs))


def test_fastapi_status_recent_control_and_device_event_compatibility(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "fastapi.db"
    client = _fastapi_client(tmp_path)
    database = Database(database_path)
    database.insert_task("batch", "1", "sample")

    status = client.get("/api/status")
    recent = client.get("/api/recent", params={"limit": 10})
    control = client.post("/api/control/pause")
    event_body = {
        "device_id": "phone-01",
        "event_type": "dm_received",
        "dedup_key": "message-1",
        "payload": {"username": "sample", "message": "hello"},
    }
    first_event = client.post("/api/device-events", json=event_body)
    duplicate_event = client.post("/api/device-events", json=event_body)

    assert status.status_code == 200
    assert status.json()["total"] == 1
    assert set(status.json()) >= {"control", "counts", "latest_event"}
    assert recent.status_code == 200
    assert recent.json() == []
    assert control.json() == {"control": "paused"}
    assert database.worker_control() == "paused"
    assert first_event.json() == {"accepted": True}
    assert duplicate_event.json() == {"accepted": False}


def test_fastapi_status_remains_available_while_reply_planner_is_blocked(
    tmp_path: Path,
) -> None:
    class BlockingBrowserDmService(FakeBrowserDmService):
        def __init__(self) -> None:
            super().__init__()
            self.entered = threading.Event()
            self.release = threading.Event()

        def plan(self, inbound: BrowserInbound) -> BrowserReply:
            self.entered.set()
            if not self.release.wait(timeout=5):
                raise AssertionError("blocked planner was not released")
            return super().plan(inbound)

    service = BlockingBrowserDmService()
    app = create_app(
        tmp_path / "fastapi.db",
        registry=_registry(tmp_path),
        browser_dm_service=service,
    )
    headers = {"Origin": "https://www.tiktok.com"}
    status_finished = threading.Event()

    def request_status(client: TestClient):
        response = client.get("/api/status")
        status_finished.set()
        return response

    with TestClient(app) as client, ThreadPoolExecutor(max_workers=2) as executor:
        planned_future = executor.submit(
            client.post,
            "/api/browser-dm/reply-plan",
            json=_browser_inbound_body(),
            headers=headers,
        )
        try:
            assert service.entered.wait(timeout=2)
            status_future = executor.submit(request_status, client)
            status_completed_while_planner_blocked = status_finished.wait(timeout=0.5)
        finally:
            service.release.set()

        planned = planned_future.result(timeout=2)
        status = status_future.result(timeout=2)

    assert status_completed_while_planner_blocked
    assert planned.status_code == 200
    assert status.status_code == 200


def test_fastapi_browser_routes_preserve_responses_and_exact_cors(
    tmp_path: Path,
) -> None:
    extension_origin = "chrome-extension://abcdefghijklmnopabcdefghijklmnop"
    service = FakeBrowserDmService()
    client = _fastapi_client(
        tmp_path,
        registry=_registry(tmp_path),
        browser_dm_service=service,
        browser_extension_origins=(extension_origin,),
    )
    headers = {"Origin": extension_origin}

    preflight = client.options(
        "/api/browser-events",
        headers={
            **headers,
            "Access-Control-Request-Method": "POST",
        },
    )
    event = client.post(
        "/api/browser-events",
        json=_browser_post_bodies()["/api/browser-events"],
        headers=headers,
    )
    planned = client.post(
        "/api/browser-dm/reply-plan", json=_browser_inbound_body(), headers=headers
    )
    database = Database(tmp_path / "fastapi.db")
    database.append_web_message(
        "account-01",
        "conversation-01",
        "fp-01",
        direction="inbound",
        message_type="TEXT",
        text="hello",
        timestamp_ms=1_720_000_000_000,
        participant_username="prospect",
    )
    with sqlite3.connect(database.path) as connection:
        connection.execute(
            """
            INSERT INTO browser_reply_plans(
                id, account_id, conversation_id, inbound_fingerprint,
                participant_username, inbound_text, inbound_timestamp_ms,
                reply_text, stage, state, plan_origin,
                source_inbound_fingerprint
            ) VALUES (17, 'account-01', 'conversation-01', 'fp-01',
                      'prospect', 'hello', 1720000000000,
                      'Thanks. WhatsApp: +1 555 0100', 'invited', 'planned',
                      'ai', 'fp-01')
            """
        )
    result = client.post(
        "/api/browser-dm/reply-result",
        json={
            **_browser_identity(),
            "plan_id": 17,
            "state": "sent",
        },
        headers=headers,
    )
    claim = client.post(
        "/api/browser-actions/claim",
        json=_browser_post_bodies()["/api/browser-actions/claim"],
        headers=headers,
    )
    action_result = client.post(
        "/api/browser-actions/result",
        json=_browser_post_bodies()["/api/browser-actions/result"],
        headers=headers,
    )
    health = client.post(
        "/api/browser-health",
        json=_browser_post_bodies()["/api/browser-health"],
        headers=headers,
    )

    assert preflight.status_code == 204
    assert preflight.headers["access-control-allow-origin"] == extension_origin
    assert preflight.headers["access-control-allow-methods"] == "POST, OPTIONS"
    assert event.json() == {"accepted": True}
    assert planned.json() == {
        "plan_id": 17,
        "conversation_id": "conversation-01",
        "inbound_fingerprint": "fp-01",
        "reply_text": "Thanks. WhatsApp: +1 555 0100",
        "stage": "invited",
    }
    assert result.json() == {"recorded": True}
    assert claim.json() == {"claimed": True}
    assert action_result.json() == {"recorded": True}
    assert health.json() == {"recorded": True}
    assert database.browser_health_snapshot() == [
        {
            "account_id": "account-01",
            "page_role": "messages",
            "device_id": "phone-01",
            "status": "ready",
            "observed_at_ms": 1_000,
            "detail": "/messages",
            "observed_username": "@SHOP_ONE",
            "last_scan_at_ms": 0,
            "last_success_at_ms": 0,
            "scan_state": "not_started",
        }
    ]
    for response in (event, planned, result, claim, action_result, health):
        assert response.headers["access-control-allow-origin"] == extension_origin


def test_fastapi_browser_routes_reject_origin_and_media_type_before_side_effects(
    tmp_path: Path,
) -> None:
    service = FakeBrowserDmService()
    client = _fastapi_client(
        tmp_path,
        registry=_registry(tmp_path),
        browser_dm_service=service,
    )

    wrong_origin = client.post(
        "/api/browser-dm/reply-plan",
        json=_browser_inbound_body(),
        headers={"Origin": "https://evil.example"},
    )
    wrong_media = client.post(
        "/api/browser-dm/reply-plan",
        content=json.dumps(_browser_inbound_body()),
        headers={
            "Origin": "https://www.tiktok.com",
            "Content-Type": "text/plain",
        },
    )

    assert wrong_origin.status_code == 403
    assert wrong_origin.json() == {"error": "browser origin is not allowed"}
    assert "access-control-allow-origin" not in wrong_origin.headers
    assert wrong_media.status_code == 415
    assert wrong_media.json() == {"error": "browser request must use application/json"}
    assert service.inbounds == []


def test_fastapi_tiktok_webhook_verifies_and_deduplicates(tmp_path: Path) -> None:
    client = _fastapi_client(
        tmp_path,
        registry=_registry(tmp_path),
        tiktok_app_secret="app-secret",
        clock=lambda: 1_720_000_010,
    )
    body = _webhook_body()
    headers = {
        "Content-Type": "application/json",
        "TikTok-Signature": _webhook_signature(body, "app-secret", 1_720_000_000),
    }

    first = client.post("/api/tiktok-business/webhook", content=body, headers=headers)
    duplicate = client.post(
        "/api/tiktok-business/webhook", content=body, headers=headers
    )

    assert first.json() == {"accepted": True}
    assert duplicate.json() == {"accepted": False}


def test_browser_bindings_expose_only_nonsecret_profile_mapping(
    tmp_path: Path,
) -> None:
    registry = WebAccountRegistry(
        (
            WebAccount(
                account_id="account-01",
                device_id="phone-01",
                expected_tiktok_username="shop_one",
                browser_profile_label="TikPoc 01",
                private_channel_hint="SYNTHETIC_PRIVATE_DESTINATION",
                offer_context="Synthetic private offer",
                faq_text="Synthetic private FAQ",
            ),
            WebAccount(
                account_id="account-02",
                device_id="phone-02",
                expected_tiktok_username="",
                browser_profile_label="TikPoc 02",
                enabled=False,
            ),
        )
    )
    client = TestClient(create_app(tmp_path / "bindings.db", registry=registry))
    response = client.get(
        "/api/browser-bindings",
        headers={"Origin": "https://www.tiktok.com"},
    )
    assert response.status_code == 200
    assert response.json() == {
        "accounts": [
            {
                "account_id": "account-01",
                "device_id": "phone-01",
                "expected_tiktok_username": "shop_one",
                "browser_profile_label": "TikPoc 01",
                "enabled": True,
                "browser_followback_enabled": True,
                "browser_dm_enabled": True,
                "binding_ready": True,
            },
            {
                "account_id": "account-02",
                "device_id": "phone-02",
                "expected_tiktok_username": "",
                "browser_profile_label": "TikPoc 02",
                "enabled": False,
                "browser_followback_enabled": False,
                "browser_dm_enabled": False,
                "binding_ready": False,
            },
        ]
    }
    serialized = json.dumps(response.json())
    assert "SYNTHETIC_PRIVATE_DESTINATION" not in serialized
    assert "Synthetic private offer" not in serialized
    assert "Synthetic private FAQ" not in serialized
